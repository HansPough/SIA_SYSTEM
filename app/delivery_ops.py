"""Delivery module (Module 4): transfer slips, shop receiving (AR), supplier receiving (RM),
discrepancy reports, drivers and vehicles.

Routes in blueprints/delivery/* stay thin: read the form, call one function here, then
commit or roll back. All stock moves go through inventory_service:

  release TS   -> inv.consume_reservation()  (transfer_out, warehouse stock out)
  shop signs AR-> inv.change_stock(+qty, "transfer_in", ref AR)
  supplier RM  -> inv.change_stock(+qty, "supplier_receipt", ref RM)
"""
import datetime as dt

from . import inventory_service as inv
from . import inventory_ops as inv_ops
from . import ref_coverage as cov
from .inventory_service import InventoryError

PAGE_SIZE = 50
TS_STATUSES = ("draft", "assigned", "loaded", "in_transit", "delivered",
               "delivered_with_variance", "cancelled")
PO_RECEIVABLE = ("sent", "partially_received")


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


def variance_label(diffs):
    """Variance as shown on the AR: No, or Yes with the total units short.

    A line counts as a variance when what the shop received differs from what
    the RF asked for (a short dispatch counts, not only a rejection).
    """
    if not any(diffs):
        return "No"
    total = sum(diffs)
    return "Yes (%+d pcs)" % total if total else "Yes"


# ---------------------------------------------------------------------
# Drivers and vehicles
# ---------------------------------------------------------------------

def list_drivers(q=""):
    where, p = "", {}
    if q.strip():
        where = (" WHERE LOWER(full_name) LIKE :q OR LOWER(COALESCE(contact_no, '')) LIKE :q "
                 "OR LOWER(COALESCE(license_no, '')) LIKE :q")
        p["q"] = _like(q)
    return _b().all("SELECT * FROM drivers" + where + " ORDER BY full_name", **p)


def get_driver(driver_id):
    return _b().one("SELECT * FROM drivers WHERE id = :i", i=driver_id)


def save_driver(user, driver_id, data):
    name = (data.get("full_name") or "").strip()
    if not name:
        raise InventoryError("Driver name is required.")
    if len(name) > 100:
        raise InventoryError("Driver name can be at most 100 characters.")
    contact = (data.get("contact_no") or "").strip()
    license_no = (data.get("license_no") or "").strip()
    if len(contact) > 30 or len(license_no) > 30:
        raise InventoryError("Contact and license can be at most 30 characters each.")
    active = bool(data.get("is_active"))
    if driver_id:
        old = get_driver(driver_id)
        if old is None:
            raise InventoryError("Driver not found.")
        _b().execute("UPDATE drivers SET full_name = :n, contact_no = :c, license_no = :l, "
                     "is_active = :a WHERE id = :i", n=name, c=contact or None,
                     l=license_no or None, a=active, i=driver_id)
        did, action = int(driver_id), "update"
    else:
        row = _b().one("INSERT INTO drivers (full_name, contact_no, license_no, is_active) "
                       "VALUES (:n, :c, :l, :a) RETURNING id",
                       n=name, c=contact or None, l=license_no or None, a=active)
        did, action = row["id"], "create"
    inv.write_audit(user["id"], action, "drivers", did, new={"name": name, "active": active})
    return did, name


def list_vehicles(q=""):
    where, p = "", {}
    if q.strip():
        where = " WHERE LOWER(plate_no) LIKE :q OR LOWER(COALESCE(vehicle_type, '')) LIKE :q"
        p["q"] = _like(q)
    return _b().all("SELECT * FROM vehicles" + where + " ORDER BY plate_no", **p)


def get_vehicle(vehicle_id):
    return _b().one("SELECT * FROM vehicles WHERE id = :i", i=vehicle_id)


def save_vehicle(user, vehicle_id, data):
    plate = (data.get("plate_no") or "").strip().upper()
    if not plate:
        raise InventoryError("Plate number is required.")
    if len(plate) > 20:
        raise InventoryError("Plate number can be at most 20 characters.")
    vtype = (data.get("vehicle_type") or "").strip()
    if len(vtype) > 40:
        raise InventoryError("Vehicle type can be at most 40 characters.")
    active = bool(data.get("is_active"))
    dup = _b().one("SELECT id FROM vehicles WHERE LOWER(plate_no) = :p", p=plate.lower())
    if dup is not None and str(dup["id"]) != str(vehicle_id or ""):
        raise InventoryError("That plate number is already on another vehicle.")
    if vehicle_id:
        old = get_vehicle(vehicle_id)
        if old is None:
            raise InventoryError("Vehicle not found.")
        _b().execute("UPDATE vehicles SET plate_no = :p, vehicle_type = :t, is_active = :a WHERE id = :i",
                     p=plate, t=vtype or None, a=active, i=vehicle_id)
        vid, action = int(vehicle_id), "update"
    else:
        row = _b().one("INSERT INTO vehicles (plate_no, vehicle_type, is_active) "
                       "VALUES (:p, :t, :a) RETURNING id", p=plate, t=vtype or None, a=active)
        vid, action = row["id"], "create"
    inv.write_audit(user["id"], action, "vehicles", vid, new={"plate": plate, "active": active})
    return vid, plate


# ---------------------------------------------------------------------
# Transfer slips
# ---------------------------------------------------------------------

def list_transfers(user, status="", q="", page=1):
    where, p = "", {}
    role = user["role"]
    if role == "shop_manager":
        where += " AND t.to_location_id = :loc"
        p["loc"] = user["location_id"]
    elif role not in ("owner",):
        where += " AND t.from_location_id = :loc"
        p["loc"] = user["location_id"]
    if status in TS_STATUSES:
        where += " AND t.status = :st"
        p["st"] = status
    if q.strip():
        where += " AND (LOWER(t.ts_no) LIKE :q OR LOWER(r.rf_no) LIKE :q OR LOWER(s.code) LIKE :q)"
        p["q"] = _like(q)
    frm = ("FROM transfer_slips t JOIN requisitions r ON r.id = t.rf_id "
           "JOIN locations f ON f.id = t.from_location_id "
           "JOIN locations s ON s.id = t.to_location_id "
           "LEFT JOIN drivers d ON d.id = t.driver_id "
           "LEFT JOIN vehicles v ON v.id = t.vehicle_id WHERE 1 = 1" + where)
    cols = ("SELECT t.id, t.ts_no, COALESCE(t.released_at, t.created_at) AS ts_date, r.rf_no, "
            "s.code AS shop, f.code AS from_loc, d.full_name AS driver, v.plate_no AS vehicle, t.status ")
    return _paged("SELECT COUNT(*) AS n " + frm, cols + frm + " ORDER BY t.id DESC", page, **p)


