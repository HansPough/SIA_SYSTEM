"""Scenario seed: fills the database with data that triggers EVERY situation in the system.

It does not insert finished rows. It logs in as the five demo users and drives the real screens (the same
routes you click), so every number, signature, fingerprint, stock movement and audit entry is genuine.
Some scenarios are finished and some stop half-way ON PURPOSE, so you can do the next step yourself.

    python scripts/seed_scenarios.py --reset --yes

  --reset   empties all transactions first (RF, REF, PO, TS, stock movements, alerts, suppliers, drivers,
            vehicles, signatures, audit log) and reloads the demo stock. Items, users and locations stay.
  --yes     you confirm this is a DEMO database.
  --force   run even though accounts other than the five demo users exist (they are kept).

The demo users must exist (database/seed_users.sql, password "changeme"). When it finishes it prints a guide
(also saved to instance/scenario_guide.md): what was created, which document number, who to log in as, and
what to try next. Do NOT run this on a database with real data.
"""
import argparse
import base64
import datetime as dt
import io
import math
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from PIL import Image, ImageDraw          # noqa: E402

from app import create_app                # noqa: E402
from app import inventory_ops as ops      # noqa: E402
from app import inventory_service as inv  # noqa: E402

DEMO_USERS = ("owner", "whmgr", "whstaff", "shopmgr", "shopstaff")
PASSWORD = "changeme"
TODAY = dt.date.today()

# Everything a user creates by working. Items, users, locations and roles are NOT in this list.
TRANSACTIONAL = [
    "notification_items", "notifications", "reservations", "requisition_items", "requisitions",
    "stock_issue_items", "stock_issues", "stock_adjustment_items", "stock_adjustments",
    "replenishment_items", "replenishments", "rfq_suppliers", "rfqs", "quotation_items", "quotations",
    "purchase_approval_items", "purchase_approvals", "purchase_order_items", "purchase_orders",
    "goods_receipt_items", "goods_receipts", "discrepancy_items", "discrepancy_reports",
    "transfer_slip_items", "transfer_slips", "acknowledgment_receipt_items", "acknowledgment_receipts",
    "supplier_return_items", "supplier_returns", "document_signatures", "audit_log",
    "stock_ledger", "stock_balances", "item_location_settings", "supplier_items", "suppliers",
    "drivers", "vehicles", "document_sequences",
]


class SeedError(Exception):
    pass


# ---------------------------------------------------------------------------------------------
# database helpers (direct SQL is used ONLY to set up the world: stock levels, suppliers, drivers)
# ---------------------------------------------------------------------------------------------

def q(sql, **p):
    rows = inv.backend().all(sql, **p)
    inv.rollback()                       # never keep a read transaction open while the screens work
    return rows


def q1(sql, **p):
    rows = q(sql, **p)
    return rows[0] if rows else None


def x(sql, **p):
    inv.backend().execute(sql, **p)
    inv.commit()


