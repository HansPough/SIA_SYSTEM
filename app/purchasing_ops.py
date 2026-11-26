"""Purchasing module (Module 3): restock alerts, REF, RFQ, quotations, approvals, PO, suppliers.

Routes in blueprints/purchasing.py stay thin: read the form, call one function here,
then commit or roll back. This module does not change stock: goods are received by
Delivery (Module 4), which calls the Inventory stock functions.

Money is stored as NUMERIC in PostgreSQL. The values are validated and rounded here
and passed to SQL as strings, so SQLite and PostgreSQL both store the same 2 decimals.
"""
import datetime as dt
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP

from . import inventory_ops as inv_ops
from . import alert_snooze as snooze
from . import inventory_service as inv
from . import ref_coverage as cov
from .inventory_service import InventoryError

PAGE_SIZE = 50
ALERT_STATUSES = ("active", "completed", "dismissed")
REF_STATUSES = ("draft", "pending_owner", "submitted", "rfq_sent", "for_approval", "approved",
                "receiving", "closed", "cancelled")
PO_STATUSES = ("draft", "sent", "partially_received", "received", "cancelled")
CANCELABLE_REF = ("draft", "pending_owner", "submitted", "rfq_sent")


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


def _money(v, label, required=True):
    """Validate a price and return it as a 2-decimal string ('12.50')."""
    s = str(v if v is not None else "").strip().replace(",", "")
    if not s:
        if required:
            raise InventoryError("%s is required." % label)
        return None
    try:
        d = Decimal(s)
    except InvalidOperation:
        raise InventoryError("%s must be a number." % label)
    if d <= 0:
        raise InventoryError("%s must be more than zero." % label)
    return str(d.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))


def _dec(v):
    return Decimal(str(v if v is not None else 0))


def _price_text(v):
    """2-decimal text for a price read back from the database (NUMERIC or SQLite float)."""
    return str(_dec(v).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))


def _amount(qty, price):
    return _dec(price) * int(qty)


def _total_text(lines):
    return str(sum((_amount(l["qty"], l["unit_price"]) for l in lines), Decimal("0")).quantize(Decimal("0.01")))


def _date(v, label, required=False):
    s = str(v if v is not None else "").strip()
    if not s:
        if required:
            raise InventoryError("%s is required." % label)
        return None
    try:
        return dt.date.fromisoformat(s)
    except ValueError:
        raise InventoryError("%s must be a date (YYYY-MM-DD)." % label)


def _today():
    return dt.date.today()


def alert_no(alert_id):
    """Readable reference for an alert; alerts are not documents, so no sequence entry."""
    return "ALT-%04d" % alert_id


def get_location(location_id):
    return _b().one("SELECT id, code, name, loc_type FROM locations WHERE id = :l", l=location_id)


def list_warehouses():
    return _b().all("SELECT id, code, name FROM locations WHERE is_active AND loc_type = 'warehouse' ORDER BY code")


def get_warehouse(location_id):
    loc = get_location(location_id)
    if loc is None or loc["loc_type"] != "warehouse":
        raise InventoryError("A purchasing action needs a warehouse. Ask the owner to set your location.")
    return loc


def list_items_for_select():
    return _b().all("SELECT id, item_code, item_name FROM items WHERE is_active ORDER BY item_name")


def list_suppliers_select(regular_only=False):
    sql = "SELECT id, supplier_name, contact_person, email, phone, is_regular FROM suppliers WHERE is_active"
    if regular_only:
        sql += " AND is_regular"
    return _b().all(sql + " ORDER BY supplier_name")


# ---------------------------------------------------------------------
# Restock alerts (restock notifications for the warehouse manager)
# ---------------------------------------------------------------------

def list_alerts(warehouse_id, status="", q="", page=1):
    where, p = "", {"w": warehouse_id}
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
    frm = "FROM notifications n WHERE n.notif_type = 'restock' AND n.location_id = :w" + where
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


def get_alert(alert_id, warehouse_id):
    head = _b().one(
        "SELECT n.*, l.code AS warehouse, l.name AS warehouse_name FROM notifications n "
        "JOIN locations l ON l.id = n.location_id "
        "WHERE n.id = :i AND n.notif_type = 'restock'", i=alert_id)
    if head is None or head["location_id"] != warehouse_id:
        return None
    head["alert_no"] = alert_no(head["id"])
    head["is_snoozed"] = snooze.is_snoozed(head)
    head["item_rows"] = _b().all(
        "SELECT ni.id, ni.item_id, ni.current_qty, ni.rop, ni.suggested_qty, ni.reason, "
        "i.item_code, i.item_name, "
        "(SELECT COUNT(*) FROM requisition_items x JOIN requisitions r ON r.id = x.rf_id "
        " WHERE x.item_id = ni.item_id AND r.warehouse_id = :w "
        " AND r.status IN ('open', 'partially_delivered') "
        " AND x.qty_requested - x.qty_reserved > 0) AS waiting_rfs "
        "FROM notification_items ni JOIN items i ON i.id = ni.item_id "
        "WHERE ni.notification_id = :n ORDER BY i.item_name", n=alert_id, w=warehouse_id)
    head["refs"] = _b().all(
        "SELECT id, ref_no, status, created_at FROM replenishments "
        "WHERE notification_id = :n AND warehouse_id = :w ORDER BY id DESC", n=alert_id, w=warehouse_id)
    return head


def snooze_alert(user, alert_id, days):
    """Silent for a few days; the alert stays open and counts again by itself."""
    until = snooze.snooze(user, alert_id, "restock", days)
    return alert_no(alert_id), until


def wake_alert(user, alert_id):
    snooze.wake(user, alert_id, "restock")
    return alert_no(alert_id)


def dismiss_alert(user, alert_id, reason):
    alert = _b().one("SELECT * FROM notifications WHERE id = :i AND notif_type = 'restock'", i=alert_id)
    if alert is None or alert["location_id"] != user["location_id"]:
        raise InventoryError("Alert not found for your warehouse.")
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


def _complete_alert(user_id, notification_id, item_ids):
    """Purchasing owns the restock alert: the items put on the REF come off it when the
    REF is submitted. The alert closes only when nothing else is left waiting on it."""
    if not notification_id:
        return
    row = _b().one("SELECT id, status FROM notifications WHERE id = :i", i=notification_id)
    if row is None or row["status"] != "active":
        return
    for item_id in set(item_ids):
        _b().execute("DELETE FROM notification_items WHERE notification_id = :n AND item_id = :i",
                     n=notification_id, i=item_id)
    left = _b().one("SELECT COUNT(*) AS n FROM notification_items WHERE notification_id = :n",
                    n=notification_id)["n"]
    if left == 0:
        _b().execute("UPDATE notifications SET status = 'completed', closed_by = :u, "
                     "closed_at = CURRENT_TIMESTAMP WHERE id = :i", u=user_id, i=notification_id)
        inv.write_audit(user_id, "complete", "notifications", notification_id,
                        old={"status": "active"}, new={"status": "completed"})


# ---------------------------------------------------------------------
# REF (replenishment form)
# ---------------------------------------------------------------------

def ref_form_data(warehouse_id):
    warehouse = get_warehouse(warehouse_id)
    items = _b().all(
        "SELECT i.id, i.item_code, i.item_name, COALESCE(b.on_hand, 0) AS on_hand, "
        "COALESCE(b.reserved, 0) AS reserved, COALESCE(s.rop, 0) AS rop, "
        "COALESCE(s.max_level, 0) AS max_level "
        "FROM items i LEFT JOIN stock_balances b ON b.item_id = i.id AND b.location_id = :w "
        "LEFT JOIN item_location_settings s ON s.item_id = i.id AND s.location_id = :w "
        "WHERE i.is_active ORDER BY i.item_name", w=warehouse_id)
    return {"warehouse": warehouse, "stock_items": items}


def list_refs(user, status="", q="", page=1):
    where, p = "", {}
    if user["role"] != "owner":
        where += " AND r.warehouse_id = :loc"
        p["loc"] = user["location_id"]
    if status in REF_STATUSES:
        where += " AND r.status = :st"
        p["st"] = status
    if q.strip():
        where += " AND (LOWER(r.ref_no) LIKE :q OR LOWER(w.code) LIKE :q)"
        p["q"] = _like(q)
    frm = ("FROM replenishments r JOIN locations w ON w.id = r.warehouse_id WHERE 1 = 1" + where)
    cols = ("SELECT r.id, r.ref_no, r.status, r.created_at, r.submitted_at, w.code AS warehouse, "
            "(SELECT COUNT(*) FROM replenishment_items x WHERE x.ref_id = r.id) AS n_items, "
            "(SELECT s.supplier_name FROM purchase_approvals pa JOIN suppliers s ON s.id = pa.supplier_id "
            " WHERE pa.ref_id = r.id ORDER BY pa.id DESC LIMIT 1) AS supplier ")
    return _paged("SELECT COUNT(*) AS n " + frm, cols + frm + " ORDER BY r.id DESC", page, **p)


def get_ref(ref_id, user):
    head = _b().one(
        "SELECT r.*, w.code AS warehouse, w.name AS warehouse_name, u.full_name AS created_by_name "
        "FROM replenishments r JOIN locations w ON w.id = r.warehouse_id "
        "JOIN users u ON u.id = r.created_by WHERE r.id = :i", i=ref_id)
    if head is None:
        return None
    if user["role"] != "owner" and (user["role"] != "warehouse_manager"
                                    or head["warehouse_id"] != user["location_id"]):
        return None
    head["lines"] = _b().all(
        "SELECT ri.id AS ref_item_id, ri.item_id, ri.qty_requested, ri.qty_received, ri.reason, ri.rf_id, "
        "i.item_code, i.item_name, rf.rf_no FROM replenishment_items ri "
        "JOIN items i ON i.id = ri.item_id LEFT JOIN requisitions rf ON rf.id = ri.rf_id "
        "WHERE ri.ref_id = :r ORDER BY i.item_name", r=ref_id)
    by_line = {c["ref_item_id"]: c for c in cov.coverage(ref_id)}
    for ln in head["lines"]:
        c = by_line.get(ln["ref_item_id"], {})
        ln.update(qty_dropped=c.get("qty_dropped", 0), dropped_reason=c.get("dropped_reason"),
                  on_po=c.get("on_po", 0), pending=c.get("pending", 0), open=c.get("open", 0),
                  po_nos=c.get("po_nos", []))
    head["has_open"] = any(ln["open"] > 0 for ln in head["lines"])
    head["rfq_rows"] = _b().all(
        "SELECT rq.id AS rfq_id, rq.rfq_no, rq.status AS rfq_status, rq.reply_deadline, "
        "s.id AS supplier_id, s.supplier_name, rs.sent_at, rs.replied_at, "
        "(SELECT q.total_amount FROM quotations q WHERE q.rfq_id = rq.id AND q.supplier_id = s.id) AS total_quoted, "
        "(SELECT q.id FROM quotations q WHERE q.rfq_id = rq.id AND q.supplier_id = s.id) AS quotation_id, "
        "(SELECT COUNT(*) FROM quotation_items qi JOIN quotations q ON q.id = qi.quotation_id "
        " WHERE q.rfq_id = rq.id AND q.supplier_id = s.id AND qi.superseded_at IS NULL) AS n_live_lines "
        "FROM rfqs rq JOIN rfq_suppliers rs ON rs.rfq_id = rq.id "
        "JOIN suppliers s ON s.id = rs.supplier_id "
        "WHERE rq.ref_id = :r ORDER BY rq.id, s.supplier_name", r=ref_id)
    head["approvals"] = _b().all(
        "SELECT pa.*, s.supplier_name FROM purchase_approvals pa JOIN suppliers s ON s.id = pa.supplier_id "
        "WHERE pa.ref_id = :r ORDER BY pa.id", r=ref_id)
    for a in head["approvals"]:
        a["can_withdraw"] = (user["role"] == "warehouse_manager" and a["decision"] == "pending"
                             and a["submitted_by"] == user["id"])
    head["pos"] = _b().all(
        "SELECT po.*, s.supplier_name, "
        "COALESCE((SELECT SUM(poi.qty_ordered * poi.unit_price) FROM purchase_order_items poi WHERE poi.po_id = po.id), 0) "
        "AS lines_total FROM purchase_orders po JOIN suppliers s ON s.id = po.supplier_id "
        "WHERE po.ref_id = :r ORDER BY po.id", r=ref_id)
    head["partly_ordered"] = any(p["status"] != "cancelled" for p in head["pos"]) and head["has_open"]
    head["rfq"] = _b().one("SELECT * FROM rfqs WHERE ref_id = :r ORDER BY id DESC LIMIT 1", r=ref_id)
    return head