def prepare_list(warehouse_id):
    rows = _b().all(
        "SELECT r.id, r.rf_no, s.code AS shop, r.status, "
        "(SELECT COUNT(*) FROM requisition_items x WHERE x.rf_id = r.id "
        " AND EXISTS (SELECT 1 FROM reservations rs WHERE rs.rf_item_id = x.id AND rs.status = 'reserved')) AS n_lines, "
        "(SELECT COALESCE(SUM(rs.qty), 0) FROM reservations rs "
        " JOIN requisition_items x ON x.id = rs.rf_item_id "
        " WHERE x.rf_id = r.id AND rs.status = 'reserved') AS qty "
        "FROM requisitions r JOIN locations s ON s.id = r.shop_id "
        "WHERE r.warehouse_id = :w AND r.status IN ('open', 'partially_delivered') "
        "AND NOT EXISTS (SELECT 1 FROM transfer_slips t WHERE t.rf_id = r.id "
        " AND t.status IN ('draft', 'assigned', 'loaded', 'in_transit')) "
        "ORDER BY r.id", w=warehouse_id)
    return [r for r in rows if r["qty"] > 0]


def get_transfer(ts_id, user):
    head = _b().one(
        "SELECT t.*, r.rf_no, r.shop_id, f.code AS from_code, f.name AS from_name, "
        "s.code AS shop_code, s.name AS shop_name, d.full_name AS driver_name, "
        "d.contact_no AS driver_contact, v.plate_no AS vehicle_plate, v.vehicle_type, "
        "pu.full_name AS prepared_by_name, lu.full_name AS loaded_by_name, "
        "ru.full_name AS released_by_name "
        "FROM transfer_slips t JOIN requisitions r ON r.id = t.rf_id "
        "JOIN locations f ON f.id = t.from_location_id JOIN locations s ON s.id = t.to_location_id "
        "LEFT JOIN drivers d ON d.id = t.driver_id LEFT JOIN vehicles v ON v.id = t.vehicle_id "
        "LEFT JOIN users pu ON pu.id = t.prepared_by LEFT JOIN users lu ON lu.id = t.loaded_by "
        "LEFT JOIN users ru ON ru.id = t.released_by WHERE t.id = :i", i=ts_id)
    if head is None:
        return None
    role = user["role"]
    allowed = (role == "owner"
               or (role == "shop_manager" and head["to_location_id"] == user["location_id"]
                   and head["status"] in ("in_transit", "delivered", "delivered_with_variance"))
               or (role in ("warehouse_manager", "warehouse_staff")
                   and head["from_location_id"] == user["location_id"]))
    if not allowed:
        return None
    head["lines"] = _b().all(
        "SELECT ti.id AS ts_item_id, ti.rf_item_id, ti.item_id, ti.qty_released, ti.loaded, "
        "i.item_code, i.item_name, "
        "COALESCE(x.qty_requested, ti.qty_released) AS qty_requested, "
        "(SELECT ai.qty_received FROM acknowledgment_receipt_items ai "
        " WHERE ai.ts_item_id = ti.id LIMIT 1) AS qty_received, "
        "(SELECT ai.qty_expected FROM acknowledgment_receipt_items ai "
        " WHERE ai.ts_item_id = ti.id LIMIT 1) AS qty_expected, "
        "(SELECT ai.variance_resolved_at FROM acknowledgment_receipt_items ai "
        " WHERE ai.ts_item_id = ti.id LIMIT 1) AS variance_resolved_at, "
        "(SELECT ai.variance_reason FROM acknowledgment_receipt_items ai "
        " WHERE ai.ts_item_id = ti.id LIMIT 1) AS variance_reason "
        "FROM transfer_slip_items ti JOIN items i ON i.id = ti.item_id "
        "LEFT JOIN requisition_items x ON x.id = ti.rf_item_id "
        "WHERE ti.ts_id = :t ORDER BY i.item_name", t=ts_id)
    ar = _b().one("SELECT * FROM acknowledgment_receipts WHERE ts_id = :t", t=ts_id)
    if ar is not None:
        ar = dict(ar)
        diffs = [l["qty_received"] - l["qty_requested"] for l in head["lines"]
                 if l["qty_received"] is not None]
        ar["variance_total"] = sum(diffs)
        ar["has_variance"] = any(diffs)
        ar["variance_label"] = variance_label(diffs)
    head["ar"] = ar
    return head


def _transfer_for_write(user, ts_id):
    ts = _b().one("SELECT * FROM transfer_slips WHERE id = :i", i=ts_id)
    if ts is None:
        raise InventoryError("Transfer slip not found.")
    if user["role"] != "warehouse_manager" or ts["from_location_id"] != user["location_id"]:
        raise InventoryError("Only the warehouse manager of the sending warehouse can change this TS.")
    return ts


def reserved_transfer_lines(rf_id):
    """The active reservation quantities that would go on the next transfer."""
    return _b().all(
        "SELECT x.id AS rf_item_id, x.item_id, i.item_code, i.item_name, SUM(rs.qty) AS qty "
        "FROM requisition_items x JOIN items i ON i.id = x.item_id "
        "JOIN reservations rs ON rs.rf_item_id = x.id AND rs.status = 'reserved' "
        "WHERE x.rf_id = :r GROUP BY x.id, x.item_id, i.item_code, i.item_name "
        "HAVING SUM(rs.qty) > 0 ORDER BY x.id", r=rf_id)


