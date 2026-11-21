"""Requisition module (Module 1): the queries and save workflows behind the screens.

Routes in blueprints/requisition.py stay thin: they read the form, call one function
here, then commit or roll back.

This module never changes stock. Inventory creates the alert rows; the warehouse
manager reserves stock for an RF in Inventory's RF queue; Delivery moves it.
Cancel is the only place that touches stock, and only through
inv.release_reservation() so the "reserved" figure stays correct.
"""
from . import alert_snooze as snooze
from . import inventory_service as inv
from . import inventory_ops as inv_ops
from .inventory_service import InventoryError

PAGE_SIZE = 50
ALERT_STATUSES = ("active", "completed", "dismissed")
RF_STATUSES = ("draft", "open", "partially_delivered", "closed", "cancelled")


def _b():
    return inv.backend()


def _like(q):
    return "%" + q.strip().lower() + "%"


def _paged(sql_count, sql_rows, page, **params):
    total = _b().one(sql_count, **params)["n"]
    pages = max(1, -(-total // PAGE_SIZE))
    page = min(max(1, page), pages)
    rows = _b().all(sql_rows + " LIMIT %d OFFSET %d" % (PAGE_SIZE, (page - 1) * PAGE_SIZE), **params)
    return {"rows": rows, "total": total, "page": page, "pages": pages}


def _whole(v, label, minimum=0):
    try:
        n = int(str(v).strip())
    except (TypeError, ValueError):
        raise InventoryError("%s must be a whole number." % label)
    if n < minimum:
        raise InventoryError("%s cannot be below %d." % (label, minimum))
    return n


def alert_no(alert_id):
    """A readable reference for an alert. Alerts are not documents, so they have no
    entry in document_sequences; this is just the row id in a stable form."""
    return "ALT-%04d" % alert_id


def get_location(location_id):
    return _b().one("SELECT id, code, name, loc_type FROM locations WHERE id = :l", l=location_id)


def get_shop(shop_id):
    loc = get_location(shop_id)
    if loc is None:
        raise InventoryError("Your account has no location. Ask the owner to set it.")
    if loc["loc_type"] != "shop":
        raise InventoryError("Only a shop can send a requisition to the warehouse.")
    return loc


def list_warehouses(shop_id=None):
    sql = "SELECT id, code, name FROM locations WHERE is_active AND loc_type = 'warehouse'"
    params = {}
    if shop_id is not None:
        sql += " AND id <> :s"
        params["s"] = shop_id
    return _b().all(sql + " ORDER BY code", **params)


# ---------------------------------------------------------------------
# Low-stock alerts (low_stock notifications for the shop manager)
# ---------------------------------------------------------------------

def list_alerts(shop_id, status="", q="", page=1):
    where, p = "", {"s": shop_id}
    if status == "active":
        where += " AND n.status = 'active' AND " + snooze.AWAKE
    elif status == "snoozed":
        where += " AND n.status = 'active' AND " + snooze.ASLEEP
    elif status in ALERT_STATUSES:
        where += " AND n.status = :st"
        p["st"] = status
    if q.strip():
        where += (" AND EXISTS (SELECT 1 FROM notification_items x JOIN items i ON i.id = x.item_id "
                  "WHERE x.notification_id = n.id AND (LOWER(i.item_name) LIKE :q OR LOWER(i.item_code) LIKE :q))")
        p["q"] = _like(q)
    frm = "FROM notifications n WHERE n.notif_type = 'low_stock' AND n.location_id = :s" + where
    cols = ("SELECT n.id, n.status, n.created_at, n.closed_at, n.dismissed_reason, n.snoozed_until, "
            "(SELECT COUNT(*) FROM notification_items x WHERE x.notification_id = n.id) AS n_items, "
            "(SELECT COALESCE(SUM(x.suggested_qty), 0) FROM notification_items x "
            " WHERE x.notification_id = n.id) AS total_suggested ")
    res = _paged("SELECT COUNT(*) AS n " + frm,
                 cols + frm + " ORDER BY CASE WHEN n.status = 'active' AND " + snooze.AWAKE + " THEN 0 "
                 "WHEN n.status = 'active' THEN 1 ELSE 2 END, n.id DESC",
                 page, **p)
    for row in res["rows"]:
        row["alert_no"] = alert_no(row["id"])
        row["is_snoozed"] = snooze.is_snoozed(row)
    return res


def get_alert(alert_id, shop_id):
    """The alert, its item rows and any RFs already created from it. None if it is not this shop's."""
    head = _b().one(
        "SELECT n.*, l.code AS shop, l.name AS shop_name FROM notifications n "
        "JOIN locations l ON l.id = n.location_id WHERE n.id = :i AND n.notif_type = 'low_stock'",
        i=alert_id)
    if head is None or head["location_id"] != shop_id:
        return None
    head["alert_no"] = alert_no(head["id"])
    head["is_snoozed"] = snooze.is_snoozed(head)
    head["item_rows"] = _b().all(
        "SELECT ni.id, ni.item_id, ni.current_qty, ni.rop, ni.suggested_qty, ni.reason, "
        "i.item_code, i.item_name FROM notification_items ni JOIN items i ON i.id = ni.item_id "
        "WHERE ni.notification_id = :n ORDER BY i.item_name", n=alert_id)
    head["rfs"] = _b().all(
        "SELECT id, rf_no, status, created_at FROM requisitions "
        "WHERE notification_id = :n AND shop_id = :s ORDER BY id DESC", n=alert_id, s=shop_id)
    return head


def snooze_alert(user, alert_id, days):
    """Silent for a few days; the alert stays open and counts again by itself."""
    until = snooze.snooze(user, alert_id, "low_stock", days)
    return alert_no(alert_id), until


def wake_alert(user, alert_id):
    snooze.wake(user, alert_id, "low_stock")
    return alert_no(alert_id)


def dismiss_alert(user, alert_id, reason):
    """Dismiss an active alert with a reason. The next movement of a still-low item
    creates a fresh alert, so a dismissed one is never reopened by itself."""
    alert = _b().one("SELECT * FROM notifications WHERE id = :i AND notif_type = 'low_stock'", i=alert_id)
    if alert is None or alert["location_id"] != user["location_id"]:
        raise InventoryError("Alert not found for your shop.")
    if alert["status"] != "active":
        raise InventoryError("This alert is already %s." % alert["status"])
    reason = (reason or "").strip()
    if not reason:
        raise InventoryError("A reason is required to dismiss an alert.")
    if len(reason) > 300:
        raise InventoryError("The reason can be at most 300 characters.")
    _b().execute(
        "UPDATE notifications SET status = 'dismissed', dismissed_reason = :r, closed_by = :u, "
        "closed_at = CURRENT_TIMESTAMP WHERE id = :i", r=reason, u=user["id"], i=alert_id)
    inv.write_audit(user["id"], "dismiss", "notifications", alert_id,
                    old={"status": "active"}, new={"status": "dismissed", "reason": reason})
    return alert_no(alert_id)


# ---------------------------------------------------------------------
# Requisition forms (RF)
# ---------------------------------------------------------------------

def rf_form_data(shop_id):
    shop = get_shop(shop_id)
    items = _b().all(
        "SELECT i.id, i.item_code, i.item_name, COALESCE(b.on_hand, 0) AS on_hand, "
        "COALESCE(b.reserved, 0) AS reserved, COALESCE(s.rop, 0) AS rop, "
        "COALESCE(s.max_level, 0) AS max_level "
        "FROM items i LEFT JOIN stock_balances b ON b.item_id = i.id AND b.location_id = :s "
        "LEFT JOIN item_location_settings s ON s.item_id = i.id AND s.location_id = :s "
        "WHERE i.is_active ORDER BY i.item_name", s=shop_id)
    return {"shop": shop, "warehouses": list_warehouses(shop_id), "stock_items": items}


def rf_for_alert(alert_id):
    """The RF already raised from this low-stock alert, if any (cancelled ones do not count)."""
    if not alert_id:
        return None
    return _b().one(
        "SELECT id, rf_no, status FROM requisitions "
        "WHERE notification_id = :n AND status <> 'cancelled' ORDER BY id DESC LIMIT 1",
        n=alert_id)


def create_rf(user, shop_id, warehouse_id, remarks, pairs, submit, alert_id=None):
    """Save the RF header and lines. Returns (rf_id, rf_no, status).

    status is 'open' when the shop manager submits, 'draft' when saving for later.
    A draft does not appear in the warehouse RF queue until it is submitted.
    alert_id links the RF to the low-stock alert it was pre-filled from.
    """
    shop = get_shop(shop_id)
    try:
        wid = int(str(warehouse_id).strip())
    except (TypeError, ValueError):
        raise InventoryError("Choose the warehouse to request from.")
    warehouse = _b().one("SELECT id, code FROM locations WHERE id = :i AND is_active "
                         "AND loc_type = 'warehouse'", i=wid)
    if warehouse is None:
        raise InventoryError("Choose the warehouse to request from.")
    if wid == shop_id:
        raise InventoryError("The shop and the warehouse must be different.")

    lines = {}
    for item_id, qty in pairs:
        if not str(item_id).strip() and not str(qty).strip():
            continue
        if not str(item_id).strip():
            raise InventoryError("Select an item for every line.")
        iid = _whole(item_id, "Item")
        q = _whole(qty, "Quantity", 1)
        item = _b().one("SELECT id, item_name, is_active FROM items WHERE id = :i", i=iid)
        if item is None or not item["is_active"]:
            raise InventoryError("An item in the form is not available. Refresh the page and try again.")
        lines[iid] = lines.get(iid, 0) + q
    if not lines:
        raise InventoryError("Add at least one item to the requisition.")

    if alert_id:
        existing = rf_for_alert(alert_id)
        if existing:
            raise InventoryError("A requisition already exists for this alert: %s." % existing["rf_no"])

    remarks = (remarks or "").strip()
    if len(remarks) > 500:
        raise InventoryError("Remarks can be at most 500 characters.")

    status = "open" if submit else "draft"
    rf_no = inv.next_doc_no("RF")
    row = _b().one(
        "INSERT INTO requisitions (rf_no, shop_id, warehouse_id, notification_id, status, requested_by, remarks, submitted_at) "
        "VALUES (:no, :s, :w, :n, :st, :u, :r, "
        "CASE WHEN :st = 'open' THEN CURRENT_TIMESTAMP ELSE NULL END) RETURNING id",
        no=rf_no, s=shop_id, w=wid, n=alert_id, st=status, u=user["id"], r=remarks or None)
    rf_id = row["id"]
    for item_id, qty in lines.items():
        _b().execute("INSERT INTO requisition_items (rf_id, item_id, qty_requested) VALUES (:r, :i, :q)",
                     r=rf_id, i=item_id, q=qty)
    inv.write_audit(user["id"], "create", "requisitions", rf_id,
                    new={"rf_no": rf_no, "shop": shop["code"], "warehouse": warehouse["code"],
                         "status": status, "items": [[i, q] for i, q in lines.items()]})
    return rf_id, rf_no, status


def submit_rf(user, rf_id):
    """Send a saved draft to the warehouse. Returns the RF number."""
    rf = _b().one("SELECT * FROM requisitions WHERE id = :i", i=rf_id)
    if rf is None or rf["shop_id"] != user["location_id"]:
        raise InventoryError("Requisition not found for your shop.")
    if rf["status"] != "draft":
        raise InventoryError("This requisition is already %s." % rf["status"].replace("_", " "))
    if _b().one("SELECT COUNT(*) AS n FROM requisition_items WHERE rf_id = :r", r=rf_id)["n"] == 0:
        raise InventoryError("This requisition has no items.")
    _b().execute("UPDATE requisitions SET status = 'open', submitted_at = CURRENT_TIMESTAMP WHERE id = :i",
                 i=rf_id)
    inv.write_audit(user["id"], "submit", "requisitions", rf_id,
                    old={"status": "draft"}, new={"status": "open", "rf_no": rf["rf_no"]})
    return rf["rf_no"]


def list_rfs(user, status="", q="", page=1):
    """RF list. A shop manager sees their shop's RFs, a warehouse manager the RFs sent
    to their warehouse, the owner every RF."""
    role, loc = user["role"], user["location_id"]
    where, p = "", {}
    if role == "shop_manager":
        where += " AND r.shop_id = :loc"
        p["loc"] = loc
    elif role == "warehouse_manager":
        where += " AND r.warehouse_id = :loc AND r.status <> 'draft'"
        p["loc"] = loc
    if status in RF_STATUSES:
        where += " AND r.status = :st"
        p["st"] = status
    if q.strip():
        where += (" AND (LOWER(r.rf_no) LIKE :q OR LOWER(s.code) LIKE :q OR LOWER(s.name) LIKE :q "
                  "OR LOWER(w.code) LIKE :q)")
        p["q"] = _like(q)
    frm = ("FROM requisitions r JOIN locations s ON s.id = r.shop_id "
           "JOIN locations w ON w.id = r.warehouse_id WHERE 1 = 1" + where)
    cols = ("SELECT r.id, r.rf_no, r.status, COALESCE(r.submitted_at, r.created_at) AS rf_date, "
            "s.code AS shop, w.code AS warehouse, "
            "(SELECT COUNT(*) FROM requisition_items x WHERE x.rf_id = r.id) AS n_items, "
            "(SELECT COALESCE(SUM(x.qty_requested), 0) FROM requisition_items x "
            " WHERE x.rf_id = r.id) AS qty_requested, "
            "(SELECT COALESCE(SUM(x.qty_delivered), 0) FROM requisition_items x "
            " WHERE x.rf_id = r.id) AS qty_delivered ")
    return _paged("SELECT COUNT(*) AS n " + frm, cols + frm + " ORDER BY r.id DESC", page, **p)


def get_rf(rf_id, user):
    """The RF with its lines and transfer slips, or None when it is not this user's to see."""
    head = _b().one(
        "SELECT r.*, s.code AS shop, s.name AS shop_name, w.code AS warehouse, "
        "w.name AS warehouse_name, u.full_name AS requested_by_name "
        "FROM requisitions r JOIN locations s ON s.id = r.shop_id "
        "JOIN locations w ON w.id = r.warehouse_id JOIN users u ON u.id = r.requested_by "
        "WHERE r.id = :i", i=rf_id)
    if head is None:
        return None
    role, loc = user["role"], user["location_id"]
    if role == "shop_manager" and head["shop_id"] != loc:
        return None
    if role == "warehouse_manager" and (head["warehouse_id"] != loc or head["status"] == "draft"):
        return None
    if role not in ("owner", "shop_manager", "warehouse_manager"):
        return None
    head["alert_no"] = alert_no(head["notification_id"]) if head["notification_id"] else None
    head["lines"] = _b().all(
        "SELECT x.id AS rf_item_id, x.item_id, x.qty_requested, x.qty_reserved, x.qty_delivered, "
        "x.qty_requested - x.qty_reserved AS still_to_reserve, "
        "x.qty_reserved - x.qty_delivered AS still_to_deliver, "
        "i.item_code, i.item_name "
        "FROM requisition_items x JOIN items i ON i.id = x.item_id "
        "WHERE x.rf_id = :r ORDER BY i.item_name", r=rf_id)
    head["ts"] = _b().all(
        "SELECT id, ts_no, status, created_at FROM transfer_slips WHERE rf_id = :r ORDER BY id", r=rf_id)
    return head


def can_cancel(user, rf):
    """Only the shop manager who raised it, and only before anything was delivered."""
    if user["role"] != "shop_manager":
        return False, "Only the shop manager can cancel a requisition."
    if rf["shop_id"] != user["location_id"]:
        return False, "You can only cancel your own shop's requisitions."
    if rf["status"] not in ("draft", "open"):
        return False, "This requisition is %s, so it can no longer be cancelled." % rf["status"].replace("_", " ")
    return True, ""


def cancel_rf(user, rf_id, reason):
    """Cancel the RF and free every reservation the warehouse still holds for it.

    Returns (rf_no, number_of_reservations_released).
    """
    rf = _b().one("SELECT * FROM requisitions WHERE id = :i", i=rf_id)
    if rf is None:
        rf = {}
    ok, why = can_cancel(user, rf) if rf else (False, "Requisition not found.")
    if not ok:
        raise InventoryError(why)
    reason = (reason or "").strip()
    if not reason:
        raise InventoryError("A reason is required to cancel a requisition.")
    if len(reason) > 300:
        raise InventoryError("The reason can be at most 300 characters.")

    busy = _b().one("SELECT ts_no FROM transfer_slips WHERE rf_id = :r "
                    "AND status IN ('draft', 'assigned', 'loaded', 'in_transit') LIMIT 1", r=rf_id)
    if busy:
        raise InventoryError("Transfer slip %s is being prepared for this RF. Cancel that TS first."
                             % busy["ts_no"])

    held = _b().all(
        "SELECT rs.id, rs.item_id FROM reservations rs "
        "JOIN requisition_items x ON x.id = rs.rf_item_id "
        "WHERE x.rf_id = :r AND rs.status = 'reserved' ORDER BY rs.id", r=rf_id)
    rf_items = _b().all("SELECT DISTINCT item_id FROM requisition_items WHERE rf_id = :r", r=rf_id)
    for res in held:
        inv.release_reservation(res["id"], "cancelled")

    # Everything that was held has been released, so the line holds nothing any more.
    # (Only draft/open RFs can be cancelled, so nothing was delivered.)
    _b().execute("UPDATE requisition_items SET qty_reserved = 0 WHERE rf_id = :r", r=rf_id)
    _b().execute(
        "UPDATE requisitions SET status = 'cancelled', cancel_reason = :reason, "
        "closed_at = CURRENT_TIMESTAMP WHERE id = :i", reason=reason, i=rf_id)
    inv.write_audit(user["id"], "cancel", "requisitions", rf_id,
                    old={"status": rf["status"]},
                    new={"status": "cancelled", "reason": reason,
                         "released_reservations": [r["id"] for r in held]})
    # Freeing reserved stock can bring the warehouse back above ROP, and a cancellation
    # removes a waiting RF from the backorder: let Inventory update its alert rows.
    inv_ops.refresh_alerts(rf["warehouse_id"], [r["item_id"] for r in rf_items])
    return rf["rf_no"], len(held)