def _ref_for_write(user, ref_id):
    ref = _b().one("SELECT * FROM replenishments WHERE id = :i", i=ref_id)
    if ref is None:
        raise InventoryError("Replenishment form not found.")
    if user["role"] != "warehouse_manager" or ref["warehouse_id"] != user["location_id"]:
        raise InventoryError("Only the warehouse manager of this warehouse can change this form.")
    return ref


def _waiting_rf(warehouse_id, item_id):
    """The oldest open RF line still waiting for this item (used for shortage lines)."""
    return _b().one(
        "SELECT r.id, r.rf_no FROM requisition_items x JOIN requisitions r ON r.id = x.rf_id "
        "WHERE x.item_id = :i AND r.warehouse_id = :w AND r.status IN ('open', 'partially_delivered') "
        "AND x.qty_requested - x.qty_reserved > 0 ORDER BY r.id LIMIT 1", i=item_id, w=warehouse_id)


def ref_for_alert(alert_id):
    """The REF already raised from this restock alert, if any (cancelled ones do not count)."""
    if not alert_id:
        return None
    return _b().one(
        "SELECT id, ref_no, status FROM replenishments "
        "WHERE notification_id = :n AND status <> 'cancelled' ORDER BY id DESC LIMIT 1",
        n=alert_id)


def create_ref(user, warehouse_id, remarks, pairs, submit, alert_id=None):
    """Save the REF and its lines. Returns (ref_id, ref_no, status).

    pairs: (item_id, qty, reason) from the form. When submitted, the items on the form
    come off the source restock alert, because Purchasing now owns their restock.
    """
    warehouse = get_warehouse(warehouse_id)
    if user["role"] != "warehouse_manager" or warehouse_id != user["location_id"]:
        raise InventoryError("You can only raise a replenishment form for your own warehouse.")
    if alert_id:
        n = _b().one("SELECT location_id FROM notifications WHERE id = :i", i=alert_id)
        if n is None or n["location_id"] != warehouse_id:
            raise InventoryError("That restock alert does not belong to this warehouse.")
        existing = ref_for_alert(alert_id)
        if existing:
            raise InventoryError("A replenishment already exists for this alert: %s." % existing["ref_no"])
    lines = {}
    for item_id, qty, reason in pairs:
        if not str(item_id).strip() and not str(qty).strip():
            continue
        if not str(item_id).strip():
            raise InventoryError("Select an item for every line.")
        iid = _whole(item_id, "Item")
        q = _whole(qty, "Quantity", 1)
        why = (reason or "below_rop").strip().lower()
        if why not in ("shortage", "below_rop"):
            why = "below_rop"
        item = _b().one("SELECT id, item_name, is_active FROM items WHERE id = :i", i=iid)
        if item is None or not item["is_active"]:
            raise InventoryError("An item in the form is not available. Refresh the page and try again.")
        if iid in lines:
            lines[iid]["qty"] += q
        else:
            lines[iid] = {"qty": q, "reason": why}

    if not lines:
        raise InventoryError("Add at least one item to the replenishment form.")
    remarks = (remarks or "").strip()
    if len(remarks) > 500:
        raise InventoryError("Remarks can be at most 500 characters.")

    status = "pending_owner" if submit else "draft"      # the owner approves it before any RFQ
    ref_no = inv.next_doc_no("REF")
    row = _b().one(
        "INSERT INTO replenishments (ref_no, warehouse_id, notification_id, status, created_by, remarks, submitted_at) "
        "VALUES (:no, :w, :n, :st, :u, :rem, "
        "CASE WHEN :st = 'pending_owner' THEN CURRENT_TIMESTAMP ELSE NULL END) RETURNING id",
        no=ref_no, w=warehouse_id, n=alert_id, st=status, u=user["id"], rem=remarks or None)
    ref_id = row["id"]
    for item_id, line in lines.items():
        rf = _waiting_rf(warehouse_id, item_id) if line["reason"] == "shortage" else None
        _b().execute(
            "INSERT INTO replenishment_items (ref_id, item_id, qty_requested, reason, rf_id) "
            "VALUES (:r, :i, :q, :why, :rf)", r=ref_id, i=item_id, q=line["qty"],
            why=line["reason"], rf=rf["id"] if rf else None)
    if submit:
        _complete_alert(user["id"], alert_id, lines.keys())
    inv.write_audit(user["id"], "create", "replenishments", ref_id,
                    new={"ref_no": ref_no, "warehouse": warehouse["code"], "status": status,
                         "items": [[i, l["qty"], l["reason"]] for i, l in lines.items()]})
    return ref_id, ref_no, status


def submit_ref(user, ref_id):
    ref = _ref_for_write(user, ref_id)
    if ref["status"] != "draft":
        raise InventoryError("This form is already %s." % ref["status"].replace("_", " "))
    if _b().one("SELECT COUNT(*) AS n FROM replenishment_items WHERE ref_id = :r", r=ref_id)["n"] == 0:
        raise InventoryError("This form has no items.")
    _b().execute("UPDATE replenishments SET status = 'pending_owner', submitted_at = CURRENT_TIMESTAMP "
                 "WHERE id = :i", i=ref_id)
    items = _b().all("SELECT item_id FROM replenishment_items WHERE ref_id = :r", r=ref_id)
    _complete_alert(user["id"], ref["notification_id"], [r["item_id"] for r in items])
    inv.write_audit(user["id"], "submit", "replenishments", ref_id,
                    old={"status": "draft"}, new={"status": "pending_owner", "ref_no": ref["ref_no"]})
    return ref["ref_no"]


def _ref_for_owner(user, ref_id):
    ref = _b().one("SELECT * FROM replenishments WHERE id = :i", i=ref_id)
    if ref is None:
        raise InventoryError("Replenishment form not found.")
    if user["role"] != "owner":
        raise InventoryError("Only the owner can approve or reject a replenishment form.")
    if ref["status"] != "pending_owner":
        raise InventoryError("%s is not waiting for your approval (it is %s)."
                             % (ref["ref_no"], ref["status"].replace("_", " ")))
    return ref


def approve_ref(user, ref_id, remarks=None):
    """The owner says yes to the REF. The warehouse manager can now send the RFQ."""
    ref = _ref_for_owner(user, ref_id)
    remarks = (remarks or "").strip()
    if len(remarks) > 300:
        raise InventoryError("Remarks can be at most 300 characters.")
    _b().execute("UPDATE replenishments SET status = 'submitted', owner_decided_by = :u, "
                 "owner_decided_at = CURRENT_TIMESTAMP, owner_remarks = :r WHERE id = :i",
                 u=user["id"], r=remarks or None, i=ref_id)
    inv.write_audit(user["id"], "approve", "replenishments", ref_id,
                    old={"status": "pending_owner"}, new={"status": "submitted", "ref_no": ref["ref_no"]})
    return ref["ref_no"]


def reject_ref(user, ref_id, remarks):
    """The owner says no. The REF is cancelled with the owner's reason (a reason is required)."""
    ref = _ref_for_owner(user, ref_id)
    remarks = (remarks or "").strip()
    if not remarks:
        raise InventoryError("Write a reason when rejecting a replenishment form.")
    if len(remarks) > 300:
        raise InventoryError("The reason can be at most 300 characters.")
    _b().execute("UPDATE replenishments SET status = 'cancelled', cancel_reason = :c, owner_decided_by = :u, "
                 "owner_decided_at = CURRENT_TIMESTAMP, owner_remarks = :r, closed_at = CURRENT_TIMESTAMP "
                 "WHERE id = :i", c="Rejected by the owner: " + remarks, u=user["id"], r=remarks, i=ref_id)
    inv.write_audit(user["id"], "reject", "replenishments", ref_id,
                    old={"status": "pending_owner"}, new={"status": "cancelled", "reason": remarks})
    return ref["ref_no"]


def list_refs_to_approve(user):
    """REFs waiting for the owner, oldest first. Empty for anyone else."""
    if user["role"] != "owner":
        return []
    return _b().all(
        "SELECT r.id, r.ref_no, r.submitted_at, w.code AS warehouse, u.full_name AS created_by_name, "
        "(SELECT COUNT(*) FROM replenishment_items x WHERE x.ref_id = r.id) AS n_items, "
        "(SELECT COALESCE(SUM(x.qty_requested), 0) FROM replenishment_items x WHERE x.ref_id = r.id) AS n_qty "
        "FROM replenishments r JOIN locations w ON w.id = r.warehouse_id JOIN users u ON u.id = r.created_by "
        "WHERE r.status = 'pending_owner' ORDER BY r.submitted_at, r.id")


def can_cancel(user, ref):
    if user["role"] != "warehouse_manager" or ref["warehouse_id"] != user["location_id"]:
        return False, "Only the warehouse manager of this warehouse can cancel it."
    if ref["status"] not in CANCELABLE_REF:
        return False, "This form is %s, so it can no longer be cancelled." % ref["status"].replace("_", " ")
    if _b().one("SELECT COUNT(*) AS n FROM purchase_orders WHERE ref_id = :r AND status <> 'cancelled'",
                r=ref["id"])["n"]:
        return False, "This form already has purchase orders. Cancel them first, or drop the items you no longer need."
    return True, ""