def create_transfer(user, rf_id):
    """Prepare a draft TS from the stock that is currently reserved for an RF. Returns (ts_id, ts_no)."""
    if user["role"] != "warehouse_manager":
        raise InventoryError("Only the warehouse manager can prepare a transfer slip.")
    rf = _b().one("SELECT r.*, s.code AS shop FROM requisitions r JOIN locations s ON s.id = r.shop_id "
                  "WHERE r.id = :i", i=rf_id)
    if rf is None or rf["warehouse_id"] != user["location_id"]:
        raise InventoryError("Requisition not found for your warehouse.")
    if rf["status"] not in ("open", "partially_delivered"):
        raise InventoryError("This RF is %s, so no transfer slip can be prepared."
                             % rf["status"].replace("_", " "))
    busy = _b().one("SELECT ts_no FROM transfer_slips WHERE rf_id = :r "
                    "AND status IN ('draft', 'assigned', 'loaded', 'in_transit') LIMIT 1", r=rf_id)
    if busy:
        raise InventoryError("Transfer slip %s is already being prepared for this RF." % busy["ts_no"])
    lines = reserved_transfer_lines(rf_id)
    if not lines:
        raise InventoryError("Nothing is reserved for this RF yet. Reserve stock in the RF queue first.")
    ts_no = inv.next_doc_no("TS")
    row = _b().one(
        "INSERT INTO transfer_slips (ts_no, rf_id, from_location_id, to_location_id, status, prepared_by) "
        "VALUES (:no, :rf, :f, :t, 'draft', :u) RETURNING id",
        no=ts_no, rf=rf_id, f=rf["warehouse_id"], t=rf["shop_id"], u=user["id"])
    ts_id = row["id"]
    for line in lines:
        _b().execute("INSERT INTO transfer_slip_items (ts_id, rf_item_id, item_id, qty_released) "
                     "VALUES (:t, :ri, :i, :q)", t=ts_id, ri=line["rf_item_id"],
                     i=line["item_id"], q=line["qty"])
    inv.write_audit(user["id"], "create", "transfer_slips", ts_id,
                    new={"ts_no": ts_no, "rf_no": rf["rf_no"], "shop": rf["shop"],
                         "items": [[l["item_id"], l["qty"]] for l in lines]})
    return ts_id, ts_no


def assign_transfer(user, ts_id, driver_id, vehicle_id, remarks):
    ts = _transfer_for_write(user, ts_id)
    if ts["status"] != "draft":
        raise InventoryError("Assign a driver and vehicle only while the TS is a draft.")
    try:
        did = int(str(driver_id).strip())
        vid = int(str(vehicle_id).strip())
    except (TypeError, ValueError):
        raise InventoryError("Choose a driver and a vehicle.")
    driver = _b().one("SELECT * FROM drivers WHERE id = :i AND is_active", i=did)
    if driver is None:
        raise InventoryError("Choose an active driver.")
    vehicle = _b().one("SELECT * FROM vehicles WHERE id = :i AND is_active", i=vid)
    if vehicle is None:
        raise InventoryError("Choose an active vehicle.")
    remarks = (remarks or "").strip()
    if len(remarks) > 500:
        raise InventoryError("Remarks can be at most 500 characters.")
    _b().execute("UPDATE transfer_slips SET driver_id = :d, vehicle_id = :v, remarks = :r, "
                 "status = 'assigned' WHERE id = :i", d=did, v=vid, r=remarks or None, i=ts_id)
    inv.write_audit(user["id"], "assign", "transfer_slips", ts_id,
                    old={"status": "draft"},
                    new={"status": "assigned", "driver": driver["full_name"],
                         "vehicle": vehicle["plate_no"]})
    return ts["ts_no"]


def load_transfer(user, ts_id, loaded_ids):
    ts = _b().one("SELECT * FROM transfer_slips WHERE id = :i", i=ts_id)
    if ts is None or ts["from_location_id"] != user["location_id"]:
        raise InventoryError("Transfer slip not found for your warehouse.")
    if ts["status"] != "assigned":
        raise InventoryError("The loading check needs a TS with a driver and vehicle (assigned).")
    items = _b().all("SELECT id, item_id FROM transfer_slip_items WHERE ts_id = :t", t=ts_id)
    checked = set()
    for raw in loaded_ids:
        if str(raw).strip():
            checked.add(_whole(raw, "Line"))
    missing = [i for i in items if i["id"] not in checked]
    if missing:
        raise InventoryError("Tick every line after counting it. %d line(s) are not checked." % len(missing))
    for item in items:
        _b().execute("UPDATE transfer_slip_items SET loaded = TRUE WHERE id = :i", i=item["id"])
    _b().execute("UPDATE transfer_slips SET status = 'loaded', loaded_by = :u WHERE id = :i",
                 u=user["id"], i=ts_id)
    inv.write_audit(user["id"], "load", "transfer_slips", ts_id,
                    old={"status": "assigned"}, new={"status": "loaded"})
    return ts["ts_no"]


def release_transfer(user, ts_id):
    ts = _transfer_for_write(user, ts_id)
    if ts["status"] != "loaded":
        raise InventoryError("Only a loaded TS can be released.")
    items = _b().all(
        "SELECT ti.id, ti.rf_item_id, ti.item_id, ti.qty_released, i.item_name "
        "FROM transfer_slip_items ti JOIN items i ON i.id = ti.item_id "
        "WHERE ti.ts_id = :t ORDER BY ti.id", t=ts_id)
    for item in items:
        held = _b().all("SELECT id, qty FROM reservations WHERE rf_item_id = :r AND status = 'reserved' "
                        "ORDER BY id", r=item["rf_item_id"])
        total = sum(r["qty"] for r in held)
        if total != item["qty_released"]:
            raise InventoryError(
                "%s: the reserved stock changed (%d reserved, %d on the TS). Cancel this TS and prepare a new one."
                % (item["item_name"], total, item["qty_released"]))
        for res in held:
            inv.consume_reservation(res["id"], ref_type="TS", ref_id=ts_id, ref_no=ts["ts_no"],
                                    user_id=user["id"])
    _b().execute("UPDATE transfer_slips SET status = 'in_transit', released_by = :u, "
                 "released_at = CURRENT_TIMESTAMP WHERE id = :i", u=user["id"], i=ts_id)
    inv_ops.refresh_alerts(ts["from_location_id"], [i["item_id"] for i in items])
    inv.write_audit(user["id"], "release", "transfer_slips", ts_id,
                    old={"status": "loaded"},
                    new={"status": "in_transit", "items": [[i["item_id"], i["qty_released"]] for i in items]})
    return ts["ts_no"]


def cancel_transfer(user, ts_id):
    ts = _transfer_for_write(user, ts_id)
    if ts["status"] not in ("draft", "assigned", "loaded"):
        raise InventoryError("This TS is %s and can no longer be cancelled."
                             % ts["status"].replace("_", " "))
    _b().execute("UPDATE transfer_slips SET status = 'cancelled' WHERE id = :i", i=ts_id)
    inv.write_audit(user["id"], "cancel", "transfer_slips", ts_id,
                    old={"status": ts["status"]}, new={"status": "cancelled"})
    return ts["ts_no"]