def make_signature(seed=0, size=(420, 160)):
    """A hand-drawn-looking signature (PNG data URL). Different per person."""
    img = Image.new("RGBA", size, (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    pts = []
    for i in range(0, 160):
        t = i / 159
        px = 20 + t * (size[0] - 40)
        py = size[1] / 2 + math.sin(t * (9 + seed) + seed) * (30 - seed * 2) * (1 - t * 0.5) + math.sin(t * 31) * 4
        pts.append((px, py))
    d.line(pts, fill=(15, 15, 70, 255), width=5, joint="curve")
    d.line([(30, size[1] - 28), (size[0] - 60, size[1] - 34)], fill=(15, 15, 70, 255), width=3)
    buf = io.BytesIO()
    img.save(buf, "PNG")
    return "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode()


# ---------------------------------------------------------------------------------------------
# a logged-in user that works through the real screens
# ---------------------------------------------------------------------------------------------

class Actor:
    def __init__(self, app, username):
        self.name = username
        self.c = app.test_client()
        r = self.c.post("/login", data={"username": username, "password": PASSWORD})
        if r.status_code != 302:
            raise SeedError("Could not log in as '%s' with the password '%s'. Load database/seed_users.sql first."
                            % (username, PASSWORD))

    @staticmethod
    def _errors(html):
        return [re.sub(r"\s+", " ", m).strip() for m in re.findall(r'class="flash error">(.*?)</div>', html, re.S)]

    def post(self, path, data=None, what=""):
        r = self.c.post(path, data=data or {}, follow_redirects=True)
        html = r.get_data(as_text=True)
        errors = self._errors(html)
        if r.status_code >= 400 or errors or "Traceback" in html:
            raise SeedError("%s (%s as %s): %s" % (what or path, path, self.name,
                                                   "; ".join(errors) or "HTTP %d" % r.status_code))
        return html

    def get(self, path):
        r = self.c.get(path)
        if r.status_code >= 400:
            raise SeedError("GET %s as %s returned %d" % (path, self.name, r.status_code))
        return r.get_data(as_text=True)


GUIDE = []


def note(area, title, doc, login, next_step):
    GUIDE.append({"area": area, "title": title, "doc": doc, "login": login, "next": next_step})
    print("   + %-34s %-18s %s" % (title[:34], doc, ("-> " + next_step)[:70]))


# ---------------------------------------------------------------------------------------------
# the world: master data and stock levels
# ---------------------------------------------------------------------------------------------

def set_stock(item_id, loc_id, on_hand, rop=None, max_level=None, lead=None):
    """Put an item at an exact quantity. Every change gets a ledger row, so stock cards stay consistent."""
    if rop is not None or not q1("SELECT 1 AS x FROM item_location_settings WHERE item_id = :i AND location_id = :l",
                                 i=item_id, l=loc_id):
        x("INSERT INTO item_location_settings (item_id, location_id, rop, max_level, lead_time_days) "
          "VALUES (:i, :l, :r, :m, :t) ON CONFLICT (item_id, location_id) DO UPDATE SET "
          "rop = EXCLUDED.rop, max_level = EXCLUDED.max_level, lead_time_days = EXCLUDED.lead_time_days",
          i=item_id, l=loc_id, r=rop if rop is not None else 6,
          m=max_level if max_level is not None else 40, t=lead if lead is not None else 7)
    row = q1("SELECT on_hand FROM stock_balances WHERE item_id = :i AND location_id = :l", i=item_id, l=loc_id)
    current = row["on_hand"] if row else 0
    if row is None:
        x("INSERT INTO stock_balances (item_id, location_id, on_hand, reserved) VALUES (:i, :l, 0, 0)", i=item_id, l=loc_id)
    delta = on_hand - current
    if delta:
        x("UPDATE stock_balances SET on_hand = :q, reserved = 0, updated_at = CURRENT_TIMESTAMP "
          "WHERE item_id = :i AND location_id = :l", q=on_hand, i=item_id, l=loc_id)
        x("INSERT INTO stock_ledger (item_id, location_id, movement_type, qty_change, balance_after, remarks) "
          "VALUES (:i, :l, 'adjustment', :d, :q, 'Demo seed: scenario stock level')", i=item_id, l=loc_id, d=delta, q=on_hand)


def reset_database():
    print("Clearing transactions and reloading the demo stock ...")
    x("TRUNCATE " + ", ".join(TRANSACTIONAL) + " RESTART IDENTITY CASCADE")
    sql = open(os.path.join(ROOT, "database", "seed_demo_stock.sql"), encoding="utf-8").read()
    from app.extensions import db            # run the whole file through the DB-API connection
    conn = db.engine.raw_connection()
    try:
        cur = conn.cursor()
        cur.execute(sql)
        conn.commit()
    finally:
        conn.close()
    # Make every item healthy first, so the ONLY alerts are the ones the scenarios create on purpose.
    x("INSERT INTO stock_ledger (item_id, location_id, movement_type, qty_change, balance_after, remarks) "
      "SELECT b.item_id, b.location_id, 'adjustment', (s.rop + (s.max_level - s.rop) / 2 + 1) - b.on_hand, "
      "s.rop + (s.max_level - s.rop) / 2 + 1, 'Demo seed: top-up so only scenarios raise alerts' "
      "FROM stock_balances b JOIN item_location_settings s ON s.item_id = b.item_id AND s.location_id = b.location_id "
      "WHERE b.on_hand <= s.rop")
    x("UPDATE stock_balances b SET on_hand = s.rop + (s.max_level - s.rop) / 2 + 1 FROM item_location_settings s "
      "WHERE s.item_id = b.item_id AND s.location_id = b.location_id AND b.on_hand <= s.rop")


def build_world():
    print("Building suppliers, drivers, vehicles and signatures ...")
    suppliers = [("Highland Wheels Corp", "Ana Reyes", "ana@highlandwheels.example", True, True),
                 ("Metro Rubber Supply", "Ben Cruz", "ben@metrorubber.example", True, True),
                 ("Northern Tire Trading", "Carla Tan", "carla@northerntire.example", False, True),
                 ("Pacific Tire Imports", "Dino Lim", "dino@pacifictire.example", False, False)]
    for name, contact, email, regular, active in suppliers:
        x("INSERT INTO suppliers (supplier_name, contact_person, email, is_regular, is_active) "
          "VALUES (:n, :c, :e, :r, :a)", n=name, c=contact, e=email, r=regular, a=active)
    for name, phone, lic, active in (("Juan Dela Cruz", "0917-111-1111", "N01-23-456789", True),
                                     ("Pedro Santos", "0918-222-2222", "N02-34-567890", True),
                                     ("Mario Reyes (resigned)", "0919-333-3333", "N03-45-678901", False)):
        x("INSERT INTO drivers (full_name, contact_no, license_no, is_active) VALUES (:n, :p, :l, :a)",
          n=name, p=phone, l=lic, a=active)
    for plate, vtype, active in (("ABC 1234", "Isuzu Elf 6-wheeler", True), ("XYZ 5678", "Toyota Hilux", True),
                                 ("OLD 0001", "Retired truck", False)):
        x("INSERT INTO vehicles (plate_no, vehicle_type, is_active) VALUES (:p, :t, :a)", p=plate, t=vtype, a=active)


def sup(name):
    return q1("SELECT id FROM suppliers WHERE supplier_name = :n", n=name)["id"]


# ---------------------------------------------------------------------------------------------
# scenarios
# ---------------------------------------------------------------------------------------------

def latest(table, col="id"):
    return q1("SELECT %s AS v FROM %s ORDER BY id DESC LIMIT 1" % (col, table))["v"]


def run(app):
    WH = q1("SELECT id FROM locations WHERE loc_type = 'warehouse' ORDER BY id LIMIT 1")["id"]
    SHOP = q1("SELECT id FROM locations WHERE loc_type = 'shop' ORDER BY id LIMIT 1")["id"]
    people = {u: Actor(app, u) for u in DEMO_USERS}
    owner, whmgr, whstaff, shopmgr, shopstaff = (people[u] for u in DEMO_USERS)

    # --- signatures: managers draw theirs once, so every PDF shows a real signature ---
    for seed, who in enumerate((owner, whmgr, shopmgr), start=1):
        who.post("/signature", {"action": "save", "signature_data": make_signature(seed)}, "save signature")

    # --- pick items ---
    both = [r for r in q("SELECT i.id, i.item_code, i.item_name FROM items i WHERE i.is_active "
                         "AND EXISTS (SELECT 1 FROM item_location_settings s WHERE s.item_id = i.id AND s.location_id = :w) "
                         "AND EXISTS (SELECT 1 FROM item_location_settings s WHERE s.item_id = i.id AND s.location_id = :s) "
                         "ORDER BY i.item_code", w=WH, s=SHOP)]
    wh_only = [r for r in q("SELECT i.id, i.item_code, i.item_name FROM items i WHERE i.is_active "
                            "AND EXISTS (SELECT 1 FROM item_location_settings s WHERE s.item_id = i.id AND s.location_id = :w) "
                            "AND i.item_code LIKE 'TIR-%' ORDER BY i.item_code DESC", w=WH)]
    used = set()

    def take(n, pool):
        out = []
        for r in pool:
            if r["id"] not in used:
                used.add(r["id"])
                out.append(r)
            if len(out) == n:
                return out
        raise SeedError("Not enough items in the database for the scenarios (need %d more)." % n)

    def healthy(items, wh=60, shop=25):
        for it in items:
            set_stock(it["id"], WH, wh, rop=10, max_level=80, lead=7)
            set_stock(it["id"], SHOP, shop, rop=6, max_level=40, lead=3)

    drivers = [r["id"] for r in q("SELECT id FROM drivers WHERE is_active ORDER BY id")]
    vehicles = [r["id"] for r in q("SELECT id FROM vehicles WHERE is_active ORDER BY id")]
    S1, S2, S3 = sup("Highland Wheels Corp"), sup("Metro Rubber Supply"), sup("Northern Tire Trading")

    # ---- helpers shared by the scenarios ----
    def rf(items_qty, action="submit_rf", remarks="Demo requisition"):
        shopmgr.post("/requisition/rf/new", {"warehouse_id": WH, "action": action, "remarks": remarks,
                     "item_id[]": [i["id"] for i, _ in items_qty], "qty_requested[]": [q_ for _, q_ in items_qty]}, "create RF")
        return latest("requisitions"), latest("requisitions", "rf_no")

    def rf_reserved(item, qty):
        rid, rno = rf([(item, qty)])
        rids = [r["id"] for r in q("SELECT id FROM requisition_items WHERE rf_id = :r ORDER BY id", r=rid)]
        whmgr.post("/inventory/rf-queue/%d" % rid, {"rf_item_id[]": rids, "qty_reserve[]": [qty]}, "reserve")
        return rid, rno

    def ts_for(rid, drv, veh):
        whmgr.post("/delivery/transfers/new", {"rf_id": rid, "driver_id": drv, "vehicle_id": veh}, "prepare + assign TS")
        tsid = latest("transfer_slips")
        return tsid, latest("transfer_slips", "ts_no")

    def ts_items(tsid):
        return [r["id"] for r in q("SELECT id FROM transfer_slip_items WHERE ts_id = :t ORDER BY id", t=tsid)]

    def deliver(tsid, reject_first=0, reason=""):
        lines = q("SELECT id, qty_released FROM transfer_slip_items WHERE ts_id = :t ORDER BY id", t=tsid)
        acc = [l["qty_released"] - (reject_first if i == 0 else 0) for i, l in enumerate(lines)]
        rej = [(reject_first if i == 0 else 0) for i, _ in enumerate(lines)]
        shopmgr.post("/delivery/deliveries/%d/receive" % tsid, {
            "remarks": "", "ts_item_id[]": [l["id"] for l in lines], "counted[]": [l["id"] for l in lines],
            "qty_expected[]": [l["qty_released"] for l in lines], "qty_accepted[]": acc, "qty_rejected[]": rej,
            "variance_reason[]": [reason if rej[i] else "" for i in range(len(lines))]}, "shop receives")

    # =========================================================================================
    print("\n[1] Low-stock alerts (shop manager)")
    a1, a2, a3, a4 = take(4, both)
    healthy([a1, a2, a3, a4])
    for it_ in (a1, a2):
        set_stock(it_["id"], SHOP, 2, rop=6, max_level=40, lead=3)
    ops.refresh_alerts(SHOP, [a1["id"], a2["id"]]); inv.commit()
    alert1 = q1("SELECT id FROM notifications WHERE notif_type = 'low_stock' AND status = 'active' ORDER BY id DESC LIMIT 1")["id"]
    shopmgr.post("/requisition/rf/new", {"warehouse_id": WH, "alert_id": alert1, "action": "submit_rf",
                 "remarks": "Created from the low-stock alert",
                 "item_id[]": [a1["id"], a2["id"]], "qty_requested[]": [20, 20]}, "RF from alert")
    rf_alert_id, rf_alert = latest("requisitions"), latest("requisitions", "rf_no")
    rids = [r["id"] for r in q("SELECT id FROM requisition_items WHERE rf_id = :r ORDER BY id", r=rf_alert_id)]
    whmgr.post("/inventory/rf-queue/%d" % rf_alert_id, {"rf_item_id[]": rids, "qty_reserve[]": [20, 20]}, "reserve")
    tsid, tsno = ts_for(rf_alert_id, drivers[0], vehicles[0])
    whstaff.post("/delivery/transfers/%d/load" % tsid, {"loaded[]": ts_items(tsid)}, "loading check")
    whstaff.post("/delivery/transfers/%d/driver-sign" % tsid, {"signature_data": make_signature(4)}, "driver signs")
    whmgr.post("/delivery/transfers/%d/release" % tsid, {}, "release")
    deliver(tsid)       # the delivery restores the shop stock, and THAT closes the alert
    status = q1("SELECT status FROM notifications WHERE id = :i", i=alert1)["status"]
    if status != "completed":
        raise SeedError("The low-stock alert did not complete after its delivery (status %s)." % status)
    note("Alerts", "Alert COMPLETED (its RF was delivered)", "ALT-%04d" % alert1, "shopmgr",
         "Requisition > Low-stock alerts > filter Completed; RF %s, TS %s" % (rf_alert, tsno))

    # =========================================================================================
    print("\n[2] Requisition forms (RF) in every state")
    it = take(11, both)
    healthy(it)


    rf_id, rf_no = rf([(it[0], 6)], action="save_draft", remarks="Saved, not sent yet")
    note("RF", "RF draft (not sent)", rf_no, "shopmgr", "Open it and press 'Sign & send RF to warehouse'")

    rf_id, rf_no = rf([(it[1], 8), (it[2], 5)])
    note("RF", "RF waiting in the warehouse queue", rf_no, "whmgr", "RF queue > open > Reserve stock (signs 'Noted by')")

    set_stock(it[3]["id"], WH, 4, rop=6, max_level=60, lead=7)          # not enough for 10
    rf_id, rf_no = rf([(it[3], 10), (it[4], 4)])
    ids = [r["id"] for r in q("SELECT id FROM requisition_items WHERE rf_id = :r ORDER BY id", r=rf_id)]
    whmgr.post("/inventory/rf-queue/%d" % rf_id, {"rf_item_id[]": ids, "qty_reserve[]": [4, 4]}, "reserve partly")
    note("RF", "RF partly reserved (stock short)", rf_no, "whmgr",
         "Short item raises a restock alert; make a REF from it. RF waits for the shortage")


    rid, rno = rf_reserved(it[5], 6)
    note("TS", "RF fully reserved (no TS yet)", rno, "whmgr", "Delivery > Transfer slips > Prepare: pick driver + vehicle")



    rid, rno = rf_reserved(it[6], 7)
    tsid, tsno = ts_for(rid, drivers[0], vehicles[0])
    note("TS", "TS assigned (waiting for loading check)", tsno, "whstaff", "Transfers to load > Loading check")

    rid, rno = rf_reserved(it[7], 5)
    tsid, tsno = ts_for(rid, drivers[1], vehicles[1])
    whstaff.post("/delivery/transfers/%d/load" % tsid, {"loaded[]": ts_items(tsid)}, "loading check")
    note("TS", "TS loaded, driver has NOT signed", tsno, "whmgr",
         "Try 'Sign & release' first: it is blocked. Then Driver signs on the staff screen")

    rid, rno = rf_reserved(it[8], 9)
    tsid, tsno = ts_for(rid, drivers[0], vehicles[0])
    whstaff.post("/delivery/transfers/%d/load" % tsid, {"loaded[]": ts_items(tsid)}, "loading check")
    whstaff.post("/delivery/transfers/%d/driver-sign" % tsid, {"signature_data": make_signature(7)}, "driver signs")
    note("TS", "TS loaded + driver signed", tsno, "whmgr", "Press 'Sign & release'")

    rid, rno = rf_reserved(it[9], 8)
    tsid, tsno = ts_for(rid, drivers[1], vehicles[0])
    whstaff.post("/delivery/transfers/%d/load" % tsid, {"loaded[]": ts_items(tsid)}, "loading check")
    whstaff.post("/delivery/transfers/%d/driver-sign" % tsid, {"signature_data": make_signature(5)}, "driver signs")
    whmgr.post("/delivery/transfers/%d/release" % tsid, {}, "release")
    note("TS", "TS in transit (shop must receive)", tsno, "shopmgr",
         "Incoming deliveries > Receive: tick 'Counted' on every line, then sign")


    rid, rno = rf_reserved(it[10], 10)
    tsid, tsno = ts_for(rid, drivers[0], vehicles[1])
    whstaff.post("/delivery/transfers/%d/load" % tsid, {"loaded[]": ts_items(tsid)}, "loading check")
    whstaff.post("/delivery/transfers/%d/driver-sign" % tsid, {"signature_data": make_signature(3)}, "driver signs")
    whmgr.post("/delivery/transfers/%d/release" % tsid, {}, "release")
    deliver(tsid, reject_first=3, reason="3 tires damaged in transit")
    note("TS", "Delivered WITH shop variance", tsno, "whmgr", "Delivery > Discrepancies > tab 'To shops': open variance")

    # a clean, completed delivery, and a cancelled RF
    cl = take(1, both)[0]
    healthy([cl])
    rid, rno = rf_reserved(cl, 6)
    tsid, tsno = ts_for(rid, drivers[1], vehicles[1])
    whstaff.post("/delivery/transfers/%d/load" % tsid, {"loaded[]": ts_items(tsid)}, "loading check")
    whstaff.post("/delivery/transfers/%d/driver-sign" % tsid, {"signature_data": make_signature(2)}, "driver signs")
    whmgr.post("/delivery/transfers/%d/release" % tsid, {}, "release")
    deliver(tsid)
    note("TS", "Delivered cleanly (AR signed)", tsno, "owner", "Open the TS: it shows the TS and its AR with all signatures")

    cn = take(1, both)[0]
    healthy([cn])
    rid, rno = rf([(cn, 5)])
    shopmgr.post("/requisition/rf/%d/cancel" % rid, {"cancel_reason": "Customer cancelled the order"}, "cancel RF")
    note("RF", "RF cancelled", rno, "shopmgr", "Requisition forms > filter Cancelled")

    # =========================================================================================
    print("\n[3] Sales, voids and stock adjustments")
    s1, s2 = take(2, both)
    healthy([s1, s2], shop=30)

    einv = [0]

    def sale(item, qty, customer):
        einv[0] += 1                                   # every sale has its own e-invoice number
        shopstaff.post("/inventory/issues/new", {"issue_date": TODAY.isoformat(), "reason": "sale",
                       "document_ref": "EI-2026-%05d" % einv[0],
                       "customer_name": customer, "item_id[]": [item["id"]], "qty[]": [qty]}, "record sale")
        return latest("stock_issues"), latest("stock_issues", "issue_no")

    _, ino = sale(s1, 2, "Walk-in customer")
    note("Sales", "Normal sale", ino, "shopstaff", "Sales / issues")
    iid, ino = sale(s2, 1, "JB Trucking")
    shopstaff.post("/inventory/issues/%d/void-request" % iid, {"void_reason": "Wrong item was entered"}, "void request")
    note("Sales", "Void REQUESTED (waiting)", ino, "shopmgr", "Open it > confirm or reject the void")
    iid, ino = sale(s1, 1, "Mang Tonyo")
    shopstaff.post("/inventory/issues/%d/void-request" % iid, {"void_reason": "Customer returned it at the counter"}, "void request")
    shopmgr.post("/inventory/issues/%d/void-review" % iid, {"decision": "confirm", "remarks": ""}, "confirm void")
    note("Sales", "Sale VOIDED (stock put back)", ino, "owner", "Sales / issues > filter Voided")

    def adjustment(actor, item, loc_qty, reason):
        actor.post("/inventory/adjustments/new", {"reason": reason, "item_id[]": [item["id"]], "qty_actual[]": [loc_qty]}, "adjustment")
        return latest("stock_adjustments"), latest("stock_adjustments", "adj_no")

    aid, ano = adjustment(shopstaff, s1, 20, "Damaged")
    note("Adjustments", "Adjustment waiting for review", ano, "shopmgr", "Stock adjustments > review: approve or reject")
    aid, ano = adjustment(shopstaff, s2, 26, "count difference")
    shopmgr.post("/inventory/adjustments/%d/review" % aid, {"decision": "approve", "review_remarks": "Recounted, correct"}, "approve")
    note("Adjustments", "Adjustment APPROVED", ano, "owner", "Stock adjustments (view): stock was changed")
    aid, ano = adjustment(shopstaff, s1, 15, "Lost")
    shopmgr.post("/inventory/adjustments/%d/review" % aid, {"decision": "reject", "review_remarks": "Recount first, then resubmit"}, "reject")
    note("Adjustments", "Adjustment REJECTED", ano, "shopstaff", "See the reviewer's remark")
    aid, ano = adjustment(whstaff, s2, 50, "count difference")
    note("Adjustments", "Warehouse adjustment waiting", ano, "whmgr", "Stock adjustments > review")

    # =========================================================================================
    print("\n[4] Replenishment (REF), RFQ, quotations and approvals")
    pool = take(34, wh_only)
    healthy(pool, wh=60, shop=25)
    pi = iter(pool)

    def ref(n_items, qty=20, action="submit_ref", remarks="Demo replenishment"):
        its = [next(pi) for _ in range(n_items)]
        whmgr.post("/purchasing/ref/new", {"remarks": remarks, "action": action,
                   "item_id[]": [i["id"] for i in its], "qty_requested[]": [qty] * n_items,
                   "reason[]": ["below_rop"] * n_items}, "create REF")
        return latest("replenishments"), latest("replenishments", "ref_no"), its

    def line_ids(ref_id):
        return {r["item_id"]: r["id"] for r in q("SELECT id, item_id FROM replenishment_items WHERE ref_id = :r", r=ref_id)}

    def rfq(ref_id, supplier_ids):
        whmgr.post("/purchasing/ref/%d/rfq/new" % ref_id, {"supplier_ids[]": supplier_ids,
                   "reply_deadline": (TODAY + dt.timedelta(days=7)).isoformat(), "terms": "30 days, delivered to warehouse"}, "RFQ")

    def quote(ref_id, supplier_id, rows, valid_days=30):
        """rows: [(item, qty, unit_price, lead_days)]"""
        ids = line_ids(ref_id)
        whmgr.post("/purchasing/ref/%d/quotation/new" % ref_id, {
            "supplier_id": supplier_id, "quotation_date": TODAY.isoformat(),
            "valid_until": (TODAY + dt.timedelta(days=valid_days)).isoformat(),
            "ref_item_id[]": [ids[i["id"]] for i, *_ in rows], "qty_offered[]": [r[1] for r in rows],
            "unit_price[]": ["%.2f" % r[2] for r in rows], "lead_time_days[]": [r[3] for r in rows]}, "quotation")
        return latest("quotations")

    def choose(ref_id, picks):
        """picks: {item_id: 'q:<id>' | 'a:<supplier id>' | 'open'}"""
        ids = line_ids(ref_id)
        data = {}
        for item_id, choice in picks.items():
            data["choice_%d" % ids[item_id]] = choice
        whmgr.post("/purchasing/ref/%d/compare" % ref_id, data, "choose suppliers")

    def pending_approval_ids():
        return [r["id"] for r in q("SELECT id FROM purchase_approvals WHERE decision = 'pending' ORDER BY id")]

    def decide(decisions=None, remarks="OK, approved"):
        ids = pending_approval_ids()
        data = {"remarks": remarks, "expected_date": (TODAY + dt.timedelta(days=10)).isoformat(), "terms": "30 days"}
        for i, aid in enumerate(ids):
            data["decision_%d" % aid] = (decisions or {}).get(i, "approve")
        owner.post("/purchasing/approvals/%d" % ids[0], data, "owner decides")

    def po_of(ref_id):
        return q("SELECT id, po_no, supplier_id FROM purchase_orders WHERE ref_id = :r ORDER BY id", r=ref_id)

    # agreed prices for the two regular suppliers (so a REF can skip the RFQ)
    agreed = pool[:6]
    for k, it_ in enumerate(agreed):
        for sid, base in ((S1, 1450), (S2, 1500)):
            x("INSERT INTO supplier_items (supplier_id, item_id, agreed_price, valid_from) VALUES (:s, :i, :p, :d)",
              s=sid, i=it_["id"], p=base + k * 25, d=(TODAY - dt.timedelta(days=30)).isoformat())

    restock = q1("SELECT id FROM notifications WHERE notif_type = 'restock' AND status = 'active' ORDER BY id DESC LIMIT 1")
    if restock:
        whmgr.post("/purchasing/ref/new", {"remarks": "Created from the restock alert", "action": "submit_ref",
                   "alert_id": restock["id"], "item_id[]": [it[3]["id"]], "qty_requested[]": [60],
                   "reason[]": ["below_rop"]}, "REF from the restock alert")
        done = q1("SELECT status FROM notifications WHERE id = :i", i=restock["id"])["status"]
        if done != "completed":
            raise SeedError("The restock alert should complete when a REF is made from it (status %s)." % done)
        note("Alerts", "Restock alert COMPLETED (REF made from it)", "ALT-%04d" % restock["id"], "whmgr",
             "Purchasing > Restock alerts > filter Completed. The REF %s is waiting for an RFQ" % latest("replenishments", "ref_no"))

    ref_id, ref_no, _ = ref(2, action="save_draft", remarks="Saved, not submitted")
    note("REF", "REF draft", ref_no, "whmgr", "Open > 'Sign & submit REF'")

    # P2: items with agreed prices: can be ordered with NO RFQ
    whmgr.post("/purchasing/ref/new", {"remarks": "Regular supplier agreed prices exist", "action": "submit_ref",
               "item_id[]": [agreed[0]["id"], agreed[1]["id"]], "qty_requested[]": [12, 12], "reason[]": ["below_rop"] * 2}, "REF")
    note("REF", "REF submitted (agreed prices exist)", latest("replenishments", "ref_no"), "whmgr",
         "Choose suppliers: pick 'agreed price' without sending an RFQ")
    used_agreed = {agreed[0]["id"], agreed[1]["id"]}
    pi = iter([i for i in pool if i["id"] not in used_agreed])

    ref_id, ref_no, its = ref(2)
    rfq(ref_id, [S1, S2, S3])
    note("REF", "RFQ sent, no replies yet", ref_no, "whmgr", "Record quotation (as suppliers reply)")

    # P4: mixed suppliers: S3 has items a,b; S2 has b,c; nobody has d
    ref_id, ref_no, its = ref(4)
    rfq(ref_id, [S1, S2, S3])
    quote(ref_id, S3, [(its[0], 20, 1400, 7), (its[1], 20, 1350, 7)])
    quote(ref_id, S2, [(its[1], 20, 1380, 5), (its[2], 20, 1420, 5)])
    note("REF", "Quotes in: mixed suppliers, 1 item unquoted", ref_no, "whmgr",
         "Choose suppliers: split items across suppliers; item 4 has NO supplier: keep open")

    # P5: two suppliers waiting for the owner
    ref_id, ref_no, its = ref(2)
    rfq(ref_id, [S1, S2])
    q1_ = quote(ref_id, S1, [(its[0], 20, 1300, 7)])
    q2_ = quote(ref_id, S2, [(its[1], 20, 1250, 6)])
    choose(ref_id, {its[0]["id"]: "q:%d" % q1_, its[1]["id"]: "q:%d" % q2_})
    note("Approvals", "Two suppliers waiting for the OWNER", ref_no, "owner",
         "Approvals: approve one, reject the other (rejecting needs remarks)")

    # P6: approved, two POs: one sent, one ready
    ref_id, ref_no, its = ref(2)
    rfq(ref_id, [S1, S3])
    q1_ = quote(ref_id, S1, [(its[0], 20, 1500, 7)])
    q3_ = quote(ref_id, S3, [(its[1], 20, 1480, 7)])
    choose(ref_id, {its[0]["id"]: "q:%d" % q1_, its[1]["id"]: "q:%d" % q3_})
    # approve ONLY this REF's approvals (the pending list also holds P5): decide by approval id order
    mine = [r["id"] for r in q("SELECT id FROM purchase_approvals WHERE ref_id = :r ORDER BY id", r=ref_id)]
    data = {"remarks": "OK", "expected_date": (TODAY + dt.timedelta(days=10)).isoformat(), "terms": "30 days"}
    for aid in mine:
        data["decision_%d" % aid] = "approve"
    owner.post("/purchasing/approvals/%d" % mine[0], data, "owner approves REF")
    po_a, po_b = po_of(ref_id)
    whmgr.post("/purchasing/po/%d" % po_a["id"], {"action": "mark_sent"}, "send PO")
    note("PO", "PO SENT (waiting for goods)", po_a["po_no"], "whstaff", "Receiving from suppliers > Check goods")
    note("PO", "PO READY to send (owner approved)", po_b["po_no"], "whmgr", "Press 'Sign & mark as sent'")

    # P7: partly ordered: one item open
    ref_id, ref_no, its = ref(3)
    rfq(ref_id, [S1])
    q1_ = quote(ref_id, S1, [(its[0], 20, 1390, 7), (its[1], 20, 1410, 7)])
    choose(ref_id, {its[0]["id"]: "q:%d" % q1_, its[1]["id"]: "q:%d" % q1_, its[2]["id"]: "open"})
    mine = [r["id"] for r in q("SELECT id FROM purchase_approvals WHERE ref_id = :r ORDER BY id", r=ref_id)]
    owner.post("/purchasing/approvals/%d" % mine[0], {"decision_%d" % mine[0]: "approve", "remarks": "OK",
               "expected_date": (TODAY + dt.timedelta(days=10)).isoformat(), "terms": ""}, "owner approves")
    note("REF", "Partly ordered (1 item still open)", ref_no, "whmgr", "'Ask more suppliers (new RFQ)' for the open item")

    # P8: an item given up, can be restored
    ref_id, ref_no, its = ref(2)
    rfq(ref_id, [S3])
    q3_ = quote(ref_id, S3, [(its[0], 20, 1405, 7)])
    choose(ref_id, {its[0]["id"]: "q:%d" % q3_, its[1]["id"]: "open"})
    mine = [r["id"] for r in q("SELECT id FROM purchase_approvals WHERE ref_id = :r ORDER BY id", r=ref_id)]
    owner.post("/purchasing/approvals/%d" % mine[0], {"decision_%d" % mine[0]: "approve", "remarks": "OK",
               "expected_date": (TODAY + dt.timedelta(days=10)).isoformat(), "terms": ""}, "owner approves")
    whmgr.post("/purchasing/ref/%d/drop" % ref_id, {"ref_item_id": line_ids(ref_id)[its[1]["id"]],
               "reason": "Maker discontinued this size"}, "give up an item")
    note("REF", "An item was GIVEN UP (can be restored)", ref_no, "whmgr", "Press 'Restore' on the dropped item")

    # P11: a quotation that has already expired
    ref_id, ref_no, its = ref(1)
    rfq(ref_id, [S3])
    quote(ref_id, S3, [(its[0], 20, 1399, 7)])
    x("UPDATE quotations SET valid_until = :d WHERE id = (SELECT MAX(id) FROM quotations)", d=(TODAY - dt.timedelta(days=5)).isoformat())
    note("REF", "Quotation EXPIRED", ref_no, "whmgr", "Choose suppliers: the expired offer is disabled; record a fresh one")

    # P10: cancelled
    ref_id, ref_no, its = ref(1)
    whmgr.post("/purchasing/ref/%d/cancel" % ref_id, {"cancel_reason": "Stock arrived from another branch"}, "cancel REF")
    note("REF", "REF cancelled", ref_no, "whmgr", "Replenishment forms > filter Cancelled")

    # =========================================================================================
    print("\n[5] Receiving goods, receiving memos and discrepancy reports")

    def full_po(n_items=1, qty=20, price=1450):
        ref_id, ref_no, its = ref(n_items, qty=qty)
        rfq(ref_id, [S1])
        qid = quote(ref_id, S1, [(i, qty, price, 7) for i in its])
        choose(ref_id, {i["id"]: "q:%d" % qid for i in its})
        mine = [r["id"] for r in q("SELECT id FROM purchase_approvals WHERE ref_id = :r ORDER BY id", r=ref_id)]
        owner.post("/purchasing/approvals/%d" % mine[0], {"decision_%d" % mine[0]: "approve", "remarks": "OK",
                   "expected_date": (TODAY + dt.timedelta(days=5)).isoformat(), "terms": ""}, "owner approves")
        po = po_of(ref_id)[0]
        whmgr.post("/purchasing/po/%d" % po["id"], {"action": "mark_sent"}, "send PO")
        return ref_id, ref_no, po

    receipts = [0]
    carriers = ["Mang Lito (driver)", "Rey Santos (helper)", "Highland truck 2", ""]

    def receive(actor, po, delivered=None, rejected=None, reason=""):
        items = q("SELECT id, qty_ordered, qty_received FROM purchase_order_items WHERE po_id = :p ORDER BY id", p=po["id"])
        deliv = [(delivered[i] if delivered else it_["qty_ordered"] - it_["qty_received"]) for i, it_ in enumerate(items)]
        rej = [(rejected[i] if rejected else 0) for i in range(len(items))]
        receipts[0] += 1                               # a supplier's DR and invoice numbers are each used once
        actor.post("/delivery/receiving/%d/check" % po["id"], {
            "supplier_dr_no": "DR-%05d" % (4000 + receipts[0]), "supplier_invoice_no": "SI-%05d" % (9000 + receipts[0]),
            "delivered_by": carriers[receipts[0] % len(carriers)], "received_date": TODAY.isoformat(),
            "po_item_id[]": [i["id"] for i in items], "qty_delivered[]": deliv,
            "qty_accepted[]": [d - r for d, r in zip(deliv, rej)], "qty_rejected[]": rej,
            "reject_reason[]": [reason if r else "" for r in rej]}, "receive goods")

    # fully received by staff: memo waits for the manager's signature, REF closes
    ref_id, ref_no, po = full_po()
    receive(whstaff, po)
    note("Receiving", "Received IN FULL by staff (memo unsigned)", ref_no, "whmgr",
         "Documents to sign: the receiving memo waits for 'Checked by'. The REF is now closed")

    # received short: discrepancy report
    ref_id, ref_no, po = full_po(qty=30)
    receive(whstaff, po, delivered=[24])
    rmid = latest("goods_receipts")
    whmgr.post("/documents/RM/%d/sign" % rmid, {"slot": "checked_by"}, "sign RM")
    drid = q1("SELECT id FROM discrepancy_reports ORDER BY id DESC LIMIT 1")["id"]
    note("Discrepancy", "Supplier delivered SHORT (open)", latest("discrepancy_reports", "report_no"), "whmgr",
         "Documents to sign: sign 'Prepared by', then the owner signs 'Noted by'")

    # rejected on arrival, DR signed by manager: waits for the owner
    ref_id, ref_no, po = full_po(qty=20)
    receive(whstaff, po, delivered=[20], rejected=[4], reason="Sidewall cuts")
    whmgr.post("/documents/RM/%d/sign" % latest("goods_receipts"), {"slot": "checked_by"}, "sign RM")
    whmgr.post("/documents/DR/%d/sign" % latest("discrepancy_reports"), {"slot": "prepared_by"}, "sign DR")
    note("Discrepancy", "Goods REJECTED; DR waits for the owner", latest("discrepancy_reports", "report_no"), "owner",
         "Documents to sign: 'Noted by'")

    # short, then the supplier replaces the rest: the DR resolves itself
    ref_id, ref_no, po = full_po(qty=20)
    receive(whstaff, po, delivered=[14])
    whmgr.post("/documents/RM/%d/sign" % latest("goods_receipts"), {"slot": "checked_by"}, "sign RM")
    receive(whstaff, po, delivered=[6])
    whmgr.post("/documents/RM/%d/sign" % latest("goods_receipts"), {"slot": "checked_by"}, "sign RM")
    note("Discrepancy", "Short, then REPLACED (DR resolved)", latest("discrepancy_reports", "report_no"), "whmgr",
         "Discrepancies > From suppliers > filter Resolved")

    # partly received PO still waiting for the balance
    ref_id, ref_no, po = full_po(qty=40)
    receive(whstaff, po, delivered=[25])
    whmgr.post("/documents/RM/%d/sign" % latest("goods_receipts"), {"slot": "checked_by"}, "sign RM")
    note("PO", "PO PARTLY received (balance due)", po["po_no"], "whstaff",
         "Check goods again when the rest arrives (a DR is opened for the short quantity)")

    # a cancelled PO frees its items
    ref_id, ref_no, po = full_po(qty=15)
    whmgr.post("/purchasing/po/%d/cancel" % po["id"], {"cancel_reason": "Supplier cannot deliver in time"}, "cancel PO")
    note("PO", "PO cancelled (REF can be re-ordered)", po["po_no"], "whmgr", "Open the REF: the item needs a supplier again")

    # owner rejected a supplier's price
    ref_id, ref_no, its = ref(2)
    rfq(ref_id, [S1, S3])
    q1_ = quote(ref_id, S1, [(its[0], 20, 1700, 7)])
    q3_ = quote(ref_id, S3, [(its[1], 20, 1320, 7)])
    choose(ref_id, {its[0]["id"]: "q:%d" % q1_, its[1]["id"]: "q:%d" % q3_})
    mine = [r["id"] for r in q("SELECT id FROM purchase_approvals WHERE ref_id = :r ORDER BY id", r=ref_id)]
    d = {"remarks": "Highland price is too high", "expected_date": (TODAY + dt.timedelta(days=10)).isoformat(), "terms": ""}
    for aid in mine:
        s_id = q1("SELECT supplier_id FROM purchase_approvals WHERE id = :a", a=aid)["supplier_id"]
        d["decision_%d" % aid] = "reject" if s_id == S1 else "approve"
    owner.post("/purchasing/approvals/%d" % mine[0], d, "owner decides")
    note("Approvals", "Owner REJECTED one supplier", ref_no, "whmgr", "The rejected item is open again: choose another supplier")

    # =========================================================================================
    print("\n[6] Final alert state")
    # shop: two items low -> one ACTIVE low-stock alert
    for it_ in (a3, a4):
        set_stock(it_["id"], SHOP, 1, rop=6, max_level=40, lead=3)
    ops.refresh_alerts(SHOP, [a3["id"], a4["id"]]); inv.commit()
    shop_alert = q1("SELECT id FROM notifications WHERE notif_type = 'low_stock' AND status = 'active' ORDER BY id DESC LIMIT 1")
    note("Alerts", "ACTIVE low-stock alert (2 items)", "ALT-%04d" % shop_alert["id"], "shopmgr",
         "Create RF from the alert, or Snooze it and watch the sidebar badge go away")
    # warehouse: items below ROP -> restock alert; snooze it so the Snoozed filter has something
    r1, r2 = take(2, wh_only)
    for it_ in (r1, r2):
        set_stock(it_["id"], WH, 2, rop=10, max_level=80, lead=7)
    ops.refresh_alerts(WH, [r1["id"], r2["id"], it[3]["id"]]); inv.commit()
    wh_alert = q1("SELECT id FROM notifications WHERE notif_type = 'restock' AND status = 'active' ORDER BY id DESC LIMIT 1")
    if wh_alert:
        whmgr.post("/purchasing/alerts/%d/snooze" % wh_alert["id"], {"days": 7}, "snooze restock alert")
        note("Alerts", "Restock alert SNOOZED for a week", "ALT-%04d" % wh_alert["id"], "whmgr",
             "Purchasing > Restock alerts > filter Snoozed > 'Wake now'")
    # an old alert someone dismissed before snoozing existed
    x("INSERT INTO notifications (notif_type, location_id, status, dismissed_reason, closed_by, closed_at) "
      "VALUES ('low_stock', :l, 'dismissed', 'Handled by phone order', (SELECT id FROM users WHERE username = 'shopmgr'), CURRENT_TIMESTAMP)", l=SHOP)


def write_guide(path):
    """The scenario guide: grouped by area, with the accounts and how to start over."""
    order, groups = [], {}
    for g in GUIDE:
        if g["area"] not in groups:
            order.append(g["area"])
            groups[g["area"]] = []
        groups[g["area"]].append(g)
    lines = ["# Scenario guide", "",
             "Created by `python scripts/seed_scenarios.py --reset --yes`. Every document was made through the real screens,",
             "so numbers, signatures and audit entries are genuine. Some scenarios stop half-way on purpose: do the next step",
             "yourself. To start over, run the same command again (document numbers are the same each time).", "",
             "## Accounts (password for all: `%s`)" % PASSWORD, "",
             "| Username | Role | Sees |", "|---|---|---|",
             "| `owner` | Owner | approvals, documents to sign, everything read-only |",
             "| `whmgr` | Warehouse manager | RF queue, REF/RFQ/PO, transfers, receiving, master data |",
             "| `whstaff` | Warehouse staff | loading check, driver signature, receiving goods |",
             "| `shopmgr` | Shop manager | low-stock alerts, RF, incoming deliveries, adjustments, voids |",
             "| `shopstaff` | Shop staff | sales and stock adjustments |", ""]
    for area in order:
        lines += ["## " + area, "", "| What it is | Document | Log in as | Try this |", "|---|---|---|---|"]
        for g in groups[area]:
            lines.append("| %s | %s | `%s` | %s |" % (g["title"], g["doc"], g["login"], g["next"]))
        lines.append("")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    open(path, "w", encoding="utf-8").write("\n".join(lines))


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--reset", action="store_true", help="empty all transactions first and reload the demo stock")
    ap.add_argument("--yes", action="store_true", help="confirm this is a DEMO database")
    ap.add_argument("--force", action="store_true", help="run even if accounts other than the demo users exist")
    ap.add_argument("--guide", default=os.path.join(ROOT, "instance", "scenario_guide.md"))
    args = ap.parse_args()

    app = create_app()
    app.config["DEV_LOGIN"] = False
    with app.app_context():
        url = app.config.get("SQLALCHEMY_DATABASE_URI", "")
        print("Database: " + re.sub(r":[^:@/]+@", ":***@", str(url)))
        names = {r["username"] for r in q("SELECT username FROM users")}
        missing = [u for u in DEMO_USERS if u not in names]
        if missing:
            raise SystemExit("Missing demo users %s. Load database/seed_users.sql first." % ", ".join(missing))
        extra = sorted(names - set(DEMO_USERS))
        if extra and not args.force:
            raise SystemExit("This database has other accounts (%s), so it may hold real data. "
                             "Use --force only if you are sure it is a demo database." % ", ".join(extra))
        busy = q1("SELECT (SELECT COUNT(*) FROM requisitions) + (SELECT COUNT(*) FROM replenishments) "
                  "+ (SELECT COUNT(*) FROM stock_issues) AS n")["n"]
        if busy and not args.reset:
            raise SystemExit("The database already has transactions. Run with --reset to start over.")
        if args.reset and not args.yes:
            raise SystemExit("--reset deletes all transactions, suppliers, drivers and vehicles. "
                             "Add --yes to confirm that this is a DEMO database.")
        try:
            if args.reset:
                reset_database()
            build_world()
            run(app)
        except SeedError as e:
            raise SystemExit("\nSEED STOPPED: %s" % e)
        write_guide(args.guide)
    print("\nDone. %d scenarios created. Guide: %s" % (len(GUIDE), args.guide))


if __name__ == "__main__":
    main()