def cancel_ref(user, ref_id, reason):
    ref = _ref_for_write(user, ref_id)
    ok, why = can_cancel(user, ref)
    if not ok:
        raise InventoryError(why)
    reason = (reason or "").strip()
    if not reason:
        raise InventoryError("A reason is required to cancel a replenishment form.")
    if len(reason) > 300:
        raise InventoryError("The reason can be at most 300 characters.")
    _b().execute("UPDATE replenishments SET status = 'cancelled', cancel_reason = :r, "
                 "closed_at = CURRENT_TIMESTAMP WHERE id = :i", r=reason, i=ref_id)
    _b().execute("UPDATE rfqs SET status = 'closed' WHERE ref_id = :r AND status = 'sent'", r=ref_id)
    inv.write_audit(user["id"], "cancel", "replenishments", ref_id,
                    old={"status": ref["status"]}, new={"status": "cancelled", "reason": reason})
    return ref["ref_no"]


# ---------------------------------------------------------------------
# RFQ
# ---------------------------------------------------------------------

def rfq_form_data(ref_id, user):
    """The RFQ screen. For a first RFQ it lists every item; for a later round it lists only the items
    that still have no supplier, and marks the suppliers that were already asked."""
    ref = _ref_for_write(user, ref_id)
    existing = _b().one("SELECT id, rfq_no FROM rfqs WHERE ref_id = :r ORDER BY id DESC LIMIT 1", r=ref_id)
    open_items = [{"item_id": c["item_id"], "item_name": c["item_name"], "qty_requested": c["open"],
                   "ordered": c["on_po"], "dropped": c["qty_dropped"]}
                  for c in cov.coverage(ref_id) if c["open"] > 0]
    asked = {}
    for row in _b().all("SELECT rs.supplier_id, rq.rfq_no FROM rfq_suppliers rs JOIN rfqs rq ON rq.id = rs.rfq_id "
                        "WHERE rq.ref_id = :r ORDER BY rq.id", r=ref_id):
        asked.setdefault(row["supplier_id"], []).append(row["rfq_no"])
    suppliers = [dict(s, asked=", ".join(asked.get(s["id"], []))) for s in list_suppliers_select()]
    return {"ref": ref, "suppliers": suppliers, "existing": existing, "round": 2 if existing else 1,
            "warehouse": (get_location(ref["warehouse_id"]) or {}).get("code", ""),
            "ref_items": open_items}


def create_rfq(user, ref_id, supplier_ids, deadline, terms):
    ref = _ref_for_write(user, ref_id)
    if _b().one("SELECT id FROM rfqs WHERE ref_id = :r LIMIT 1", r=ref_id):
        # A later round: ask more suppliers about the items that still have no supplier.
        if ref["status"] not in ("submitted", "rfq_sent"):
            raise InventoryError("This REF is %s, so another RFQ cannot be sent now." % ref["status"].replace("_", " "))
        if not cov.open_lines(ref_id):
            raise InventoryError("Every item is already ordered, waiting for approval, or dropped. "
                                 "There is nothing left to ask suppliers about.")
    elif ref["status"] == "pending_owner":
        raise InventoryError("The owner has not approved this REF yet. You can send the RFQ once they do.")
    elif ref["status"] != "submitted":
        raise InventoryError("Submit the REF before sending an RFQ.")
    ids = []
    for raw in supplier_ids:
        if str(raw).strip():
            sid = _whole(raw, "Supplier")
            if sid not in ids:
                ids.append(sid)
    if not ids:
        raise InventoryError("Choose at least one supplier for the RFQ.")
    names = []
    for sid in ids:
        s = _b().one("SELECT supplier_name FROM suppliers WHERE id = :i AND is_active", i=sid)
        if s is None:
            raise InventoryError("A chosen supplier is not active. Refresh the page and try again.")
        names.append(s["supplier_name"])
    d = _date(deadline, "Reply deadline")
    if d and d < _today():
        raise InventoryError("The reply deadline cannot be in the past.")
    terms = (terms or "").strip()
    if len(terms) > 1000:
        raise InventoryError("Terms can be at most 1000 characters.")
    rfq_no = inv.next_doc_no("RFQ")
    row = _b().one(
        "INSERT INTO rfqs (rfq_no, ref_id, reply_deadline, terms, status, created_by, sent_at) "
        "VALUES (:no, :ref, :d, :t, 'sent', :u, CURRENT_TIMESTAMP) RETURNING id",
        no=rfq_no, ref=ref_id, d=d.isoformat() if d else None, t=terms or None, u=user["id"])
    rfq_id = row["id"]
    for sid in ids:
        _b().execute("INSERT INTO rfq_suppliers (rfq_id, supplier_id, sent_at) "
                     "VALUES (:r, :s, CURRENT_TIMESTAMP)", r=rfq_id, s=sid)
    # The RFQ is a document: freeze what it asks about (the quantity still open right now).
    for ln in cov.open_lines(ref_id):
        _b().execute("INSERT INTO rfq_items (rfq_id, replenishment_item_id, item_id, qty_requested) "
                     "VALUES (:r, :ri, :i, :q)", r=rfq_id, ri=ln["ref_item_id"], i=ln["item_id"], q=ln["open"])
    cov.refresh_status(ref_id)
    inv.write_audit(user["id"], "create", "rfqs", rfq_id,
                    new={"rfq_no": rfq_no, "ref_no": ref["ref_no"], "suppliers": names})
    return rfq_no, len(ids), rfq_id


def get_rfq(rfq_id, user):
    """One RFQ with its items and suppliers, or None (missing, or not this user's warehouse)."""
    rq = _b().one(
        "SELECT rq.*, r.ref_no, r.warehouse_id, r.status AS ref_status, w.code AS warehouse, "
        "w.name AS warehouse_name, u.full_name AS created_by_name "
        "FROM rfqs rq JOIN replenishments r ON r.id = rq.ref_id "
        "JOIN locations w ON w.id = r.warehouse_id JOIN users u ON u.id = rq.created_by "
        "WHERE rq.id = :i", i=rfq_id)
    if rq is None:
        return None
    if user["role"] != "owner" and (user["role"] != "warehouse_manager"
                                    or rq["warehouse_id"] != user["location_id"]):
        return None
    rq["items"] = _b().all(
        "SELECT x.replenishment_item_id AS ref_item_id, x.item_id, x.qty_requested, i.item_code, i.item_name "
        "FROM rfq_items x JOIN items i ON i.id = x.item_id WHERE x.rfq_id = :r ORDER BY i.item_name", r=rfq_id)
    if not rq["items"]:      # an RFQ made before rfq_items existed (migration 020 fills these in)
        rq["items"] = _b().all(
            "SELECT ri.id AS ref_item_id, ri.item_id, ri.qty_requested, i.item_code, i.item_name "
            "FROM replenishment_items ri JOIN items i ON i.id = ri.item_id "
            "WHERE ri.ref_id = :r ORDER BY i.item_name", r=rq["ref_id"])
    rq["suppliers"] = _b().all(
        "SELECT s.id AS supplier_id, s.supplier_name, s.contact_person, s.email, s.phone, s.address, "
        "rs.sent_at, rs.replied_at, "
        "(SELECT q.id FROM quotations q WHERE q.rfq_id = rs.rfq_id AND q.supplier_id = s.id) AS quotation_id, "
        "(SELECT q.total_amount FROM quotations q WHERE q.rfq_id = rs.rfq_id AND q.supplier_id = s.id) "
        "AS total_quoted "
        "FROM rfq_suppliers rs JOIN suppliers s ON s.id = rs.supplier_id "
        "WHERE rs.rfq_id = :r ORDER BY s.supplier_name", r=rfq_id)
    rq["n_replied"] = sum(1 for s in rq["suppliers"] if s["replied_at"])
    rq["is_latest"] = bool(_b().one(
        "SELECT 1 AS x FROM rfqs WHERE ref_id = :r AND id > :i LIMIT 1", r=rq["ref_id"], i=rfq_id) is None)
    return rq


def rfq_supplier_quote(rfq_id, supplier_id):
    """What one supplier quoted on one RFQ: {item_id: {"unit_price", "qty_offered"}}, live lines only."""
    rows = _b().all(
        "SELECT qi.item_id, qi.unit_price, qi.qty_offered FROM quotation_items qi "
        "JOIN quotations q ON q.id = qi.quotation_id "
        "WHERE q.rfq_id = :r AND q.supplier_id = :s AND qi.superseded_at IS NULL", r=rfq_id, s=supplier_id)
    return {r["item_id"]: {"unit_price": _price_text(r["unit_price"]), "qty_offered": r["qty_offered"]} for r in rows}


def next_supplier_to_quote(ref_id, current_supplier_id):
    """After saving one reply: the next supplier of the latest RFQ that has not replied yet."""
    rfq = _ref_rfq(ref_id)
    if rfq is None:
        return None
    row = _b().one(
        "SELECT rs.supplier_id FROM rfq_suppliers rs JOIN suppliers s ON s.id = rs.supplier_id "
        "WHERE rs.rfq_id = :r AND rs.replied_at IS NULL AND rs.supplier_id <> :s "
        "ORDER BY s.supplier_name LIMIT 1", r=rfq["id"], s=current_supplier_id)
    return row["supplier_id"] if row else None


FLOW_STEPS = ("REF", "REF approval", "RFQ", "Quotes", "PO approval", "Purchase order", "Receiving")


def ref_flow(ref, role=None):
    """Where a REF is on its way from request to goods: the seven steps, which are done, and one
    sentence saying what to do next (worded for the viewer's role). None for a cancelled REF.

    The owner decides twice: first whether to buy at all (REF approval), then which supplier
    gets the order (PO approval). The warehouse manager does everything in between."""
    status = ref["status"]
    if status == "cancelled":
        return None
    owner = role == "owner"
    live_pos = [p for p in ref.get("pos", []) if p["status"] != "cancelled"]
    if status == "draft":
        cur, nxt = 0, "Sign and submit this REF."
    elif status == "pending_owner":
        cur = 1
        nxt = ("Approve or reject this replenishment form. The RFQ goes out after you approve."
               if owner else "Waiting for the owner to approve this REF. You can send the RFQ once they do.")
    elif status == "closed":
        cur, nxt = len(FLOW_STEPS), "Everything has been received."
    elif status == "receiving":
        cur, nxt = 6, "Goods are arriving. Record each delivery under Supplier receiving."
    elif status == "approved":
        if any(p["status"] == "draft" for p in live_pos):
            cur, nxt = 5, "Send the purchase order to the supplier."
        else:
            cur, nxt = 5, "Wait for the supplier's delivery, then record it under Supplier receiving."
    elif status == "for_approval":
        cur = 4
        nxt = ("Review the suppliers chosen and decide on each one." if owner
               else "The owner is deciding which suppliers get the order. You will be notified.")
    elif not ref.get("rfq"):
        cur, nxt = 2, "Approved. Send an RFQ to suppliers, or choose a regular supplier's agreed price."
    else:
        replied = any(r["replied_at"] for r in ref.get("rfq_rows", []))
        if ref.get("has_open"):
            cur = 3
            nxt = ("Choose suppliers, or record more replies first." if replied
                   else "Send the RFQ, then record each supplier's reply.")
        else:
            cur, nxt = 4, "Waiting for the owner."
    steps = [{"label": label,
              "state": "done" if i < cur else "current" if i == cur else "todo"}
             for i, label in enumerate(FLOW_STEPS)]
    return {"steps": steps, "next": nxt}