# ---------------------------------------------------------------------
# Shop receiving (AR)
# ---------------------------------------------------------------------

def list_incoming(shop_id, status="", q="", page=1):
    where, p = (" AND t.to_location_id = :loc "
                "AND t.status IN ('in_transit', 'delivered', 'delivered_with_variance')"), {"loc": shop_id}
    if status in TS_STATUSES:
        where += " AND t.status = :st"
        p["st"] = status
    if q.strip():
        where += " AND (LOWER(t.ts_no) LIKE :q OR LOWER(r.rf_no) LIKE :q)"
        p["q"] = _like(q)
    frm = ("FROM transfer_slips t JOIN requisitions r ON r.id = t.rf_id "
           "JOIN locations f ON f.id = t.from_location_id "
           "LEFT JOIN drivers d ON d.id = t.driver_id "
           "LEFT JOIN vehicles v ON v.id = t.vehicle_id WHERE 1 = 1" + where)
    cols = ("SELECT t.id, t.ts_no, COALESCE(t.released_at, t.created_at) AS ts_date, r.rf_no, "
            "f.code AS from_loc, d.full_name AS driver, v.plate_no AS vehicle, t.status, "
            "(SELECT ar.id FROM acknowledgment_receipts ar WHERE ar.ts_id = t.id LIMIT 1) AS ar_id, "
            "(SELECT COUNT(*) FROM acknowledgment_receipt_items ai "
            " JOIN transfer_slip_items ti ON ti.id = ai.ts_item_id "
            " WHERE ti.ts_id = t.id AND ai.qty_received < ai.qty_expected) AS n_variance, "
            "(SELECT COUNT(*) FROM acknowledgment_receipt_items ai "
            " JOIN transfer_slip_items ti ON ti.id = ai.ts_item_id "
            " WHERE ti.ts_id = t.id AND ai.qty_received < ai.qty_expected "
            " AND ai.variance_resolved_at IS NULL) AS n_open_variance ")
    return _paged("SELECT COUNT(*) AS n " + frm, cols + frm + " ORDER BY t.id DESC", page, **p)


def list_shop_variances(user, status="", page=1):
    """Shop delivery variances, scoped to the user's shop/warehouse."""
    where, p = "", {}
    role = user["role"]
    if role == "shop_manager":
        where += " AND t.to_location_id = :loc"
        p["loc"] = user["location_id"]
    elif role == "warehouse_manager":
        where += " AND t.from_location_id = :loc"
        p["loc"] = user["location_id"]
    elif role != "owner":
        return {"rows": [], "total": 0, "page": 1, "pages": 1}
    if status == "open":
        where += " AND ari.variance_resolved_at IS NULL"
    elif status == "resolved":
        where += " AND ari.variance_resolved_at IS NOT NULL"
    frm = ("FROM acknowledgment_receipt_items ari "
           "JOIN acknowledgment_receipts ar ON ar.id = ari.ar_id "
           "JOIN transfer_slips t ON t.id = ar.ts_id "
           "JOIN requisitions r ON r.id = t.rf_id "
           "JOIN items i ON i.id = ari.item_id "
           "JOIN locations s ON s.id = t.to_location_id "
           "WHERE ari.qty_received < ari.qty_expected" + where)
    cols = ("SELECT ari.id, ar.id AS ar_id, t.id AS ts_id, ar.ar_no, t.ts_no, r.rf_no, s.code AS shop, "
            "i.item_code, i.item_name, "
            "ari.qty_expected, ari.qty_received, "
            "ari.qty_expected - ari.qty_received AS qty_rejected, ari.variance_reason, "
            "ari.variance_resolved_at, ar.received_at ")
    res = _paged("SELECT COUNT(*) AS n " + frm,
                 cols + frm + " ORDER BY CASE WHEN ari.variance_resolved_at IS NULL THEN 0 ELSE 1 END, "
                 "ar.id DESC, i.item_name", page, **p)
    return res


def receive_form(ts_id, user):
    ts = get_transfer(ts_id, user)
    if ts is None or user["role"] != "shop_manager" or ts["status"] != "in_transit":
        return None
    return ts


def _resolve_shop_variances(rf_id, user_id, ar_no):
    """Mark earlier shop variances resolved when their RF line is fully delivered.

    A variance stays open while the shop still waits for units. Once a later
    delivery raises qty_delivered to qty_requested, every earlier variance line
    for that RF item is stamped resolved. Returns the number of lines resolved.
    """
    resolved = 0
    for line in _b().all("SELECT id, qty_requested, qty_delivered FROM requisition_items "
                         "WHERE rf_id = :r", r=rf_id):
        if line["qty_delivered"] < line["qty_requested"]:
            continue
        rows = _b().all(
            "SELECT ari.id FROM acknowledgment_receipt_items ari "
            "JOIN transfer_slip_items tsi ON tsi.id = ari.ts_item_id "
            "WHERE tsi.rf_item_id = :ri AND ari.qty_received < ari.qty_expected "
            "AND ari.variance_resolved_at IS NULL", ri=line["id"])
        for row in rows:
            _b().execute("UPDATE acknowledgment_receipt_items "
                         "SET variance_resolved_at = CURRENT_TIMESTAMP WHERE id = :i", i=row["id"])
            inv.write_audit(user_id, "resolve", "acknowledgment_receipt_items", row["id"],
                            old={"variance_resolved_at": None},
                            new={"variance_resolved_at": "automatic", "ar_no": ar_no})
            resolved += 1
    return resolved


def receive_delivery(user, ts_id, received_date, remarks, pairs, counted=None):
    """Shop signs the AR.

    pairs: (ts_item_id, qty_accepted, qty_rejected, variance_reason).

    Accepted units go into shop stock. Rejected/short units go back into
    warehouse stock and are taken off the RF line's reserved quantity, so the
    warehouse can send them again. When a later delivery fills the RF line, the
    earlier variance is marked resolved automatically.
    Returns (ar_no, has_variance, resolved_variance_count).
    """
    if user["role"] != "shop_manager":
        raise InventoryError("Only the shop manager signs the acknowledgment receipt.")
    ts = get_transfer(ts_id, user)
    if ts is None:
        raise InventoryError("Transfer slip not found for your shop.")
    if ts["status"] != "in_transit":
        raise InventoryError("This TS is %s, so it cannot be received." % ts["status"].replace("_", " "))
    d = _date(received_date, "Date received") or _today()
    remarks = (remarks or "").strip()
    if len(remarks) > 500:
        raise InventoryError("Remarks can be at most 500 characters.")
    by_ts = {i["ts_item_id"]: i for i in ts["lines"]}
    # The shop manager must check the delivery against the TS line by line before signing.
    # (counted=None is only for callers that are not the receiving screen.)
    if counted is not None:
        counted_ids = {str(c).strip() for c in counted}
        if any(str(tid) not in counted_ids for tid in by_ts):
            raise InventoryError("Check the delivery against the TS first: tick 'Counted' on every line "
                                 "after you have counted the items.")
    got = {}
    for ts_item_id, accepted_raw, rejected_raw, reason in pairs:
        if not str(ts_item_id).strip():
            continue
        tid = _whole(ts_item_id, "Line")
        if tid not in by_ts:
            raise InventoryError("A line does not belong to this transfer slip.")
        item = by_ts[tid]
        expected = item["qty_released"]
        accepted_raw = str(accepted_raw or "").strip()
        rejected_raw = str(rejected_raw or "").strip()
        accepted = _whole(accepted_raw or "0", "%s: qty accepted" % item["item_name"])
        rejected = _whole(rejected_raw or "0", "%s: qty rejected" % item["item_name"])
        # The form asks for one of the two; fill the other. Both blank means
        # "everything on the TS was accepted", which also keeps the page usable
        # without JavaScript.
        if not accepted_raw and not rejected_raw:
            accepted, rejected = expected, 0
        elif not accepted_raw:
            accepted = expected - rejected
        elif not rejected_raw:
            rejected = expected - accepted
        if accepted < 0 or rejected < 0:
            raise InventoryError("%s: accepted and rejected cannot be more than the quantity on the TS."
                                 % item["item_name"])
        if accepted + rejected != expected:
            raise InventoryError("%s: accepted + rejected must equal the quantity on the TS."
                                 % item["item_name"])
        reason = (reason or "").strip()
        if rejected > 0 and not reason:
            raise InventoryError("%s: a reason is required when the quantity differs."
                                 % item["item_name"])
        if len(reason) > 200:
            raise InventoryError("A variance reason can be at most 200 characters.")
        got[tid] = {"expected": expected, "accepted": accepted, "rejected": rejected, "reason": reason}
    missing = [i for i in by_ts if i not in got]
    if missing:
        raise InventoryError("Enter the accepted/rejected quantity for every line.")
    # A short dispatch is a variance too: the shop received less than the RF asked for.
    has_variance = any(g["accepted"] != by_ts[tid]["qty_requested"] or g["rejected"] > 0
                       for tid, g in got.items())

    ar_no = inv.next_doc_no("AR")
    row = _b().one(
        "INSERT INTO acknowledgment_receipts (ar_no, ts_id, received_by, received_at, has_variance, remarks) "
        "VALUES (:no, :t, :u, :d, :v, :r) RETURNING id",
        no=ar_no, t=ts_id, u=user["id"], d=d.isoformat(), v=has_variance, r=remarks or None)
    ar_id = row["id"]
    returned_items = []
    for tid, item in by_ts.items():
        g = got[tid]
        _b().execute(
            "INSERT INTO acknowledgment_receipt_items (ar_id, ts_item_id, item_id, qty_expected, "
            "qty_received, variance_reason) VALUES (:a, :t, :i, :e, :r, :why)",
            a=ar_id, t=tid, i=item["item_id"], e=g["expected"], r=g["accepted"],
            why=g["reason"] or None)
        if g["accepted"] > 0:
            inv.change_stock(item["item_id"], ts["to_location_id"], g["accepted"], "transfer_in",
                             ref_type="AR", ref_id=ar_id, ref_no=ar_no, user_id=user["id"])
        if g["rejected"] > 0:
            # The units never entered the shop: put them back in warehouse
            # stock and free the RF line so they can be sent again.
            inv.change_stock(item["item_id"], ts["from_location_id"], g["rejected"], "transfer_in",
                             ref_type="AR", ref_id=ar_id, ref_no=ar_no, user_id=user["id"],
                             remarks="Not accepted at shop; returned to warehouse")
            _b().execute("UPDATE requisition_items SET qty_reserved = "
                         "CASE WHEN qty_reserved < :r THEN 0 ELSE qty_reserved - :r END "
                         "WHERE id = :i", r=g["rejected"], i=item["rf_item_id"])
            returned_items.append(item["item_id"])
        _b().execute("UPDATE requisition_items SET qty_delivered = "
                     "CASE WHEN qty_delivered + :q > qty_requested THEN qty_requested ELSE qty_delivered + :q END "
                     "WHERE id = :i", q=g["accepted"], i=item["rf_item_id"])
    _b().execute("UPDATE transfer_slips SET status = :s, delivered_at = CURRENT_TIMESTAMP WHERE id = :i",
                 s="delivered_with_variance" if has_variance else "delivered", i=ts_id)
    resolved = _resolve_shop_variances(ts["rf_id"], user["id"], ar_no)
    lines = _b().all("SELECT qty_requested, qty_delivered FROM requisition_items WHERE rf_id = :r",
                     r=ts["rf_id"])
    done = all(l["qty_delivered"] >= l["qty_requested"] for l in lines)
    if done:
        _b().execute("UPDATE requisitions SET status = 'closed', closed_at = CURRENT_TIMESTAMP "
                     "WHERE id = :r", r=ts["rf_id"])
    else:
        _b().execute("UPDATE requisitions SET status = 'partially_delivered' WHERE id = :r",
                     r=ts["rf_id"])
    inv_ops.refresh_alerts(ts["to_location_id"], [i["item_id"] for i in ts["lines"]])
    if returned_items:
        inv_ops.refresh_alerts(ts["from_location_id"], returned_items)
    inv.write_audit(user["id"], "receive", "acknowledgment_receipts", ar_id,
                    new={"ar_no": ar_no, "ts_no": ts["ts_no"], "has_variance": has_variance,
                         "items": [[i["item_id"], got[i["ts_item_id"]]["accepted"],
                                    got[i["ts_item_id"]]["rejected"]] for i in ts["lines"]]})
    return ar_no, has_variance, resolved


# ---------------------------------------------------------------------
# Supplier receiving (RM) and discrepancies
# ---------------------------------------------------------------------