def _ref_rfq(ref_id):
    return _b().one("SELECT * FROM rfqs WHERE ref_id = :r ORDER BY id DESC LIMIT 1", r=ref_id)


def _latest_quote_for_supplier(ref_id, supplier_id):
    """The supplier's newest reply on this REF, across every RFQ round."""
    return _b().one(
        "SELECT q.*, rq.rfq_no FROM quotations q JOIN rfqs rq ON rq.id = q.rfq_id "
        "WHERE rq.ref_id = :r AND q.supplier_id = :s ORDER BY q.id DESC LIMIT 1",
        r=ref_id, s=supplier_id)


def quotation_form_data(ref_id, user, supplier_id=None):
    """Everything the quotation screen needs.

    The supplier list holds every active supplier, with the ones the RFQ was
    sent to marked first: a supplier can reply even when the warehouse forgot
    to put them on the RFQ, and saving that quotation adds them to it.
    ``supplier_prices`` maps supplier -> item -> agreed price valid today, so
    the unit-price boxes can be filled instead of typed one by one.
    ``existing_items`` holds the supplier's newest saved reply on this REF
    (whatever RFQ round it came from) when the page was opened for one supplier.
    """
    ref = _ref_for_write(user, ref_id)
    rfq = _ref_rfq(ref_id)
    suppliers = []
    if rfq:
        suppliers = _b().all(
            "SELECT s.id, s.supplier_name, s.is_regular, "
            "CASE WHEN rs.supplier_id IS NULL THEN 0 ELSE 1 END AS on_rfq "
            "FROM suppliers s LEFT JOIN rfq_suppliers rs "
            "ON rs.rfq_id = :r AND rs.supplier_id = s.id "
            "WHERE s.is_active "
            "ORDER BY CASE WHEN rs.supplier_id IS NULL THEN 1 ELSE 0 END, s.supplier_name",
            r=rfq["id"])
    selected = None
    try:
        selected = int(str(supplier_id).strip())
    except (TypeError, ValueError):
        pass
    if selected is not None and not any(s["id"] == selected for s in suppliers):
        selected = None
    if selected is None:
        selected = next((s["id"] for s in suppliers if s["on_rfq"]), None)

    items = _b().all(
        "SELECT ri.id AS ref_item_id, ri.item_id, ri.qty_requested, i.item_code, i.item_name "
        "FROM replenishment_items ri JOIN items i ON i.id = ri.item_id "
        "WHERE ri.ref_id = :r ORDER BY i.item_name", r=ref_id)

    supplier_prices = {}
    for row in _b().all(
            "SELECT si.supplier_id, si.item_id, si.agreed_price FROM supplier_items si "
            "WHERE si.valid_from <= :d AND (si.valid_to IS NULL OR si.valid_to >= :d) "
            "AND si.supplier_id IN (SELECT id FROM suppliers WHERE is_active) "
            "AND si.item_id IN (SELECT item_id FROM replenishment_items WHERE ref_id = :r) "
            "ORDER BY si.supplier_id, si.item_id, si.valid_from DESC",
            d=_today().isoformat(), r=ref_id):
        supplier_prices.setdefault(str(row["supplier_id"]), {}).setdefault(
            str(row["item_id"]), _price_text(row["agreed_price"]))

    existing = None
    existing_items = {}
    loaded_from = None
    if rfq and selected:
        existing = _latest_quote_for_supplier(ref_id, selected)
        if existing:
            # The newest reply may belong to an earlier RFQ round. Load it for editing;
            # saving records it under the current RFQ and supersedes the older lines.
            if existing["rfq_id"] != rfq["id"]:
                loaded_from = existing["rfq_no"]
            for qi in _b().all("SELECT * FROM quotation_items WHERE quotation_id = :q "
                               "AND superseded_at IS NULL", q=existing["id"]):
                qi["unit_price"] = _price_text(qi["unit_price"])
                existing_items[qi["replenishment_item_id"]] = qi
    # Show only what still needs a supplier (an item already ordered or dropped needs no new quotation),
    # but keep any line this supplier's saved quotation already contains.
    still_open = {c["ref_item_id"]: c["open"] for c in cov.coverage(ref_id)}
    items = [dict(it, qty_requested=still_open.get(it["ref_item_id"], 0) or it["qty_requested"])
             for it in items
             if still_open.get(it["ref_item_id"], 0) > 0 or it["ref_item_id"] in existing_items]
    return {"ref": ref, "rfq": rfq, "suppliers": suppliers, "ref_items": items,
            "supplier_prices": supplier_prices, "selected_supplier_id": selected,
            "existing": existing, "existing_items": existing_items, "today": _today().isoformat(),
            "loaded_from_rfq": loaded_from}


def _ref_items(ref_id):
    return _b().all(
        "SELECT ri.id AS ref_item_id, ri.item_id, ri.qty_requested, i.item_name "
        "FROM replenishment_items ri JOIN items i ON i.id = ri.item_id "
        "WHERE ri.ref_id = :r ORDER BY i.item_name", r=ref_id)


def save_quotation(user, ref_id, supplier_id, quotation_ref, quotation_date, valid_until, notes, lines):
    """Record or replace one supplier's quotation for this REF's RFQ. Returns (quotation_id, supplier_name)."""
    ref = _ref_for_write(user, ref_id)
    rfq = _ref_rfq(ref_id)
    if rfq is None:
        raise InventoryError("This REF has no RFQ yet. Create the RFQ first.")
    if ref["status"] not in ("submitted", "rfq_sent"):
        raise InventoryError("This REF is %s, so no quotation can be recorded now."
                             % ref["status"].replace("_", " "))
    if rfq["status"] != "sent":
        raise InventoryError("This RFQ is %s, so no quotation can be recorded." % rfq["status"])
    try:
        sid = int(str(supplier_id).strip())
    except (TypeError, ValueError):
        raise InventoryError("Choose the supplier who replied.")
    supplier = _b().one("SELECT id, supplier_name FROM suppliers WHERE id = :s AND is_active", s=sid)
    if supplier is None:
        raise InventoryError("Choose an active supplier.")
    on_rfq = _b().one("SELECT 1 AS x FROM rfq_suppliers WHERE rfq_id = :r AND supplier_id = :s",
                      r=rfq["id"], s=sid)
    if on_rfq is None:
        # The supplier replied even though the RFQ was not recorded as sent to
        # them (for example, they were emailed outside the system). Keep the
        # quotation and add them to the RFQ instead of refusing it.
        _b().execute("INSERT INTO rfq_suppliers (rfq_id, supplier_id, sent_at) "
                     "VALUES (:r, :s, NULL) ON CONFLICT (rfq_id, supplier_id) DO NOTHING",
                     r=rfq["id"], s=sid)
    qdate = _date(quotation_date, "Quotation date", required=True)
    valid = _date(valid_until, "Valid until")
    if valid and valid < qdate:
        raise InventoryError("Valid until cannot be before the quotation date.")
    quotation_ref = (quotation_ref or "").strip()
    if len(quotation_ref) > 50:
        raise InventoryError("The supplier quotation reference can be at most 50 characters.")
    notes = (notes or "").strip()
    if len(notes) > 500:
        raise InventoryError("Notes can be at most 500 characters.")

    ref_items = {r["ref_item_id"]: r for r in _ref_items(ref_id)}
    rows = []
    for ref_item_id, qty_raw, price_raw, lead_raw in lines:
        if not str(ref_item_id).strip():
            continue
        rid = _whole(ref_item_id, "Line")
        if rid not in ref_items:
            raise InventoryError("A line does not belong to this REF.")
        qty = _whole(qty_raw or "0", "Quantity offered")
        if qty == 0:
            continue
        item_name = ref_items[rid]["item_name"]
        try:
            price = _money(price_raw, "%s: unit price" % item_name)
        except InventoryError:
            raise InventoryError("%s: enter the unit price %s quoted. If they did not offer this item, "
                                 "set its quantity to 0." % (item_name, supplier["supplier_name"]))
        lead = None
        if str(lead_raw).strip():
            lead = _whole(lead_raw, "%s: lead time" % ref_items[rid]["item_name"])
        rows.append({"ref_item_id": rid, "item_id": ref_items[rid]["item_id"],
                     "qty": qty, "unit_price": price, "lead": lead})
    if not rows:
        raise InventoryError("Enter at least one item with a quantity above zero and a unit price.")
    total = _total_text(rows)

    existing = _b().one("SELECT id FROM quotations WHERE rfq_id = :r AND supplier_id = :s",
                        r=rfq["id"], s=sid)
    if existing:
        quotation_id = existing["id"]
        _b().execute(
            "UPDATE quotations SET quotation_ref = :ref, quotation_date = :d, valid_until = :v, "
            "total_amount = :t, notes = :n, recorded_by = :u, recorded_at = CURRENT_TIMESTAMP "
            "WHERE id = :i", ref=quotation_ref or None, d=qdate.isoformat(),
            v=valid.isoformat() if valid else None, t=total, n=notes or None,
            u=user["id"], i=quotation_id)
        _b().execute("DELETE FROM quotation_items WHERE quotation_id = :q", q=quotation_id)
        action = "update"
    else:
        row = _b().one(
            "INSERT INTO quotations (rfq_id, supplier_id, quotation_ref, quotation_date, valid_until, "
            "total_amount, notes, recorded_by) VALUES (:r, :s, :ref, :d, :v, :t, :n, :u) RETURNING id",
            r=rfq["id"], s=sid, ref=quotation_ref or None, d=qdate.isoformat(),
            v=valid.isoformat() if valid else None, t=total, n=notes or None, u=user["id"])
        quotation_id = row["id"]
        action = "create"
    for r in rows:
        _b().execute(
            "INSERT INTO quotation_items (quotation_id, replenishment_item_id, item_id, qty_offered, "
            "unit_price, lead_time_days) VALUES (:q, :ri, :i, :qty, :p, :lead)",
            q=quotation_id, ri=r["ref_item_id"], i=r["item_id"], qty=r["qty"],
            p=r["unit_price"], lead=r["lead"])
    _supersede_older_lines(ref_id, sid, quotation_id)
    _b().execute("UPDATE rfq_suppliers SET replied_at = CURRENT_TIMESTAMP "
                 "WHERE rfq_id = :r AND supplier_id = :s", r=rfq["id"], s=sid)
    inv.write_audit(user["id"], action, "quotations", quotation_id,
                    new={"supplier": supplier["supplier_name"], "ref_no": ref["ref_no"], "total": total})
    return quotation_id, supplier["supplier_name"]