def list_pos(warehouse_id, status="", q="", page=1):
    where, p = " AND r.warehouse_id = :w", {"w": warehouse_id}
    if status in PO_RECEIVABLE:
        where += " AND po.status = :st"
        p["st"] = status
    if q.strip():
        where += " AND (LOWER(po.po_no) LIKE :q OR LOWER(s.supplier_name) LIKE :q)"
        p["q"] = _like(q)
    frm = ("FROM purchase_orders po JOIN suppliers s ON s.id = po.supplier_id "
           "JOIN replenishments r ON r.id = po.ref_id WHERE 1 = 1" + where)
    cols = ("SELECT po.id, po.po_no, po.expected_date, po.status, s.supplier_name, "
            "(SELECT COUNT(*) FROM purchase_order_items x WHERE x.po_id = po.id) AS n_items, "
            "(SELECT COALESCE(SUM(x.qty_ordered), 0) FROM purchase_order_items x WHERE x.po_id = po.id) AS qty_ordered, "
            "(SELECT COALESCE(SUM(x.qty_received), 0) FROM purchase_order_items x WHERE x.po_id = po.id) AS qty_received ")
    return _paged("SELECT COUNT(*) AS n " + frm, cols + frm + " ORDER BY po.id DESC", page, **p)


def receiving_form(po_id, warehouse_id):
    po = _b().one(
        "SELECT po.*, s.supplier_name, r.ref_no, r.warehouse_id FROM purchase_orders po "
        "JOIN suppliers s ON s.id = po.supplier_id JOIN replenishments r ON r.id = po.ref_id "
        "WHERE po.id = :i", i=po_id)
    if po is None or po["warehouse_id"] != warehouse_id or po["status"] not in PO_RECEIVABLE:
        return None
    po["lines"] = _b().all(
        "SELECT poi.*, i.item_code, i.item_name, poi.qty_ordered - poi.qty_received AS remaining "
        "FROM purchase_order_items poi JOIN items i ON i.id = poi.item_id "
        "WHERE poi.po_id = :p ORDER BY i.item_name", p=po_id)
    po["lines"] = [i for i in po["lines"] if i["remaining"] > 0]
    sup = _b().one("SELECT contact_person, phone, email FROM suppliers WHERE id = :s", s=po["supplier_id"]) or {}
    po["supplier_contact"] = " · ".join(x for x in (sup.get("contact_person"), sup.get("phone"), sup.get("email")) if x)
    return po


def _apply_discrepancy_replacements(user_id, rm_no, lines):
    """Fill open discrepancy lines with units accepted on a later receiving memo.

    A short or rejected line stays open while the supplier still owes units.
    When those units arrive, the accepted quantity of the new RM is matched to
    the oldest open report line for the same PO line. A report resolves itself
    once every line has been covered.

    Called BEFORE this RM's own discrepancy lines are inserted, so a receipt is
    never used to resolve the report it just created. Returns the report
    numbers that were resolved automatically.
    """
    touched = set()
    for pid, line in lines.items():
        left = line["accepted"]
        if left <= 0:
            continue
        open_lines = _b().all(
            "SELECT di.id, di.report_id, di.qty_short, di.qty_rejected, di.qty_replaced "
            "FROM discrepancy_items di JOIN discrepancy_reports dr ON dr.id = di.report_id "
            "WHERE di.po_item_id = :p AND dr.status IN ('open', 'follow_up') "
            "ORDER BY dr.id, di.id", p=pid)
        for row in open_lines:
            if left <= 0:
                break
            outstanding = row["qty_short"] + row["qty_rejected"] - row["qty_replaced"]
            if outstanding <= 0:
                touched.add(row["report_id"])
                continue
            add = min(left, outstanding)
            _b().execute("UPDATE discrepancy_items SET qty_replaced = qty_replaced + :a "
                         "WHERE id = :i", a=add, i=row["id"])
            left -= add
            touched.add(row["report_id"])

    resolved = []
    for report_id in sorted(touched):
        left_total = _b().one(
            "SELECT COALESCE(SUM(qty_short + qty_rejected - qty_replaced), 0) AS n "
            "FROM discrepancy_items WHERE report_id = :r", r=report_id)["n"]
        if left_total and left_total > 0:
            continue
        rep = _b().one("SELECT report_no, status, follow_up_notes FROM discrepancy_reports "
                       "WHERE id = :r", r=report_id)
        if rep is None or rep["status"] not in ("open", "follow_up"):
            continue
        note = "Automatically resolved: the outstanding quantity was received on %s." % rm_no
        notes = ((rep["follow_up_notes"] or "").strip() + " " + note).strip()[:1000]
        _b().execute("UPDATE discrepancy_reports SET status = 'resolved', "
                     "resolved_at = CURRENT_TIMESTAMP, follow_up_notes = :n WHERE id = :r",
                     n=notes, r=report_id)
        inv.write_audit(user_id, "resolve", "discrepancy_reports", report_id,
                        old={"status": rep["status"]},
                        new={"status": "resolved", "automatic": True, "rm_no": rm_no})
        resolved.append(rep["report_no"])
    return resolved


def receive_goods(user, po_id, supplier_dr_no, received_date, remarks, pairs,
                  supplier_invoice_no=None, delivered_by=None):
    """Warehouse staff record an RM.

    Returns (rm_id, rm_no, discrepancy_no, resolved_discrepancy_nos).
    """
    if user["role"] not in ("warehouse_staff", "warehouse_manager"):
        raise InventoryError("Only warehouse staff can record a receiving memo.")
    po = _b().one(
        "SELECT po.*, r.ref_no, r.warehouse_id FROM purchase_orders po "
        "JOIN replenishments r ON r.id = po.ref_id WHERE po.id = :i", i=po_id)
    if po is None or po["warehouse_id"] != user["location_id"]:
        raise InventoryError("Purchase order not found for your warehouse.")
    if po["status"] not in PO_RECEIVABLE:
        raise InventoryError("This PO is %s, so goods cannot be received against it."
                             % po["status"].replace("_", " "))
    dr = (supplier_dr_no or "").strip()
    if not dr:
        raise InventoryError("The supplier's delivery receipt number is required.")
    if len(dr) > 40:
        raise InventoryError("The supplier DR number can be at most 40 characters.")
    invoice = (supplier_invoice_no or "").strip()
    carrier = (delivered_by or "").strip()                  # (not `delivered`: that name is the per-line quantity below)
    if len(invoice) > 40:
        raise InventoryError("The supplier invoice number can be at most 40 characters.")
    if len(carrier) > 80:
        raise InventoryError("'Delivered by' can be at most 80 characters.")
    # A supplier's DR number and invoice number are each used once. Catch a number typed twice.
    for label, value, column in (("DR no.", dr, "supplier_dr_no"), ("Invoice no.", invoice, "supplier_invoice_no")):
        if value:
            used = _b().one(
                "SELECT gr.rm_no FROM goods_receipts gr JOIN purchase_orders p ON p.id = gr.po_id "
                "WHERE p.supplier_id = :s AND LOWER(gr.%s) = LOWER(:v) LIMIT 1" % column,
                s=po["supplier_id"], v=value)
            if used:
                raise InventoryError("%s %s from this supplier was already recorded on %s." % (label, value, used["rm_no"]))
    d = _date(received_date, "Date received") or _today()
    remarks = (remarks or "").strip()
    if len(remarks) > 500:
        raise InventoryError("Remarks can be at most 500 characters.")

    items = {i["id"]: i for i in _b().all(
        "SELECT poi.*, i.item_name FROM purchase_order_items poi JOIN items i ON i.id = poi.item_id "
        "WHERE poi.po_id = :p", p=po_id)}
    lines = {}
    for po_item_id, delivered_raw, accepted_raw, rejected_raw, reason in pairs:
        if not str(po_item_id).strip():
            continue
        pid = _whole(po_item_id, "Line")
        if pid not in items:
            raise InventoryError("A line does not belong to this purchase order.")
        item = items[pid]
        delivered = _whole(delivered_raw or "0", "%s: qty delivered" % item["item_name"])
        accepted_raw = str(accepted_raw or "").strip()
        rejected_raw = str(rejected_raw or "").strip()
        accepted = _whole(accepted_raw or "0", "%s: qty accepted" % item["item_name"])
        rejected = _whole(rejected_raw or "0", "%s: qty rejected" % item["item_name"])
        # The form asks for one of the two; fill the other. Both blank means
        # "everything delivered was accepted", which also keeps the page usable
        # without JavaScript.
        if not accepted_raw and not rejected_raw:
            accepted, rejected = delivered, 0
        elif not accepted_raw:
            accepted = delivered - rejected
        elif not rejected_raw:
            rejected = delivered - accepted
        if delivered == 0 and accepted == 0 and rejected == 0:
            continue
        remaining = item["qty_ordered"] - item["qty_received"]
        if delivered > remaining:
            raise InventoryError("%s: only %d still expected." % (item["item_name"], remaining))
        if accepted < 0 or rejected < 0:
            raise InventoryError("%s: accepted and rejected cannot be more than the quantity delivered."
                                 % item["item_name"])
        if accepted + rejected != delivered:
            raise InventoryError("%s: accepted + rejected must equal the quantity delivered."
                                 % item["item_name"])
        reason = (reason or "").strip()
        if rejected > 0 and not reason:
            raise InventoryError("%s: a reason is required for rejected stock." % item["item_name"])
        if len(reason) > 200:
            raise InventoryError("A reject reason can be at most 200 characters.")
        lines[pid] = {"delivered": delivered, "accepted": accepted, "rejected": rejected,
                      "reason": reason}
    if not lines:
        raise InventoryError("Enter at least one delivered quantity.")

    rm_no = inv.next_doc_no("RM")
    row = _b().one(
        "INSERT INTO goods_receipts (rm_no, po_id, supplier_dr_no, supplier_invoice_no, delivered_by, "
        "received_date, received_by, remarks) VALUES (:no, :po, :dr, :inv, :by, :d, :u, :r) RETURNING id",
        no=rm_no, po=po_id, dr=dr, inv=invoice or None, by=carrier or None, d=d.isoformat(),
        u=user["id"], r=remarks or None)
    rm_id = row["id"]
    accepted_items = []
    discrepancy_lines = []
    for pid, line in lines.items():
        item = items[pid]
        _b().execute(
            "INSERT INTO goods_receipt_items (rm_id, po_item_id, item_id, qty_delivered, qty_accepted, "
            "qty_rejected, reject_reason) VALUES (:r, :p, :i, :d, :a, :x, :why)",
            r=rm_id, p=pid, i=item["item_id"], d=line["delivered"], a=line["accepted"],
            x=line["rejected"], why=line["reason"] or None)
        if line["accepted"] > 0:
            inv.change_stock(item["item_id"], po["warehouse_id"], line["accepted"], "supplier_receipt",
                             ref_type="RM", ref_id=rm_id, ref_no=rm_no, user_id=user["id"])
            accepted_items.append(item["item_id"])
            _b().execute("UPDATE purchase_order_items SET qty_received = qty_received + :q WHERE id = :i",
                         q=line["accepted"], i=pid)
            _b().execute("UPDATE replenishment_items SET qty_received = qty_received + :q "
                         "WHERE id = :i", q=line["accepted"], i=item["replenishment_item_id"])
        short = (item["qty_ordered"] - item["qty_received"]) - line["delivered"]
        if short > 0 or line["rejected"] > 0:
            # If this PO line is already on an open report, those units are
            # still the same debt. Do not count them twice: a replacement that
            # is rejected again stays on the original report.
            already = _b().one(
                "SELECT di.report_id FROM discrepancy_items di "
                "JOIN discrepancy_reports dr ON dr.id = di.report_id "
                "WHERE di.po_item_id = :p AND dr.status IN ('open', 'follow_up') LIMIT 1",
                p=pid)
            if already is None:
                discrepancy_lines.append({"po_item_id": pid, "item_id": item["item_id"],
                                          "qty_short": short, "qty_rejected": line["rejected"],
                                          "reason": line["reason"] or None})

    po_lines = _b().all("SELECT qty_ordered, qty_received FROM purchase_order_items WHERE po_id = :p",
                        p=po_id)
    received_all = all(l["qty_received"] >= l["qty_ordered"] for l in po_lines)
    _b().execute("UPDATE purchase_orders SET status = :s WHERE id = :i",
                 s="received" if received_all else "partially_received", i=po_id)

    # The REF closes only when everything wanted has arrived AND no quantity is still waiting for a
    # supplier (a REF ordered from several suppliers stays open until the last PO is received).
    if cov.all_received(po["ref_id"]) and not cov.open_lines(po["ref_id"]):
        _b().execute("UPDATE replenishments SET status = 'closed', closed_at = CURRENT_TIMESTAMP "
                     "WHERE id = :r", r=po["ref_id"])
    else:
        _b().execute("UPDATE replenishments SET status = 'receiving' WHERE id = :r "
                     "AND status = 'approved'", r=po["ref_id"])

    # Units accepted now may be the replacement for an earlier short/rejected
    # delivery. Close those reports before this RM's own report is created.
    resolved_drs = _apply_discrepancy_replacements(user["id"], rm_no, lines)

    dr_no = None
    if discrepancy_lines:
        dr_no = inv.next_doc_no("DR")
        report = _b().one(
            "INSERT INTO discrepancy_reports (report_no, rm_id, po_id, status, created_by) "
            "VALUES (:no, :rm, :po, 'open', :u) RETURNING id",
            no=dr_no, rm=rm_id, po=po_id, u=user["id"])
        report_id = report["id"]
        for line in discrepancy_lines:
            _b().execute(
                "INSERT INTO discrepancy_items (report_id, po_item_id, item_id, qty_short, "
                "qty_rejected, reason) VALUES (:r, :p, :i, :s, :x, :why)",
                r=report_id, p=line["po_item_id"], i=line["item_id"], s=line["qty_short"],
                x=line["qty_rejected"], why=line["reason"])
        inv.write_audit(user["id"], "create", "discrepancy_reports", report_id,
                        new={"report_no": dr_no, "rm_no": rm_no,
                             "items": [[l["item_id"], l["qty_short"], l["qty_rejected"]]
                                       for l in discrepancy_lines]})
    if accepted_items:
        inv_ops.refresh_alerts(po["warehouse_id"], accepted_items)
    inv.write_audit(user["id"], "create", "goods_receipts", rm_id,
                    new={"rm_no": rm_no, "po_no": po["po_no"], "supplier_dr_no": dr,
                         "items": [[pid, l["delivered"], l["accepted"], l["rejected"]]
                                   for pid, l in lines.items()]})
    return rm_id, rm_no, dr_no, resolved_drs