def _supersede_older_lines(ref_id, supplier_id, quotation_id):
    """The items in the new reply replace this supplier's older lines for the same items.

    A second RFQ round can re-quote an item. Leaving the round-1 line live would let a
    stale price compete with the new one on Choose suppliers, so it is marked superseded.
    Lines for items this reply does not include stay valid: they are still the supplier's
    last word on those items. The older quotation totals are recomputed to match.
    """
    _b().execute(
        "UPDATE quotation_items SET superseded_at = CURRENT_TIMESTAMP "
        "WHERE superseded_at IS NULL AND quotation_id <> :q "
        "AND replenishment_item_id IN (SELECT replenishment_item_id FROM quotation_items WHERE quotation_id = :q) "
        "AND quotation_id IN (SELECT oq.id FROM quotations oq JOIN rfqs rq ON rq.id = oq.rfq_id "
        "                      WHERE rq.ref_id = :r AND oq.supplier_id = :s)",
        q=quotation_id, r=ref_id, s=supplier_id)
    _b().execute(
        "UPDATE quotations SET total_amount = COALESCE((SELECT SUM(qi.qty_offered * qi.unit_price) "
        "FROM quotation_items qi WHERE qi.quotation_id = quotations.id AND qi.superseded_at IS NULL), 0) "
        "WHERE id IN (SELECT oq.id FROM quotations oq JOIN rfqs rq ON rq.id = oq.rfq_id "
        "             WHERE rq.ref_id = :r AND oq.supplier_id = :s AND oq.id <> :q)",
        q=quotation_id, r=ref_id, s=supplier_id)


# ---------------------------------------------------------------------
# Choose supplier -> owner approval
# ---------------------------------------------------------------------

def _valid_agreed_price(supplier_id, item_id, on_date=None):
    on_date = (on_date or _today()).isoformat()
    return _b().one(
        "SELECT agreed_price, valid_from, valid_to FROM supplier_items "
        "WHERE supplier_id = :s AND item_id = :i AND valid_from <= :d "
        "AND (valid_to IS NULL OR valid_to >= :d) ORDER BY valid_from DESC LIMIT 1",
        s=supplier_id, i=item_id, d=on_date)


def _annotate_offers(options, need):
    """Plain numbers and medals for comparing the offers on one line, in place. Display only: it
    never changes which offer is pre-selected.

    * rank 1: the lowest unit price (equal lowest prices share it). Only given when there are at
      least two different prices to compare. There is no 2nd or 3rd place.
    * fastest: the shortest stated lead time, only when the offers state different lead times.
    * covers / short / total: whether the offer covers the quantity needed, and what that costs.
    * above: how much more than the lowest price it is.
    Offers are listed cheapest first, expired ones last."""
    usable = [o for o in options if not o["expired"]]
    prices = sorted({_dec(o["unit_price"]) for o in usable})
    lowest = prices[0] if len(prices) > 1 else None
    leads = [o["lead"] for o in usable if o["lead"] is not None]
    best_lead = min(leads) if len(set(leads)) > 1 else None
    low = prices[0] if prices else None
    for o in options:
        price = _dec(o["unit_price"])
        o["covers"] = o["max_qty"] >= need
        o["short"] = max(0, need - o["max_qty"])
        o["supplies"] = min(o["max_qty"], need)
        o["total"] = _price_text(_amount(o["supplies"], o["unit_price"]))
        o["rank"] = 1 if (lowest is not None and not o["expired"] and price == lowest) else None
        o["only_offer"] = (not o["expired"]) and len(usable) == 1
        o["fastest"] = (not o["expired"]) and best_lead is not None and o["lead"] == best_lead
        if low is not None and not o["expired"] and price > low:
            o["above"] = _price_text(price - low)
            o["above_pct"] = int(((price - low) / low * 100).quantize(Decimal("1"), rounding=ROUND_HALF_UP))
        else:
            o["above"] = o["above_pct"] = None
    options.sort(key=lambda o: (o["expired"], _dec(o["unit_price"]), o["supplier_name"]))


def _attach_comparison(row):
    """For the owner: how each price chosen on this approval compares with every other offer on
    the same REF line (quotations and agreed prices), so a dearer choice is visible at a glance."""
    if not row["lines"]:
        return
    try:
        probed = _line_options(row["ref_id"], [{"ref_item_id": l["ref_item_id"], "item_id": l["item_id"],
                                                "open": l["qty"]} for l in row["lines"]])
    except InventoryError:
        return
    for line, p in zip(row["lines"], probed):
        paid = _price_text(line["unit_price"])
        mine = next((o for o in p["options"]
                     if o["supplier_id"] == row["supplier_id"] and o["unit_price"] == paid), None)
        others = [o for o in p["options"] if not o["expired"] and o["supplier_id"] != row["supplier_id"]]
        line["compare"] = {
            "mine": mine,
            "n_offers": len(others) + (1 if mine else 0),
            "cheaper": [o for o in others if _dec(o["unit_price"]) < _dec(paid)][:3],
        }


def _line_options(ref_id, lines):
    """For each REF line that still needs a supplier: who offered it, how many, at what price."""
    today = _today()
    quotes = {}
    unpriced = {}
    for row in _b().all(
            "SELECT qi.replenishment_item_id AS rid, qi.qty_offered, qi.unit_price, qi.lead_time_days, "
            "q.id AS quotation_id, q.valid_until, s.id AS supplier_id, s.supplier_name, s.is_regular "
            "FROM quotation_items qi JOIN quotations q ON q.id = qi.quotation_id "
            "JOIN rfqs rq ON rq.id = q.rfq_id JOIN suppliers s ON s.id = q.supplier_id "
            "WHERE rq.ref_id = :r AND s.is_active AND qi.qty_offered > 0 "
            "AND qi.superseded_at IS NULL ORDER BY s.supplier_name", r=ref_id):
        if _dec(row["unit_price"]) <= 0:
            # A reply stored before prices were required to be above zero: it is not an offer.
            unpriced.setdefault(row["rid"], []).append(row["supplier_name"])
            continue
        valid = _date(row["valid_until"], "Valid until") if row.get("valid_until") else None
        quotes.setdefault(row["rid"], []).append({
            "key": "q:%d" % row["quotation_id"], "kind": "quotation", "supplier_id": row["supplier_id"],
            "supplier_name": row["supplier_name"], "max_qty": row["qty_offered"],
            "unit_price": _price_text(row["unit_price"]), "lead": row["lead_time_days"],
            "valid_until": str(valid) if valid else None, "regular": bool(row.get("is_regular")),
            "expired": bool(valid and valid < today)})
    regulars = _b().all("SELECT id, supplier_name FROM suppliers WHERE is_active AND is_regular "
                        "ORDER BY supplier_name")
    for ln in lines:
        options = list(quotes.get(ln["ref_item_id"], []))
        active_quoted = {o["supplier_id"] for o in options if not o["expired"]}
        replaced = set()
        for sup in regulars:
            if sup["id"] in active_quoted:
                continue                       # one offer per supplier: a live quotation wins
            price = _valid_agreed_price(sup["id"], ln["item_id"])
            if price is not None and _dec(price["agreed_price"]) > 0:
                options.append({"key": "a:%d" % sup["id"], "kind": "agreed_price", "supplier_id": sup["id"],
                                "supplier_name": sup["supplier_name"] + " (agreed price)",
                                "max_qty": ln["open"], "unit_price": _price_text(price["agreed_price"]),
                                "lead": None, "valid_until": None, "regular": True, "expired": False})
                replaced.add(sup["id"])
        if replaced:
            # An expired quotation is no offer any more: show the agreed price in its place.
            options = [o for o in options
                       if not (o["kind"] == "quotation" and o["expired"] and o["supplier_id"] in replaced)]
        usable = [o for o in options if not o["expired"]]
        full = [o for o in usable if o["max_qty"] >= ln["open"]]
        if full:
            pick = min(full, key=lambda o: (Decimal(o["unit_price"]), o["supplier_name"]))
        elif usable:
            pick = max(usable, key=lambda o: o["max_qty"])
        else:
            pick = None
        _annotate_offers(options, ln["open"])
        ln["options"] = options
        ln["unpriced"] = unpriced.get(ln["ref_item_id"], [])
        ln["default"] = pick["key"] if pick else "open"
        ln["qty_default"] = min(pick["max_qty"], ln["open"]) if pick else ln["open"]
        ln["subtotal"] = (_price_text(_amount(ln["qty_default"], pick["unit_price"])) if pick else None)
    return lines


def _split_name(supplier_name, kinds):
    """Keep the agreed-price tag when it applies, and explain a supplier whose lines mix bases."""
    base = supplier_name.replace(" (agreed price)", "")
    if kinds == {"agreed_price"}:
        return base + " (agreed price)"
    if len(kinds) > 1:
        return base + " (quotation + agreed price)"
    return base


def _default_split(lines):
    """Group the pre-selected offer of each line by supplier, for the split preview."""
    groups = {}
    for ln in lines:
        opt = next((o for o in ln["options"] if o["key"] == ln["default"]), None)
        if ln["default"] == "open" or opt is None:
            continue
        g = groups.setdefault(opt["supplier_id"],
                              {"supplier_name": opt["supplier_name"], "lines": [], "kinds": set()})
        g["kinds"].add(opt["kind"])
        g["lines"].append({"name": ln["item_name"], "qty": min(opt["max_qty"], ln["open"]),
                           "unit_price": opt["unit_price"]})
    out = []
    for g in groups.values():
        g["total"] = _total_text(g["lines"])
        g["supplier_name"] = _split_name(g["supplier_name"], g["kinds"])
        out.append(g)
    out.sort(key=lambda g: g["supplier_name"].lower())
    return out


def _bulk_suppliers(lines):
    """Per supplier: how many open items they can cover and the total at their max quantities."""
    groups = {}
    for ln in lines:
        best = {}
        for o in ln["options"]:
            if o["expired"]:
                continue
            cur = best.get(o["supplier_id"])
            if cur is None or _dec(o["unit_price"]) < _dec(cur["unit_price"]):
                best[o["supplier_id"]] = o
        for sid, o in best.items():
            g = groups.setdefault(sid, {"supplier_id": sid,
                                        "supplier_name": o["supplier_name"].replace(" (agreed price)", ""),
                                        "covered": 0, "total": Decimal("0")})
            g["covered"] += 1
            g["total"] += _amount(min(o["max_qty"], ln["open"]), o["unit_price"])
    out = list(groups.values())
    for g in out:
        g["total"] = str(g["total"].quantize(Decimal("0.01")))
    out.sort(key=lambda g: g["supplier_name"].lower())
    return out


def compare_data(ref_id, user):
    """Everything the 'choose suppliers' screen needs: each line still to order, with every offer."""
    ref = _ref_for_write(user, ref_id)
    if ref["status"] == "pending_owner":
        raise InventoryError("The owner has not approved this REF yet, so suppliers cannot be chosen.")
    coverage_lines = cov.coverage(ref_id)
    open_ = _line_options(ref_id, [dict(c) for c in coverage_lines if c["open"] > 0])
    done = [c for c in coverage_lines if c["open"] == 0]
    return {"ref": ref, "lines": open_, "done": done, "groups": _default_split(open_),
            "has_medals": any(o["rank"] for l in open_ for o in l["options"]),
            "bulk": _bulk_suppliers(open_), "bulk_items": len(open_),
            "pending": _b().one("SELECT COUNT(*) AS n FROM purchase_approvals WHERE ref_id = :r "
                                "AND decision = 'pending'", r=ref_id)["n"] > 0}


def submit_choices(user, ref_id, choices):
    """Send the owner one approval per supplier, all together.

    choices: {ref_item_id: (choice, qty)} for every line that still needs a supplier.
      choice  'q:<quotation id>'  order from that quotation
              'a:<supplier id>'   order at a regular supplier's agreed price
              'open'              keep open: order from someone else later

    Lines that go to the same supplier are one approval and later one PO, even when
    they come from quotations recorded in different RFQ rounds.
    Returns (first_approval_id, ref_no, number_of_approvals).
    """
    ref = _ref_for_write(user, ref_id)
    if _b().one("SELECT id FROM purchase_approvals WHERE ref_id = :r AND decision = 'pending'", r=ref_id):
        raise InventoryError("This REF is already waiting for the owner's decision.")
    if ref["status"] not in ("submitted", "rfq_sent"):
        raise InventoryError("This REF is %s, so it cannot be sent for approval."
                             % ref["status"].replace("_", " "))
    lines = _line_options(ref_id, [dict(c) for c in cov.coverage(ref_id) if c["open"] > 0])
    if not lines:
        raise InventoryError("Every item on this REF is already ordered or dropped.")
    groups = {}
    for ln in lines:
        name = ln["item_name"]
        choice, qty_raw = choices.get(ln["ref_item_id"], ("", ""))
        choice = str(choice or "").strip()
        if not choice:
            raise InventoryError("%s: choose a supplier or keep it open." % name)
        if choice == "open":
            continue
        option = next((o for o in ln["options"] if o["key"] == choice), None)
        if option is None:
            raise InventoryError("%s: that supplier did not offer this item." % name)
        if option["expired"]:
            raise InventoryError("%s: the quotation from %s has expired. Record a fresh quotation first."
                                 % (name, option["supplier_name"]))
        qty = _whole(qty_raw, "%s: quantity" % name) if str(qty_raw or "").strip() else min(option["max_qty"], ln["open"])
        top = min(option["max_qty"], ln["open"])
        if qty < 1 or qty > top:
            raise InventoryError("%s: order between 1 and %d from %s." % (name, top, option["supplier_name"]))
        kind, _, ident = choice.partition(":")
        group = groups.setdefault(option["supplier_id"],
                                  {"rows": [], "quotation_ids": set(), "kinds": set()})
        group["rows"].append({"ln": ln, "qty": qty, "price": option["unit_price"]})
        group["kinds"].add(option["kind"])
        if kind == "q":
            group["quotation_ids"].add(int(ident))
    if not groups:
        raise InventoryError("Choose a supplier for at least one item. To stop ordering this REF, cancel it instead.")

    first_id, n = None, 0
    for sid, group in groups.items():
        qids, kinds = group["quotation_ids"], group["kinds"]
        if kinds == {"agreed_price"}:
            basis, qid = "agreed_price", None
        elif kinds == {"quotation"}:
            basis = "quotation"
            qid = next(iter(qids)) if len(qids) == 1 else None
        else:
            # One supplier can pay quotation prices for some lines and agreed prices for others;
            # they still get one approval and one PO.
            basis, qid = "mixed", None
        row = _b().one(
            "INSERT INTO purchase_approvals (ref_id, supplier_id, basis, quotation_id, decision, submitted_by) "
            "VALUES (:r, :s, :b, :q, 'pending', :u) RETURNING id", r=ref_id, s=sid, b=basis, q=qid, u=user["id"])
        first_id = first_id or row["id"]
        n += 1
        _b().execute("UPDATE purchase_approvals SET batch_id = :b WHERE id = :i", b=first_id, i=row["id"])
        for r in group["rows"]:
            _b().execute(
                "INSERT INTO purchase_approval_items (approval_id, replenishment_item_id, item_id, qty, unit_price) "
                "VALUES (:a, :ri, :i, :q, :p)", a=row["id"], ri=r["ln"]["ref_item_id"], i=r["ln"]["item_id"],
                q=r["qty"], p=r["price"])
        supplier = _b().one("SELECT supplier_name FROM suppliers WHERE id = :i", i=sid)["supplier_name"]
        inv.write_audit(user["id"], "submit", "purchase_approvals", row["id"],
                        new={"ref_no": ref["ref_no"], "supplier": supplier, "basis": basis,
                             "lines": len(group["rows"])})
    cov.refresh_status(ref_id)
    return first_id, ref["ref_no"], n


def _check_drop_reason(name, reason):
    reason = (reason or "").strip()
    if not reason:
        raise InventoryError("%s: write why you are dropping it." % name)
    if len(reason) < 5 or reason.lower() in ("drop", "dropped", "n/a", "na", "none", "no", "x"):
        raise InventoryError("%s: write a real reason (at least 5 characters), so the owner knows why it was given up." % name)
    if len(reason) > 200:
        raise InventoryError("%s: the reason can be at most 200 characters." % name)
    return reason


def _drop(user, ref, ln, reason):
    """Give up on the quantity of this line that still has no supplier.

    Dropping must never work like dismissing the need: if the item is still at or below its reorder
    point, a fresh restock alert is raised, and a shop RF that is still waiting for it is reported.
    Returns a list of notes for the user.
    """
    _b().execute("UPDATE replenishment_items SET qty_dropped = qty_dropped + :q, dropped_reason = :r WHERE id = :i",
                 q=ln["open"], r=reason, i=ln["ref_item_id"])
    inv.write_audit(user["id"], "drop", "replenishment_items", ln["ref_item_id"],
                    new={"ref_no": ref["ref_no"], "item": ln["item_name"], "qty": ln["open"], "reason": reason})
    notes = []
    inv_ops.refresh_alerts(ref["warehouse_id"], [ln["item_id"]])
    if any(r["item_id"] == ln["item_id"] for r in inv.items_below_rop(ref["warehouse_id"])):
        notes.append("%s is still below its reorder point, so it shows again in the warehouse alerts." % ln["item_name"])
    rf = _b().one("SELECT r.rf_no FROM replenishment_items ri JOIN requisitions r ON r.id = ri.rf_id "
                  "WHERE ri.id = :i", i=ln["ref_item_id"])
    if rf:
        notes.append("%s is still waiting for %s. It will not be fulfilled unless you order it in another REF."
                     % (rf["rf_no"], ln["item_name"]))
    return notes


def restore_dropped(user, ref_id, ref_item_id):
    """Bring a given-up item back: it needs a supplier again. Not possible once the REF is closed."""
    ref = _ref_for_write(user, ref_id)
    if ref["status"] in ("draft", "closed", "cancelled"):
        raise InventoryError("This REF is %s, so a dropped item cannot be restored. Create a new REF for it."
                             % ref["status"])
    try:
        rid = int(str(ref_item_id).strip())
    except (TypeError, ValueError):
        raise InventoryError("Choose the item to restore.")
    line = next((c for c in cov.coverage(ref_id) if c["ref_item_id"] == rid), None)
    if line is None:
        raise InventoryError("That item does not belong to this REF.")
    if line["qty_dropped"] <= 0:
        raise InventoryError("%s was not dropped." % line["item_name"])
    _b().execute("UPDATE replenishment_items SET qty_dropped = 0, dropped_reason = NULL WHERE id = :i", i=rid)
    inv.write_audit(user["id"], "restore", "replenishment_items", rid,
                    old={"qty_dropped": line["qty_dropped"], "reason": line["dropped_reason"]},
                    new={"ref_no": ref["ref_no"], "item": line["item_name"]})
    cov.refresh_status(ref_id)
    return ref["ref_no"], line["item_name"]


def drop_open_lines(user, ref_id, drops):
    """Warehouse manager gives up on items nobody can supply. drops: [(ref_item_id, reason)]."""
    ref = _ref_for_write(user, ref_id)
    if ref["status"] not in ("submitted", "rfq_sent"):
        raise InventoryError("This REF is %s, so its items cannot be dropped now." % ref["status"].replace("_", " "))
    if _b().one("SELECT id FROM purchase_approvals WHERE ref_id = :r AND decision = 'pending'", r=ref_id):
        raise InventoryError("The owner is deciding this REF. Wait for the decision first.")
    by_id = {c["ref_item_id"]: c for c in cov.coverage(ref_id)}
    done = 0
    notes = []
    for rid, reason in drops:
        try:
            ln = by_id[int(str(rid).strip())]
        except (KeyError, ValueError):
            raise InventoryError("A line does not belong to this REF.")
        if ln["open"] <= 0:
            raise InventoryError("%s has nothing left to drop." % ln["item_name"])
        reason = _check_drop_reason(ln["item_name"], reason)
        notes += _drop(user, ref, ln, reason)
        done += 1
    if not done:
        raise InventoryError("Choose an item to drop.")
    has_po = _b().one("SELECT COUNT(*) AS n FROM purchase_orders WHERE ref_id = :r AND status <> 'cancelled'",
                      r=ref_id)["n"]
    left = _b().one("SELECT COALESCE(SUM(qty_requested - qty_dropped), 0) AS n FROM replenishment_items WHERE ref_id = :r",
                    r=ref_id)["n"]
    if not has_po and left <= 0:
        raise InventoryError("Nothing would be left to order. Cancel the REF instead.")
    cov.refresh_status(ref_id)
    drop_open_lines.notes = notes
    return ref["ref_no"], done


# ---------------------------------------------------------------------
# Approvals (owner)
# ---------------------------------------------------------------------

def approval_lines(appr):
    """What the owner is approving: qty, unit price and amount per REF line."""
    own = _b().all(
        "SELECT pai.replenishment_item_id AS ref_item_id, pai.item_id, pai.qty, pai.unit_price, i.item_name "
        "FROM purchase_approval_items pai JOIN items i ON i.id = pai.item_id "
        "WHERE pai.approval_id = :a ORDER BY i.item_name", a=appr["id"])
    if own:
        for line in own:
            line["amount"] = str(_amount(line["qty"], line["unit_price"]).quantize(Decimal("0.01")))
        return own
    # Older approvals (made before split ordering) work out their lines from the quotation or agreed prices.
    items = _ref_items(appr["ref_id"])
    out = []
    if appr["basis"] == "quotation":
        qitems = _b().all("SELECT * FROM quotation_items WHERE quotation_id = :q", q=appr["quotation_id"])
        by_line = {q["replenishment_item_id"]: q for q in qitems}
        for ri in items:
            qi = by_line.get(ri["ref_item_id"])
            if qi is None or qi["qty_offered"] <= 0:
                continue
            out.append({"ref_item_id": ri["ref_item_id"], "item_id": ri["item_id"],
                        "item_name": ri["item_name"], "qty": qi["qty_offered"],
                        "unit_price": qi["unit_price"]})
    else:
        for ri in items:
            price = _valid_agreed_price(appr["supplier_id"], ri["item_id"])
            if price is None:
                raise InventoryError("No valid agreed price for %s any more." % ri["item_name"])
            out.append({"ref_item_id": ri["ref_item_id"], "item_id": ri["item_id"],
                        "item_name": ri["item_name"], "qty": ri["qty_requested"],
                        "unit_price": price["agreed_price"]})
    for line in out:
        line["amount"] = str(_amount(line["qty"], line["unit_price"]).quantize(Decimal("0.01")))
    return out