def get_receiving(rm_id, user):
    head = _b().one(
        "SELECT gr.*, po.po_no, po.id AS po_id, s.supplier_name, r.ref_no, r.warehouse_id, "
        "u.full_name AS received_by_name FROM goods_receipts gr "
        "JOIN purchase_orders po ON po.id = gr.po_id JOIN suppliers s ON s.id = po.supplier_id "
        "JOIN replenishments r ON r.id = po.ref_id JOIN users u ON u.id = gr.received_by "
        "WHERE gr.id = :i", i=rm_id)
    if head is None:
        return None
    if user["role"] != "owner" and head["warehouse_id"] != user["location_id"]:
        return None
    head["lines"] = _b().all(
        "SELECT gri.*, i.item_code, i.item_name FROM goods_receipt_items gri "
        "JOIN items i ON i.id = gri.item_id WHERE gri.rm_id = :r ORDER BY i.item_name", r=rm_id)
    head["discrepancy"] = _b().one(
        "SELECT id, report_no, status FROM discrepancy_reports WHERE rm_id = :r ORDER BY id DESC LIMIT 1",
        r=rm_id)
    return head


def list_discrepancies(user, status="", q="", page=1):
    where, p = "", {}
    if user["role"] != "owner":
        where += " AND r.warehouse_id = :loc"
        p["loc"] = user["location_id"]
    if status in ("open", "follow_up", "resolved"):
        where += " AND d.status = :st"
        p["st"] = status
    if q.strip():
        where += " AND (LOWER(d.report_no) LIKE :q OR LOWER(po.po_no) LIKE :q OR LOWER(s.supplier_name) LIKE :q)"
        p["q"] = _like(q)
    frm = ("FROM discrepancy_reports d JOIN purchase_orders po ON po.id = d.po_id "
           "JOIN suppliers s ON s.id = po.supplier_id JOIN replenishments r ON r.id = po.ref_id "
           "WHERE 1 = 1" + where)
    cols = ("SELECT d.id, d.report_no, d.status, d.created_at, po.po_no, s.supplier_name ")
    return _paged("SELECT COUNT(*) AS n " + frm, cols + frm + " ORDER BY d.id DESC", page, **p)


def get_discrepancy(report_id, user):
    head = _b().one(
        "SELECT d.*, po.po_no, s.supplier_name, r.warehouse_id, gr.rm_no "
        "FROM discrepancy_reports d JOIN purchase_orders po ON po.id = d.po_id "
        "JOIN suppliers s ON s.id = po.supplier_id JOIN replenishments r ON r.id = po.ref_id "
        "JOIN goods_receipts gr ON gr.id = d.rm_id WHERE d.id = :i", i=report_id)
    if head is None:
        return None
    if user["role"] != "owner" and head["warehouse_id"] != user["location_id"]:
        return None
    head["lines"] = _b().all(
        "SELECT di.*, i.item_code, i.item_name FROM discrepancy_items di "
        "JOIN items i ON i.id = di.item_id WHERE di.report_id = :r ORDER BY i.item_name", r=report_id)
    for line in head["lines"]:
        line["qty_outstanding"] = max(
            0, (line["qty_short"] or 0) + (line["qty_rejected"] or 0) - (line["qty_replaced"] or 0))
    head["qty_outstanding"] = sum(l["qty_outstanding"] for l in head["lines"])
    return head


def update_discrepancy(user, report_id, status, notes):
    rep = _b().one("SELECT * FROM discrepancy_reports WHERE id = :i", i=report_id)
    if rep is None:
        raise InventoryError("Discrepancy report not found.")
    if user["role"] != "warehouse_manager":
        raise InventoryError("Only the warehouse manager updates a discrepancy report.")
    status = (status or "").strip().lower()
    if status not in ("open", "follow_up", "resolved"):
        raise InventoryError("Choose open, follow-up or resolved.")
    notes = (notes or "").strip()
    if len(notes) > 1000:
        raise InventoryError("Follow-up notes can be at most 1000 characters.")
    if status == "resolved":
        _b().execute("UPDATE discrepancy_reports SET status = :s, follow_up_notes = :n, "
                     "resolved_at = COALESCE(resolved_at, CURRENT_TIMESTAMP) WHERE id = :i",
                     s=status, n=notes or None, i=report_id)
    else:
        _b().execute("UPDATE discrepancy_reports SET status = :s, follow_up_notes = :n, "
                     "resolved_at = NULL WHERE id = :i", s=status, n=notes or None, i=report_id)
    inv.write_audit(user["id"], "update", "discrepancy_reports", report_id,
                    old={"status": rep["status"]}, new={"status": status, "notes": notes or None})
    return rep["report_no"]