def _approval_total(appr):
    try:
        return _total_text(approval_lines(appr))
    except InventoryError:
        return None


def list_approvals(user, decision="", q="", page=1):
    where, p = "", {}
    if user["role"] != "owner":
        where += " AND r.warehouse_id = :loc"
        p["loc"] = user["location_id"]
    if decision in ("pending", "approved", "rejected"):
        where += " AND pa.decision = :d"
        p["d"] = decision
    if q.strip():
        where += " AND (LOWER(r.ref_no) LIKE :q OR LOWER(s.supplier_name) LIKE :q)"
        p["q"] = _like(q)
    frm = ("FROM purchase_approvals pa JOIN replenishments r ON r.id = pa.ref_id "
           "JOIN suppliers s ON s.id = pa.supplier_id JOIN users u ON u.id = pa.submitted_by "
           "WHERE 1 = 1" + where)
    cols = ("SELECT pa.id, pa.decision, pa.basis, pa.submitted_at, r.ref_no, r.status AS ref_status, "
            "s.supplier_name, u.full_name AS submitted_by_name ")
    res = _paged("SELECT COUNT(*) AS n " + frm,
                 cols + frm + " ORDER BY CASE pa.decision WHEN 'pending' THEN 0 ELSE 1 END, pa.id DESC",
                 page, **p)
    for row in res["rows"]:
        appr = _b().one("SELECT * FROM purchase_approvals WHERE id = :i", i=row["id"])
        row["total"] = _approval_total(appr)
    return res


def _suggested_expected(rows):
    """The latest quotation date + lead time across the pending suppliers, not in the past.

    Goods cannot arrive before the slowest supplier has delivered, and the owner sets one
    date for every PO in the batch, so the latest suggestion wins. Only quotation-based
    suppliers carry a lead time; agreed prices have none.
    """
    best = None
    for row in rows:
        if not row.get("quotation_id"):
            continue
        q = _b().one(
            "SELECT q.quotation_date, MAX(qi.lead_time_days) AS lead FROM quotations q "
            "LEFT JOIN quotation_items qi ON qi.quotation_id = q.id AND qi.superseded_at IS NULL "
            "WHERE q.id = :q GROUP BY q.quotation_date", q=row["quotation_id"])
        if q is None or q["lead"] is None:
            continue
        d = _date(q["quotation_date"], "Quotation date") + dt.timedelta(days=int(q["lead"]))
        if best is None or d > best:
            best = d
    return max(best, _today()).isoformat() if best else None


def get_approval(approval_id, user):
    appr = _b().one(
        "SELECT pa.*, r.ref_no, r.warehouse_id, r.status AS ref_status, s.supplier_name, "
        "u.full_name AS submitted_by_name, d.full_name AS decided_by_name, q.quotation_ref "
        "FROM purchase_approvals pa JOIN replenishments r ON r.id = pa.ref_id "
        "JOIN suppliers s ON s.id = pa.supplier_id JOIN users u ON u.id = pa.submitted_by "
        "LEFT JOIN users d ON d.id = pa.decided_by LEFT JOIN quotations q ON q.id = pa.quotation_id "
        "WHERE pa.id = :i", i=approval_id)
    if appr is None:
        return None
    if user["role"] != "owner":
        return None
    appr["lines"] = approval_lines(appr)
    appr["total"] = _total_text(appr["lines"])
    appr["po"] = _b().one("SELECT id, po_no, status FROM purchase_orders WHERE approval_id = :a",
                          a=approval_id)
    # every supplier in the same submission, so the owner decides them on one screen
    if appr.get("batch_id"):
        ids = [r["id"] for r in _b().all("SELECT id FROM purchase_approvals WHERE batch_id = :b ORDER BY id",
                                         b=appr["batch_id"])]
    else:
        ids = [approval_id]
    appr["batch"] = []
    for i in ids:
        row = _b().one(
            "SELECT pa.*, s.supplier_name, q.quotation_ref FROM purchase_approvals pa "
            "JOIN suppliers s ON s.id = pa.supplier_id LEFT JOIN quotations q ON q.id = pa.quotation_id "
            "WHERE pa.id = :i", i=i)
        row["lines"] = approval_lines(row)
        _attach_comparison(row)
        row["total"] = _total_text(row["lines"])
        row["po"] = _b().one("SELECT id, po_no, status FROM purchase_orders WHERE approval_id = :a", a=i)
        appr["batch"].append(row)
    appr["batch_total"] = _total_text([l for r in appr["batch"] for l in r["lines"]])
    appr["batch_pending"] = [r for r in appr["batch"] if r["decision"] == "pending"]
    appr["suggested_expected"] = _suggested_expected(appr["batch_pending"])
    coverage_lines = cov.coverage(appr["ref_id"])
    appr["open_lines"] = [c for c in coverage_lines if c["open"] > 0]
    appr["dropped_lines"] = [c for c in coverage_lines if c["qty_dropped"] > 0]
    return appr


def approval_ref_id(approval_id):
    row = _b().one("SELECT ref_id FROM purchase_approvals WHERE id = :i", i=approval_id)
    return row["ref_id"] if row else None


def can_withdraw_approval(user, appr):
    """The warehouse manager who submitted it can take it back while the owner has not decided."""
    if appr["decision"] != "pending":
        return False, "This approval was already %s." % appr["decision"]
    if (user["role"] != "warehouse_manager" or appr["warehouse_id"] != user["location_id"]
            or appr["submitted_by"] != user["id"]):
        return False, "Only the warehouse manager who submitted it can withdraw it."
    return True, ""


def withdraw_approval(user, approval_id):
    """Delete a pending approval so the REF can be priced again. Returns (ref_id, supplier_name)."""
    appr = _b().one(
        "SELECT pa.*, r.warehouse_id, s.supplier_name FROM purchase_approvals pa "
        "JOIN replenishments r ON r.id = pa.ref_id JOIN suppliers s ON s.id = pa.supplier_id "
        "WHERE pa.id = :i", i=approval_id)
    if appr is None:
        raise InventoryError("Approval not found.")
    ok, why = can_withdraw_approval(user, appr)
    if not ok:
        raise InventoryError(why)
    _b().execute("DELETE FROM purchase_approval_items WHERE approval_id = :a", a=approval_id)
    _b().execute("DELETE FROM purchase_approvals WHERE id = :a", a=approval_id)
    cov.refresh_status(appr["ref_id"])          # the withdrawn lines are open again
    inv.write_audit(user["id"], "withdraw", "purchase_approvals", approval_id,
                    old={"decision": "pending", "supplier": appr["supplier_name"],
                         "submitted_by": appr["submitted_by"]})
    return appr["ref_id"], appr["supplier_name"]


def decide_approval(user, approval_id, decision, remarks, expected_date=None, terms=None):
    """Owner approves or rejects. Approving creates the draft PO. Returns (po_no or None, decision, po_id)."""
    appr = _b().one("SELECT * FROM purchase_approvals WHERE id = :i", i=approval_id)
    if appr is None:
        raise InventoryError("Approval not found.")
    if user["role"] != "owner":
        raise InventoryError("Only the owner approves a supplier and price.")
    if appr["decision"] != "pending":
        raise InventoryError("This approval was already %s." % appr["decision"])
    decision = (decision or "").strip().lower()
    if decision not in ("approve", "reject"):
        raise InventoryError("Choose approve or reject.")
    remarks = (remarks or "").strip()
    if len(remarks) > 500:
        raise InventoryError("Remarks can be at most 500 characters.")
    if decision == "reject":
        if not remarks:
            raise InventoryError("Remarks are required when rejecting.")
        _b().execute("UPDATE purchase_approvals SET decision = 'rejected', decided_by = :u, "
                     "decided_at = CURRENT_TIMESTAMP, remarks = :rem WHERE id = :i",
                     u=user["id"], rem=remarks, i=approval_id)
        cov.refresh_status(appr["ref_id"])       # the rejected lines are open again
        inv.write_audit(user["id"], "reject", "purchase_approvals", approval_id,
                        old={"decision": "pending"}, new={"decision": "rejected", "remarks": remarks})
        return None, "rejected", None

    lines = approval_lines(appr)
    if not lines:
        raise InventoryError("There are no priced lines to order.")
    total = _total_text(lines)
    expected = _date(expected_date, "Expected delivery")
    if expected and expected < _today():
        raise InventoryError("The expected delivery date cannot be in the past.")
    terms = (terms or "").strip()
    if len(terms) > 1000:
        raise InventoryError("Terms can be at most 1000 characters.")
    po_no = inv.next_doc_no("PO")
    row = _b().one(
        "INSERT INTO purchase_orders (po_no, ref_id, supplier_id, approval_id, po_date, expected_date, "
        "terms, status, total_amount) "
        "VALUES (:no, :r, :s, :a, :d, :e, :t, 'draft', :total) RETURNING id",
        no=po_no, r=appr["ref_id"], s=appr["supplier_id"], a=approval_id,
        d=_today().isoformat(), e=expected.isoformat() if expected else None,
        t=terms or None, total=total)
    po_id = row["id"]
    for line in lines:
        _b().execute(
            "INSERT INTO purchase_order_items (po_id, replenishment_item_id, item_id, qty_ordered, unit_price) "
            "VALUES (:p, :ri, :i, :q, :price)", p=po_id, ri=line["ref_item_id"], i=line["item_id"],
            q=line["qty"], price=line["unit_price"])
    _b().execute("UPDATE purchase_approvals SET decision = 'approved', decided_by = :u, "
                 "decided_at = CURRENT_TIMESTAMP, remarks = :rem WHERE id = :i",
                 u=user["id"], rem=remarks or None, i=approval_id)
    cov.refresh_status(appr["ref_id"])        # 'approved' only when nothing is left to order
    inv.write_audit(user["id"], "approve", "purchase_approvals", approval_id,
                    old={"decision": "pending"},
                    new={"decision": "approved", "po_no": po_no, "total": total, "remarks": remarks or None})
    return po_no, "approved", po_id


def decide_batch(user, approval_id, decisions, remarks, expected_date=None, terms=None):
    """The owner decides every pending supplier of one submission at once.

    decisions: {approval_id: 'approve' | 'reject'}. Returns a list of
    (approval_id, supplier_name, result, po_no, po_id).
    """
    appr = get_approval(approval_id, user)
    if appr is None:
        raise InventoryError("Approval not found.")
    pending = appr["batch_pending"]
    if not pending:
        raise InventoryError("This approval was already decided.")
    out = []
    for row in pending:
        dec = (decisions.get(row["id"]) or "").strip()
        if not dec:
            raise InventoryError("Choose approve or reject for %s." % row["supplier_name"])
    for row in pending:
        po_no, result, po_id = decide_approval(user, row["id"], decisions[row["id"]], remarks,
                                               expected_date, terms)
        out.append((row["id"], row["supplier_name"], result, po_no, po_id))
    return out


# ---------------------------------------------------------------------
# Purchase orders (view; Delivery receives the goods)
# ---------------------------------------------------------------------

def list_pos(user, status="", q="", page=1):
    where, p = "", {}
    if user["role"] != "owner":
        where += " AND r.warehouse_id = :loc"
        p["loc"] = user["location_id"]
    if status in PO_STATUSES:
        where += " AND po.status = :st"
        p["st"] = status
    if q.strip():
        where += " AND (LOWER(po.po_no) LIKE :q OR LOWER(s.supplier_name) LIKE :q OR LOWER(r.ref_no) LIKE :q)"
        p["q"] = _like(q)
    frm = ("FROM purchase_orders po JOIN suppliers s ON s.id = po.supplier_id "
           "JOIN replenishments r ON r.id = po.ref_id WHERE 1 = 1" + where)
    cols = ("SELECT po.id, po.po_no, po.po_date, po.status, po.total_amount, s.supplier_name, r.ref_no ")
    return _paged("SELECT COUNT(*) AS n " + frm, cols + frm + " ORDER BY po.id DESC", page, **p)


def get_po(po_id, user):
    po = _b().one(
        "SELECT po.*, s.supplier_name, s.email AS supplier_email, r.ref_no, r.warehouse_id, "
        "u.full_name AS approved_by_name FROM purchase_orders po "
        "JOIN suppliers s ON s.id = po.supplier_id JOIN replenishments r ON r.id = po.ref_id "
        "JOIN purchase_approvals pa ON pa.id = po.approval_id JOIN users u ON u.id = pa.decided_by "
        "WHERE po.id = :i", i=po_id)
    if po is None:
        return None
    if user["role"] != "owner" and po["warehouse_id"] != user["location_id"]:
        return None
    po["lines"] = _b().all(
        "SELECT poi.*, i.item_code, i.item_name FROM purchase_order_items poi "
        "JOIN items i ON i.id = poi.item_id WHERE poi.po_id = :p ORDER BY i.item_name", p=po_id)
    for line in po["lines"]:
        line["amount"] = str(_amount(line["qty_ordered"], line["unit_price"]).quantize(Decimal("0.01")))
    return po


def mark_po_sent(user, po_id):
    po = _b().one("SELECT * FROM purchase_orders WHERE id = :i", i=po_id)
    if po is None:
        raise InventoryError("Purchase order not found.")
    # Owner approves, warehouse manager sends: the approver is not also the sender.
    if user["role"] != "warehouse_manager":
        raise InventoryError("Only the warehouse manager sends a PO to the supplier. The owner approves it.")
    wh = _b().one("SELECT warehouse_id FROM replenishments WHERE id = :r", r=po["ref_id"])
    if wh and user.get("location_id") and wh["warehouse_id"] != user["location_id"]:
        raise InventoryError("This PO belongs to another warehouse.")
    if po["status"] != "draft":
        raise InventoryError("This PO is already %s." % po["status"].replace("_", " "))
    _b().execute("UPDATE purchase_orders SET status = 'sent', sent_at = CURRENT_TIMESTAMP WHERE id = :i",
                 i=po_id)
    inv.write_audit(user["id"], "send", "purchase_orders", po_id,
                    old={"status": "draft"}, new={"status": "sent", "po_no": po["po_no"]})
    return po["po_no"]


def cancel_po(user, po_id, reason):
    """Cancel a draft or sent PO. Returns the PO number."""
    po = _b().one(
        "SELECT po.*, r.warehouse_id FROM purchase_orders po "
        "JOIN replenishments r ON r.id = po.ref_id WHERE po.id = :i", i=po_id)
    if po is None:
        raise InventoryError("Purchase order not found.")
    if user["role"] == "warehouse_manager":
        if po["warehouse_id"] != user["location_id"]:
            raise InventoryError("This PO belongs to another warehouse.")
    elif user["role"] != "owner":
        raise InventoryError("Only the warehouse manager or the owner can cancel a PO.")
    if po["status"] not in ("draft", "sent"):
        raise InventoryError("This PO is %s, so it can no longer be cancelled."
                             % po["status"].replace("_", " "))
    reason = (reason or "").strip()
    if not reason:
        raise InventoryError("A reason is required to cancel a purchase order.")
    if len(reason) > 300:
        raise InventoryError("The cancel reason can be at most 300 characters.")
    _b().execute("UPDATE purchase_orders SET status = 'cancelled' WHERE id = :i", i=po_id)
    inv.write_audit(user["id"], "cancel", "purchase_orders", po_id,
                    old={"status": po["status"]},
                    new={"status": "cancelled", "reason": reason, "po_no": po["po_no"]})
    cov.refresh_status(po["ref_id"])           # its lines are open again, so the REF can be ordered elsewhere
    return po["po_no"]


def update_po(user, po_id, expected_date, terms):
    """Edit a draft PO's expected delivery date and terms. Returns the PO number."""
    po = _b().one(
        "SELECT po.*, r.warehouse_id FROM purchase_orders po "
        "JOIN replenishments r ON r.id = po.ref_id WHERE po.id = :i", i=po_id)
    if po is None:
        raise InventoryError("Purchase order not found.")
    if user["role"] == "warehouse_manager":
        if po["warehouse_id"] != user["location_id"]:
            raise InventoryError("This PO belongs to another warehouse.")
    elif user["role"] != "owner":
        raise InventoryError("Only the warehouse manager or the owner can edit a PO.")
    if po["status"] != "draft":
        raise InventoryError("Only a draft PO can be edited. Use cancel and re-order instead.")
    expected = _date(expected_date, "Expected delivery")
    if expected and expected < _today():
        raise InventoryError("The expected delivery date cannot be in the past.")
    terms = (terms or "").strip()
    if len(terms) > 1000:
        raise InventoryError("Terms can be at most 1000 characters.")
    _b().execute("UPDATE purchase_orders SET expected_date = :e, terms = :t WHERE id = :i",
                 e=expected.isoformat() if expected else None, t=terms or None, i=po_id)
    inv.write_audit(user["id"], "update", "purchase_orders", po_id,
                    old={"expected_date": po["expected_date"], "terms": po["terms"]},
                    new={"expected_date": expected.isoformat() if expected else None,
                         "terms": terms or None})
    return po["po_no"]


# ---------------------------------------------------------------------
# Suppliers and agreed prices
# ---------------------------------------------------------------------

def list_suppliers(q="", show="", page=1):
    where, p = " WHERE 1 = 1", {}
    if show == "regular":
        where += " AND s.is_regular"
    elif show == "active":
        where += " AND s.is_active"
    if q.strip():
        where += (" AND (LOWER(s.supplier_name) LIKE :q OR LOWER(COALESCE(s.contact_person, '')) LIKE :q "
                  "OR LOWER(COALESCE(s.email, '')) LIKE :q)")
        p["q"] = _like(q)
    frm = "FROM suppliers s" + where
    cols = ("SELECT s.*, (SELECT COUNT(*) FROM supplier_items si WHERE si.supplier_id = s.id) AS n_prices ")
    return _paged("SELECT COUNT(*) AS n " + frm, cols + frm + " ORDER BY s.supplier_name", page, **p)


def get_supplier(supplier_id):
    head = _b().one("SELECT * FROM suppliers WHERE id = :i", i=supplier_id)
    if head is None:
        return None
    head["prices"] = _b().all(
        "SELECT si.*, i.item_code, i.item_name FROM supplier_items si JOIN items i ON i.id = si.item_id "
        "WHERE si.supplier_id = :s ORDER BY i.item_name, si.valid_from DESC", s=supplier_id)
    return head


def save_supplier(user, supplier_id, data, pairs):
    """Create or update a supplier and its agreed prices. Returns (supplier_id, name, created)."""
    name = (data.get("supplier_name") or "").strip()
    if not name:
        raise InventoryError("Supplier name is required.")
    if len(name) > 120:
        raise InventoryError("Supplier name can be at most 120 characters.")
    email = (data.get("email") or "").strip()
    if not email or "@" not in email:
        raise InventoryError("A valid email is required.")
    if len(email) > 120:
        raise InventoryError("Email can be at most 120 characters.")
    contact = (data.get("contact_person") or "").strip()
    phone = (data.get("phone") or "").strip()
    address = (data.get("address") or "").strip()
    if len(contact) > 100 or len(phone) > 30 or len(address) > 200:
        raise InventoryError("Contact, phone or address is too long.")
    is_regular = bool(data.get("is_regular"))
    is_active = bool(data.get("is_active"))

    dup = _b().one("SELECT id FROM suppliers WHERE LOWER(supplier_name) = :n", n=name.lower())
    if dup is not None and str(dup["id"]) != str(supplier_id or ""):
        raise InventoryError("A supplier with that name already exists.")

    args = dict(n=name, c=contact or None, e=email, p=phone or None, a=address or None,
                r=is_regular, act=is_active)
    if supplier_id:
        old = _b().one("SELECT * FROM suppliers WHERE id = :i", i=supplier_id)
        if old is None:
            raise InventoryError("Supplier not found.")
        _b().execute("UPDATE suppliers SET supplier_name = :n, contact_person = :c, email = :e, "
                     "phone = :p, address = :a, is_regular = :r, is_active = :act WHERE id = :i",
                     i=supplier_id, **args)
        sid = int(supplier_id)
        created = False
        old_values = {"name": old["supplier_name"], "regular": bool(old["is_regular"]),
                      "active": bool(old["is_active"])}
    else:
        row = _b().one(
            "INSERT INTO suppliers (supplier_name, contact_person, email, phone, address, is_regular, is_active) "
            "VALUES (:n, :c, :e, :p, :a, :r, :act) RETURNING id", **args)
        sid = row["id"]
        created = True
        old_values = None

    saved_prices = 0
    for item_id, price_raw, from_raw, to_raw in pairs:
        if not str(item_id).strip() and not str(price_raw).strip():
            continue
        if not str(item_id).strip():
            raise InventoryError("Select an item for every agreed price line.")
        iid = _whole(item_id, "Item")
        if _b().one("SELECT id FROM items WHERE id = :i", i=iid) is None:
            raise InventoryError("An item in the agreed price lines does not exist.")
        price = _money(price_raw, "Agreed price")
        valid_from = _date(from_raw, "Valid from") or _today()
        valid_to = _date(to_raw, "Valid to")
        if valid_to and valid_to < valid_from:
            raise InventoryError("Valid to cannot be before valid from.")
        _b().execute(
            "INSERT INTO supplier_items (supplier_id, item_id, agreed_price, valid_from, valid_to) "
            "VALUES (:s, :i, :p, :f, :t) ON CONFLICT (supplier_id, item_id, valid_from) "
            "DO UPDATE SET agreed_price = :p, valid_to = :t",
            s=sid, i=iid, p=price, f=valid_from.isoformat(), t=valid_to.isoformat() if valid_to else None)
        saved_prices += 1

    inv.write_audit(user["id"], "create" if created else "update", "suppliers", sid,
                    old=old_values, new={"name": name, "regular": is_regular, "active": is_active,
                                         "agreed_prices_saved": saved_prices})
    return sid, name, created
