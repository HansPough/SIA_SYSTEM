"""Click-through tests for Module 4 (Delivery): transfer slips, shop receiving (AR),
supplier receiving (RM), discrepancy reports, drivers and vehicles.
"""
import datetime as dt
import os
import re
import sys
import unittest

sys.path.insert(0, os.path.dirname(__file__))
from app import create_app
from app import inventory_service as inv
from sqlite_schema import SqliteBackend

WH, SHOP = 1, 2
A, B = 1, 2


import base64
import io

from PIL import Image, ImageDraw


def make_png(size=(400, 160), ink=True):
    """A real signature-like PNG (transparent background, a few strokes)."""
    img = Image.new("RGBA", size, (0, 0, 0, 0))
    if ink:
        d = ImageDraw.Draw(img)
        d.line([(20, 120), (90, 30), (150, 110), (230, 40), (330, 100)], fill=(10, 10, 60, 255), width=6)
    buf = io.BytesIO()
    img.save(buf, "PNG")
    return "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode()


class Base(unittest.TestCase):
    def setUp(self):
        self.be = SqliteBackend()
        inv.set_backend(self.be)
        inv._backend_supports_locking = False
        b = self.be
        for code in ("owner", "shop_manager", "shop_staff", "warehouse_manager", "warehouse_staff"):
            b.execute("INSERT INTO roles (code, name) VALUES (:c, :c)", c=code)
        b.execute("INSERT INTO locations (code, name, loc_type) VALUES ('WH', 'Warehouse', 'warehouse')")
        b.execute("INSERT INTO locations (code, name, loc_type) VALUES ('SHOP', 'Shop', 'shop')")
        for name, role, loc in (("owner", "owner", None), ("whmgr", "warehouse_manager", WH),
                                ("whstaff", "warehouse_staff", WH), ("shopmgr", "shop_manager", SHOP),
                                ("shopstaff", "shop_staff", SHOP)):
            b.execute("INSERT INTO users (username, full_name, role_id, location_id) VALUES (:u, :u, "
                      "(SELECT id FROM roles WHERE code = :r), :l)", u=name, r=role, l=loc)
        self.uid = {r["username"]: r["id"] for r in b.all("SELECT id, username FROM users")}
        b.execute("INSERT INTO items (item_code, item_name, brand) VALUES ('TIR-0001', 'ANSU 12R22.5 18PR AL505', 'ANSU')")
        b.execute("INSERT INTO items (item_code, item_name, brand) VALUES ('TIR-0002', 'RODEO 1200R20 20PR RT201', 'RODEO')")
        b.execute("INSERT INTO item_location_settings (item_id, location_id, rop, max_level) VALUES (1, 2, 2, 10)")
        b.execute("INSERT INTO item_location_settings (item_id, location_id, rop, max_level) VALUES (2, 2, 2, 10)")
        inv.change_stock(A, WH, 20, "opening")
        inv.change_stock(B, WH, 10, "opening")

        # open RF with stock already reserved in the RF queue
        b.execute("INSERT INTO requisitions (rf_no, shop_id, warehouse_id, status, requested_by) "
                  "VALUES ('RF-2026-9001', 2, 1, 'open', (SELECT id FROM users WHERE username = 'shopmgr'))")
        b.execute("INSERT INTO requisition_items (rf_id, item_id, qty_requested, qty_reserved) VALUES (1, 1, 5, 5)")
        b.execute("INSERT INTO requisition_items (rf_id, item_id, qty_requested, qty_reserved) VALUES (1, 2, 3, 3)")
        inv.reserve(1, A, WH, 5, self.uid["whmgr"])
        inv.reserve(2, B, WH, 3, self.uid["whmgr"])

        b.execute("INSERT INTO drivers (full_name, contact_no, license_no, is_active) VALUES ('Dan Driver', '0917', 'L1', 1)")
        b.execute("INSERT INTO drivers (full_name, contact_no, license_no, is_active) VALUES ('Old Driver', '0918', 'L2', 0)")
        b.execute("INSERT INTO vehicles (plate_no, vehicle_type, is_active) VALUES ('ABC 123', 'Truck', 1)")
        b.execute("INSERT INTO vehicles (plate_no, vehicle_type, is_active) VALUES ('OLD 000', 'Van', 0)")

        # a sent PO for supplier receiving (REF 1, supplier 1)
        b.execute("INSERT INTO suppliers (supplier_name, email, is_regular) VALUES ('Sup A', 'a@sup.test', 1)")
        b.execute("INSERT INTO replenishments (ref_no, warehouse_id, status, created_by) "
                  "VALUES ('REF-2026-9001', 1, 'approved', (SELECT id FROM users WHERE username = 'whmgr'))")
        b.execute("INSERT INTO replenishment_items (ref_id, item_id, qty_requested) VALUES (1, 1, 10)")
        b.execute("INSERT INTO replenishment_items (ref_id, item_id, qty_requested) VALUES (1, 2, 5)")
        b.execute("INSERT INTO purchase_approvals (ref_id, supplier_id, basis, decision, submitted_by, decided_by) "
                  "VALUES (1, 1, 'agreed_price', 'approved', (SELECT id FROM users WHERE username = 'whmgr'), "
                  "(SELECT id FROM users WHERE username = 'owner'))")
        b.execute("INSERT INTO purchase_orders (po_no, ref_id, supplier_id, approval_id, po_date, status, total_amount) "
                  "VALUES ('PO-2026-9001', 1, 1, 1, '2026-11-01', 'sent', 0)")
        b.execute("INSERT INTO purchase_order_items (po_id, replenishment_item_id, item_id, qty_ordered, unit_price) "
                  "VALUES (1, 1, 1, 10, 10)")
        b.execute("INSERT INTO purchase_order_items (po_id, replenishment_item_id, item_id, qty_ordered, unit_price) "
                  "VALUES (1, 2, 2, 5, 20)")
        b.commit()

        self.app = create_app()
        self.app.config["TESTING"] = True
        self.app.config["DEV_LOGIN"] = True
        self.c = self.app.test_client()

    def login(self, name):
        self.c.get("/dev/user/%d" % self.uid[name])

    def flashes(self, resp):
        return re.findall(r'class="flash [a-z]+">([^<]*)<', resp.get_data(as_text=True))

    def ts_id(self):
        return self.be.one("SELECT id FROM transfer_slips ORDER BY id DESC LIMIT 1")["id"]

    def ts_items(self):
        return self.be.all("SELECT * FROM transfer_slip_items WHERE ts_id = :t ORDER BY id",
                           t=self.ts_id())

    def prepare(self, note=""):
        """Create the TS. note is the reason line A is sent short (only when it is)."""
        self.login("whmgr")
        return self.c.post("/delivery/transfers/new",
                           data={"rf_id": "1", "rf_item_id[]": ["1", "2"], "dispatch_note[]": [note, ""]})

    def assign(self, driver=1, vehicle=1):
        self.login("whmgr")
        return self.c.post("/delivery/transfers/%d/assign" % self.ts_id(),
                           data={"driver_id": str(driver), "vehicle_id": str(vehicle), "remarks": ""})

    def load(self, checked=None):
        self.login("whstaff")
        ids = [str(i["id"]) for i in self.ts_items()] if checked is None else checked
        return self.c.post("/delivery/transfers/%d/load" % self.ts_id(), data={"loaded[]": ids})

    def driver_signs(self, who="whstaff"):
        """The driver signs on the warehouse staff's screen after loading."""
        self.login(who)
        return self.c.post("/delivery/transfers/%d/driver-sign" % self.ts_id(),
                           data={"signature_data": make_png()})

    def release(self, driver_signs=True, **data):
        if driver_signs:
            self.driver_signs()
        self.login("whmgr")
        return self.c.post("/delivery/transfers/%d/release" % self.ts_id(), data=data)

    def chain(self, note=""):
        self.prepare(note)
        tid = self.ts_id()
        self.assign()
        self.load()
        self.release()
        return tid

    def receive_data(self, qty_a=None, qty_b=None, reason="", date="2026-11-03"):
        items = self.ts_items()
        expected = {i["item_id"]: i["qty_released"] for i in items}
        qty = dict(expected)
        if qty_a is not None:
            qty[A] = qty_a
        if qty_b is not None:
            qty[B] = qty_b
        return {"received_date": date,
                "ts_item_id[]": [str(i["id"]) for i in items],
                "counted[]": [str(i["id"]) for i in items],      # the shop manager ticked Counted on every line
                "qty_expected[]": [str(expected[i["item_id"]]) for i in items],
                "qty_accepted[]": [str(qty[i["item_id"]]) for i in items],
                "qty_rejected[]": [str(expected[i["item_id"]] - qty[i["item_id"]]) for i in items],
                "variance_reason[]": [reason if qty[i["item_id"]] != expected[i["item_id"]] else ""
                                      for i in items]}

    def receive(self, **kw):
        tid = self.ts_id()
        self.login("shopmgr")
        return self.c.post("/delivery/deliveries/%d/receive" % tid, data=self.receive_data(**kw))

    def receipt_data(self, a=(10, 10, 0, ""), b=(5, 5, 0, ""), dr="DR-1"):
        return {"supplier_dr_no": dr, "received_date": "2026-11-03",
                "po_item_id[]": ["1", "2"],
                "qty_delivered[]": [str(a[0]), str(b[0])],
                "qty_accepted[]": [str(a[1]), str(b[1])],
                "qty_rejected[]": [str(a[2]), str(b[2])],
                "reject_reason[]": [a[3], b[3]]}

    def receive_po(self, **kw):
        self.login("whstaff")
        return self.c.post("/delivery/receiving/1/check", data=self.receipt_data(**kw))


class TransferTests(Base):
    def test_document_is_default_and_release_keeps_actions(self):
        for role in ("shopmgr", "whmgr"):
            self.login(role)
            page = self.c.get("/requisition/rf/1").get_data(as_text=True)
            self.assertIn('class="card doc"', page)
            self.assertNotIn('>View document</a>', page)
        self.prepare()
        self.assign()
        self.login("whstaff")
        page = self.c.get("/delivery/transfers/1").get_data(as_text=True)
        self.assertIn('class="card doc"', page)
        self.assertIn('Load check', page)
        page = self.c.get("/delivery/transfers/1/load").get_data(as_text=True)
        self.assertIn('class="card doc"', page)
        self.assertIn('name="loaded[]"', page)
        self.load()
        self.login("whmgr")
        page = self.c.get("/delivery/transfers/1/release").get_data(as_text=True)
        self.assertIn('class="card doc"', page)
        self.assertIn('name="paper_reason"', page)
        self.assertIn('Sign &amp; release', page)

    def test_active_transfer_prevents_changing_reserved_quantities(self):
        self.be.execute("UPDATE requisition_items SET qty_requested = 8 WHERE id = 1")
        self.be.commit()
        self.prepare("only 5 in stock")
        self.login("whmgr")
        page = self.c.get("/inventory/rf-queue/1").get_data(as_text=True)
        self.assertNotIn('value="reserve_stock"', page)
        self.assertIn('Open TS-', page)
        response = self.c.post("/inventory/rf-queue/1", data={"rf_item_id[]": ["1"], "qty_reserve[]": ["1"]})
        self.assertIn('Finish or cancel transfer slip', response.get_data(as_text=True))
        self.assertEqual(inv.get_balance(A, WH)["reserved"], 5)
        self.c.post("/delivery/transfers/1/cancel")
        response = self.c.post("/inventory/rf-queue/1", data={"rf_item_id[]": ["1"], "qty_reserve[]": ["1"]})
        self.assertEqual(response.headers["Location"], "/delivery/transfers/new?rf_id=1")
        self.assertEqual(inv.get_balance(A, WH)["reserved"], 6)

    def test_short_dispatch_needs_a_reason(self):
        self.be.execute("UPDATE requisition_items SET qty_requested = 8 WHERE id = 1")
        self.be.commit()
        self.login("whmgr")
        page = self.c.get("/delivery/transfers/new?rf_id=1").get_data(as_text=True)
        self.assertIn("Short by", page)
        self.assertIn('<input type="text" name="dispatch_note[]" maxlength="200" required', page)
        form_at = page.index('<form method="post">')
        note_at = page.index('name="dispatch_note[]"')
        self.assertTrue(form_at < note_at < page.index("</form>", form_at))   # the browser sends it
        r = self.prepare()                                  # sent short with no reason
        self.assertIn("only 5 of 8 is being sent. Say why", " ".join(self.flashes(r)))
        self.assertIsNone(self.be.one("SELECT id FROM transfer_slips"))
        self.prepare("only 5 in stock")
        self.assertEqual(self.be.one("SELECT dispatch_note FROM transfer_slip_items WHERE ts_id = 1")["dispatch_note"],
                         "only 5 in stock")
        self.assertEqual(self.be.one("SELECT qty_needed FROM transfer_slip_items WHERE ts_id = 1")["qty_needed"], 8)

    def test_remaining_stock_can_be_reserved_while_transfer_is_in_transit(self):
        self.be.execute("UPDATE requisition_items SET qty_requested = 8 WHERE id = 1")
        self.be.commit()
        self.chain("only 5 in stock")
        self.login("whmgr")
        response = self.c.post("/inventory/rf-queue/1", data={"rf_item_id[]": ["1"], "qty_reserve[]": ["3"]})
        self.assertEqual(response.headers["Location"], "/delivery/transfers/1")
        self.assertEqual(inv.get_balance(A, WH)["reserved"], 3)

    def test_selected_transfer_form_only_shows_requested_rf(self):
        self.login("whmgr")
        page = self.c.get("/delivery/transfers/new?rf_id=1").get_data(as_text=True)
        self.assertIn('RF-2026-9001', page)
        self.assertIn('Create transfer', page)
        self.assertIn('ANSU 12R22.5 18PR AL505', page)
        self.assertIn('RODEO 1200R20 20PR RT201', page)
        self.assertIn("<th class=\"num\">Still needed</th><th class=\"num\">On TS</th>", page)
        self.assertRegex(page, re.compile(r'ANSU 12R22\.5 18PR AL505.*?<td class="num">5</td>', re.S))
        self.assertNotIn('RF-2026-9001', self.c.get("/delivery/transfers/new?rf_id=999").get_data(as_text=True))

    def test_queue_links_directly_to_next_step(self):
        self.login("whmgr")
        page = self.c.get("/inventory/rf-queue").get_data(as_text=True)
        self.assertIn('href="/delivery/transfers/new?rf_id=1">Create transfer', page)
        self.prepare()
        page = self.c.get("/inventory/rf-queue").get_data(as_text=True)
        self.assertIn('href="/delivery/transfers/1">Open transfer', page)
        self.c.post("/delivery/transfers/1/cancel")
        page = self.c.get("/inventory/rf-queue").get_data(as_text=True)
        self.assertIn('href="/delivery/transfers/new?rf_id=1">Create transfer', page)

    def test_shop_can_open_transfer_from_requisition_after_release(self):
        self.chain()
        self.login("shopmgr")
        page = self.c.get("/requisition/rf/1").get_data(as_text=True)
        self.assertIn('href="/delivery/transfers/1">TS-', page)
        response = self.c.get("/delivery/transfers/1")
        self.assertEqual(response.status_code, 200)
        self.assertIn('href="/delivery/deliveries">Back</a>', response.get_data(as_text=True))
        self.assertEqual(self.c.get("/delivery/deliveries").status_code, 200)

    def test_selected_transfer_keeps_fields_on_validation_error(self):
        self.login("whmgr")
        response = self.c.post("/delivery/transfers/new?rf_id=1", data={"rf_id": "1", "driver_id": "1", "vehicle_id": "2"})
        self.assertEqual(response.status_code, 200)
        self.assertIn('Choose an active vehicle', response.get_data(as_text=True))
        self.assertIn('value="1" selected>Dan Driver', response.get_data(as_text=True))
        self.assertEqual(self.be.one("SELECT COUNT(*) AS n FROM transfer_slips")["n"], 0)

    def test_prepare_list_and_draft(self):
        self.login("whmgr")
        page = self.c.get("/delivery/transfers/new").get_data(as_text=True)
        self.assertIn("RF-2026-9001", page)
        r = self.prepare()
        self.assertEqual(r.status_code, 302)
        ts = self.be.one("SELECT * FROM transfer_slips")
        self.assertEqual((ts["status"], ts["from_location_id"], ts["to_location_id"]), ("draft", WH, SHOP))
        self.assertEqual(ts["ts_no"], "TS-%d-0001" % dt.date.today().year)
        lines = {l["item_id"]: l["qty_released"] for l in self.ts_items()}
        self.assertEqual(lines, {A: 5, B: 3})
        self.assertEqual(inv.get_balance(A, WH)["reserved"], 5)       # nothing released yet

    def test_one_ts_at_a_time_and_cancel_frees_it(self):
        self.prepare()
        r = self.prepare()
        self.assertEqual(r.status_code, 200)
        self.assertIn("already being prepared", " ".join(self.flashes(r)))
        self.assertEqual(self.be.one("SELECT COUNT(*) AS n FROM transfer_slips")["n"], 1)
        self.login("whmgr")
        self.c.post("/delivery/transfers/%d/cancel" % self.ts_id())
        self.assertEqual(self.be.one("SELECT status FROM transfer_slips")["status"], "cancelled")
        self.assertEqual(inv.get_balance(A, WH)["reserved"], 5)       # still held for the RF
        r = self.prepare()
        self.assertEqual(r.status_code, 302)
        self.assertEqual(self.be.one("SELECT COUNT(*) AS n FROM transfer_slips")["n"], 2)

    def test_assign_requires_active_driver_and_vehicle(self):
        self.prepare()
        r = self.assign(driver=2)                                   # inactive
        self.assertEqual(r.status_code, 200)
        self.assertIn("active driver", " ".join(self.flashes(r)))
        r = self.assign(vehicle=2)
        self.assertIn("active vehicle", " ".join(self.flashes(r)))
        r = self.assign()
        self.assertEqual(r.status_code, 302)
        ts = self.be.one("SELECT * FROM transfer_slips")
        self.assertEqual((ts["status"], ts["driver_id"], ts["vehicle_id"]), ("assigned", 1, 1))

    def test_loading_check_needs_every_line(self):
        self.prepare()
        self.assign()
        r = self.load(checked=[])
        self.assertEqual(r.status_code, 200)
        self.assertIn("Tick every line", " ".join(self.flashes(r)))
        self.assertEqual(self.be.one("SELECT status FROM transfer_slips")["status"], "assigned")
        r = self.load()
        self.assertEqual(r.status_code, 302)
        ts = self.be.one("SELECT * FROM transfer_slips")
        self.assertEqual((ts["status"], ts["loaded_by"]), ("loaded", self.uid["whstaff"]))

    def test_release_deducts_warehouse_and_closes_reservations(self):
        self.prepare()
        self.assign()
        self.load()
        r = self.release()
        self.assertEqual(r.status_code, 302)
        self.assertEqual(inv.get_balance(A, WH)["on_hand"], 15)
        self.assertEqual(inv.get_balance(B, WH)["on_hand"], 7)
        self.assertEqual(inv.get_balance(A, WH)["reserved"], 0)
        ts = self.be.one("SELECT * FROM transfer_slips")
        self.assertEqual((ts["status"], ts["released_by"]), ("in_transit", self.uid["whmgr"]))
        self.assertEqual(self.be.one("SELECT COUNT(*) AS n FROM reservations WHERE status = 'released'")["n"], 2)
        self.assertEqual(self.be.one("SELECT status FROM requisitions")["status"], "open")

    def test_release_needs_loaded_ts(self):
        self.prepare()
        self.login("whmgr")
        r = self.c.post("/delivery/transfers/%d/release" % self.ts_id(), follow_redirects=True)
        self.assertEqual(self.be.one("SELECT status FROM transfer_slips")["status"], "draft")
        self.assertIn("loaded TS", " ".join(self.flashes(r)))

    def test_cancel_rf_is_blocked_while_a_ts_is_in_progress(self):
        self.prepare()
        self.login("shopmgr")
        r = self.c.post("/requisition/rf/1/cancel", data={"cancel_reason": "changed mind"},
                        follow_redirects=True)
        self.assertEqual(r.status_code, 200)
        self.assertIn("Transfer slip", " ".join(self.flashes(r)))
        self.assertEqual(self.be.one("SELECT status FROM requisitions")["status"], "open")


class ShopReceivingTests(Base):
    def test_shop_sees_incoming_and_full_receipt_closes_rf(self):
        self.chain()
        self.login("shopmgr")
        page = self.c.get("/delivery/deliveries").get_data(as_text=True)
        self.assertIn("TS-", page)
        self.assertEqual(self.c.get("/delivery/deliveries/%d/receive" % self.ts_id()).status_code, 200)
        r = self.receive()
        self.assertEqual(r.status_code, 302)
        ar = self.be.one("SELECT * FROM acknowledgment_receipts")
        self.assertEqual((ar["ar_no"], bool(ar["has_variance"])), ("AR-%d-0001" % dt.date.today().year, False))
        self.assertEqual(inv.get_balance(A, SHOP)["on_hand"], 5)
        self.assertEqual(inv.get_balance(B, SHOP)["on_hand"], 3)
        self.assertEqual(self.be.one("SELECT status FROM transfer_slips")["status"], "delivered")
        self.assertEqual(self.be.one("SELECT status FROM requisitions")["status"], "closed")
        self.assertEqual(self.be.one("SELECT qty_delivered FROM requisition_items WHERE item_id = 1")["qty_delivered"], 5)

    def test_short_receipt_needs_a_reason_and_records_variance(self):
        self.chain()
        r = self.receive(qty_a=4)
        self.assertEqual(r.status_code, 200)
        self.assertIn("reason is required", " ".join(self.flashes(r)))
        self.assertEqual(self.be.one("SELECT status FROM transfer_slips")["status"], "in_transit")

        r = self.receive(qty_a=4, reason="one tire missing")
        self.assertEqual(r.status_code, 302)
        ar = self.be.one("SELECT * FROM acknowledgment_receipts")
        self.assertTrue(ar["has_variance"])
        self.assertEqual(inv.get_balance(A, SHOP)["on_hand"], 4)
        self.assertEqual(self.be.one("SELECT status FROM transfer_slips")["status"], "delivered_with_variance")
        self.assertEqual(self.be.one("SELECT status FROM requisitions")["status"], "partially_delivered")
        item = self.be.one("SELECT * FROM acknowledgment_receipt_items WHERE item_id = 1")
        self.assertEqual((item["qty_expected"], item["qty_received"], item["variance_reason"]),
                         (5, 4, "one tire missing"))
        self.assertIsNone(item["variance_resolved_at"])
        # rejected 1 went back to warehouse stock, and the RF line can be sent again
        self.assertEqual(inv.get_balance(A, WH)["on_hand"], 16)
        rf_item = self.be.one("SELECT * FROM requisition_items WHERE item_id = 1")
        self.assertEqual((rf_item["qty_reserved"], rf_item["qty_delivered"]), (4, 4))

    def test_resent_shop_shortage_resolves_the_variance(self):
        self.chain()
        self.receive(qty_a=4, reason="one tire missing")
        # warehouse reserves and sends the remaining 1 unit
        inv.reserve(1, A, WH, 1, self.uid["whmgr"])
        self.be.execute("UPDATE requisition_items SET qty_reserved = qty_reserved + 1 WHERE id = 1")
        self.be.commit()
        self.prepare()                                  # the remainder is the whole line, so no reason is asked
        self.assertIsNone(self.ts_items()[0]["dispatch_note"])
        self.assertEqual(self.ts_items()[0]["qty_needed"], 1)   # only the unit still owed
        self.assign()
        self.load()
        self.release()
        r = self.receive()
        self.assertEqual(r.status_code, 302)
        first = self.be.one("SELECT * FROM acknowledgment_receipt_items WHERE item_id = 1 ORDER BY id LIMIT 1")
        self.assertIsNotNone(first["variance_resolved_at"])
        # the remainder is measured against the 1 unit it was sent for, not the RF total of 5
        page = self.c.get("/documents/AR/2").get_data(as_text=True)
        self.assertIn('class="num diff-zero">0</td>', page)
        self.assertNotIn("Yes (", page)
        self.assertNotIn("diff-short", page)
        self.assertEqual(self.be.one("SELECT status FROM requisitions")["status"], "closed")
        self.assertEqual(inv.get_balance(A, SHOP)["on_hand"], 5)
        self.assertEqual(self.be.one("SELECT qty_delivered FROM requisition_items WHERE id = 1")["qty_delivered"], 5)

    def test_discrepancies_list_is_read_only(self):
        self.chain()
        self.receive(qty_a=4, reason="one tire missing")
        ar = self.be.one("SELECT * FROM acknowledgment_receipts")
        ari = self.be.one("SELECT id FROM acknowledgment_receipt_items WHERE item_id = 1")
        for who in ("owner", "whmgr"):
            self.login(who)
            page = self.c.get("/delivery/variances").get_data(as_text=True)
            self.assertIn(ar["ar_no"], page)
            self.assertIn("one tire missing", page)
            self.assertIn("/documents/AR/%d" % ar["id"], page)
            self.assertNotIn('name="resolution_note"', page)
            self.assertNotIn("/variances/%d/resolve" % ari["id"], page)

    def test_approving_an_adjustment_linked_to_the_ar_resolves_the_variance(self):
        self.chain()
        self.receive(qty_a=4, reason="one tire missing")
        ar = self.be.one("SELECT * FROM acknowledgment_receipts")
        self.login("whstaff")
        r = self.c.post("/inventory/adjustments/new", data={
            "reason": "Delivery variance", "ar_no": ar["ar_no"], "remarks": "written off at the shop",
            "item_id[]": ["1"], "qty_actual[]": ["15"], "item_remarks[]": ["short at shop"]})
        self.assertEqual(r.status_code, 302, self.flashes(r))
        adj_id = int(r.headers["Location"].rstrip("/").split("/")[-1])
        page = self.c.get("/inventory/adjustments/%d" % adj_id).get_data(as_text=True)
        self.assertIn(ar["ar_no"], page)
        self.assertIn("/documents/AR/%d" % ar["id"], page)                      # the link is shown
        self.assertIsNone(self.be.one("SELECT variance_resolved_at FROM acknowledgment_receipt_items "
                                      "WHERE item_id = 1")["variance_resolved_at"])
        self.login("whmgr")
        self.c.post("/inventory/adjustments/%d/review" % adj_id,
                    data={"decision": "approve", "review_remarks": "approved write-off"})
        self.assertIsNotNone(self.be.one("SELECT variance_resolved_at FROM acknowledgment_receipt_items "
                                         "WHERE item_id = 1")["variance_resolved_at"])
        audit = self.be.one("SELECT * FROM audit_log WHERE table_name = 'acknowledgment_receipt_items' "
                            "AND action = 'resolve'")
        self.assertIn("adjustment", audit["new_values"])

    def test_a_manager_linked_adjustment_resolves_the_variance_on_save(self):
        self.chain()
        self.receive(qty_a=4, reason="one tire missing")
        ar = self.be.one("SELECT * FROM acknowledgment_receipts")
        self.login("whmgr")
        r = self.c.post("/inventory/adjustments/new", data={
            "reason": "Delivery variance", "ar_no": ar["ar_no"],
            "item_id[]": ["1"], "qty_actual[]": ["15"]})
        self.assertEqual(r.status_code, 302, self.flashes(r))
        self.assertIsNotNone(self.be.one("SELECT variance_resolved_at FROM acknowledgment_receipt_items "
                                         "WHERE item_id = 1")["variance_resolved_at"])

    def test_cannot_receive_a_ts_that_is_not_in_transit(self):
        self.prepare()
        self.login("shopmgr")
        r = self.c.get("/delivery/deliveries/%d/receive" % self.ts_id(), follow_redirects=True)
        self.assertIn("not in transit", " ".join(self.flashes(r)))

    def test_owner_and_warehouse_cannot_sign_the_ar(self):
        self.chain()
        for who in ("owner", "whmgr", "shopstaff"):
            self.login(who)
            self.assertEqual(self.c.post("/delivery/deliveries/%d/receive" % self.ts_id(),
                                         data=self.receive_data()).status_code, 403)


class SupplierReceivingTests(Base):
    def test_receiving_list_and_full_receipt(self):
        self.login("whstaff")
        page = self.c.get("/delivery/receiving").get_data(as_text=True)
        self.assertIn("PO-2026-9001", page)
        self.assertEqual(self.c.get("/delivery/receiving/1/check").status_code, 200)
        r = self.receive_po()
        self.assertEqual(r.status_code, 302)
        rm = self.be.one("SELECT * FROM goods_receipts")
        self.assertEqual(rm["rm_no"], "RM-%d-0001" % dt.date.today().year)
        self.assertEqual(inv.get_balance(A, WH)["on_hand"], 30)
        self.assertEqual(inv.get_balance(B, WH)["on_hand"], 15)
        self.assertEqual(self.be.one("SELECT status FROM purchase_orders")["status"], "received")
        self.assertEqual(self.be.one("SELECT status FROM replenishments")["status"], "closed")
        self.assertEqual(self.be.one("SELECT COUNT(*) AS n FROM discrepancy_reports")["n"], 0)
        self.assertEqual(self.be.one("SELECT qty_received FROM purchase_order_items WHERE id = 1")["qty_received"], 10)

    def test_short_and_rejected_create_a_discrepancy(self):
        r = self.receive_po(b=(4, 3, 1, "damaged casing"))
        self.assertEqual(r.status_code, 302)
        self.assertEqual(inv.get_balance(B, WH)["on_hand"], 13)      # only accepted added
        self.assertEqual(self.be.one("SELECT status FROM purchase_orders")["status"], "partially_received")
        self.assertEqual(self.be.one("SELECT status FROM replenishments")["status"], "receiving")
        rep = self.be.one("SELECT * FROM discrepancy_reports")
        self.assertEqual(rep["report_no"], "DR-%d-0001" % dt.date.today().year)
        item = self.be.one("SELECT * FROM discrepancy_items")
        self.assertEqual((item["item_id"], item["qty_short"], item["qty_rejected"], item["reason"]),
                         (B, 1, 1, "damaged casing"))
        self.assertEqual(self.be.one("SELECT qty_received FROM purchase_order_items WHERE id = 2")["qty_received"], 3)

        # second receipt finishes the PO and covers the discrepancy: it resolves itself
        r = self.receive_po(a=(0, 0, 0, ""), b=(2, 2, 0, ""), dr="DR-2")
        self.assertEqual(r.status_code, 302)
        self.assertEqual(self.be.one("SELECT status FROM purchase_orders")["status"], "received")
        self.assertEqual(self.be.one("SELECT status FROM replenishments")["status"], "closed")
        rep = self.be.one("SELECT * FROM discrepancy_reports")
        self.assertEqual(rep["status"], "resolved")
        self.assertIsNotNone(rep["resolved_at"])
        self.assertIn("Automatically resolved", rep["follow_up_notes"])
        item = self.be.one("SELECT * FROM discrepancy_items")
        self.assertEqual(item["qty_replaced"], 2)                 # short 1 + rejected 1
        self.assertEqual(item["qty_short"] + item["qty_rejected"], 2)

    def test_repeatedly_rejected_replacement_stays_on_the_same_report(self):
        self.receive_po(b=(4, 3, 1, "damaged"))                  # DR: short 1, rejected 1
        r = self.receive_po(a=(0, 0, 0, ""), b=(1, 0, 1, "damaged again"), dr="DR-2")
        self.assertEqual(r.status_code, 302)
        self.assertEqual(self.be.one("SELECT COUNT(*) AS n FROM discrepancy_reports")["n"], 1)
        item = self.be.one("SELECT * FROM discrepancy_items")
        self.assertEqual((item["qty_short"], item["qty_rejected"], item["qty_replaced"]), (1, 1, 0))

    def test_accepted_fills_in_when_rejected_is_left_blank(self):
        r = self.receive_po(b=(4, 3, "", "damaged"))             # delivered 4, accepted 3 -> rejected 1
        self.assertEqual(r.status_code, 302)
        row = self.be.one("SELECT * FROM goods_receipt_items WHERE po_item_id = 2")
        self.assertEqual((row["qty_accepted"], row["qty_rejected"]), (3, 1))

    def test_rejected_fills_in_when_accepted_is_left_blank(self):
        r = self.receive_po(b=(4, "", 1, "damaged"))             # delivered 4, rejected 1 -> accepted 3
        self.assertEqual(r.status_code, 302)
        row = self.be.one("SELECT * FROM goods_receipt_items WHERE po_item_id = 2")
        self.assertEqual((row["qty_accepted"], row["qty_rejected"]), (3, 1))

    def test_receiving_validation(self):
        bad = [dict(a=(10, 9, 0, "")),                       # accepted != delivered
               dict(a=(11, 11, 0, "")),                      # more than ordered
               dict(b=(5, 4, 1, ""))]                        # rejected without a reason
        for kw in bad:
            r = self.receive_po(**kw)
            self.assertEqual(r.status_code, 200)
        self.assertEqual(self.be.one("SELECT COUNT(*) AS n FROM goods_receipts")["n"], 0)
        self.login("whstaff")
        r = self.c.post("/delivery/receiving/1/check", data=dict(self.receipt_data(), supplier_dr_no=""))
        self.assertEqual(r.status_code, 200)
        self.assertIn("delivery receipt number", " ".join(self.flashes(r)))

    def test_receiving_memo_and_owner_view(self):
        self.receive_po(b=(4, 3, 1, "damaged"))
        rm_id = self.be.one("SELECT id FROM goods_receipts")["id"]
        self.login("whstaff")
        self.assertEqual(self.c.get("/delivery/receiving/rm/%d" % rm_id).status_code, 200)
        self.login("owner")
        page = self.c.get("/delivery/receiving/rm/%d" % rm_id)
        self.assertEqual(page.status_code, 200)
        self.assertIn("RM-", page.get_data(as_text=True))

    def test_discrepancy_follow_up_and_resolve(self):
        self.receive_po(b=(4, 3, 1, "damaged"))
        rid = self.be.one("SELECT id FROM discrepancy_reports")["id"]
        self.login("whmgr")
        self.assertIn("DR-", self.c.get("/delivery/discrepancies").get_data(as_text=True))
        r = self.c.post("/delivery/discrepancies/%d" % rid,
                        data={"status": "resolved", "follow_up_notes": "supplier credited"})
        self.assertEqual(r.status_code, 302)
        rep = self.be.one("SELECT * FROM discrepancy_reports WHERE id = :r", r=rid)
        self.assertEqual((rep["status"], rep["follow_up_notes"]), ("resolved", "supplier credited"))
        self.assertIsNotNone(rep["resolved_at"])
        self.c.post("/delivery/discrepancies/%d" % rid, data={"status": "follow_up", "follow_up_notes": "reopened"})
        rep = self.be.one("SELECT * FROM discrepancy_reports WHERE id = :r", r=rid)
        self.assertEqual((rep["status"], rep["resolved_at"]), ("follow_up", None))


class DriverVehicleTests(Base):
    def test_driver_crud_and_inactive_hidden_from_assignment(self):
        self.login("whmgr")
        r = self.c.post("/delivery/drivers/new", data={"full_name": "New Driver", "is_active": "on"})
        self.assertEqual(r.status_code, 302)
        self.assertIsNotNone(self.be.one("SELECT id FROM drivers WHERE full_name = 'New Driver'"))
        self.c.post("/delivery/drivers/1/edit", data={"full_name": "Dan Driver", "contact_no": "0999"})
        d = self.be.one("SELECT * FROM drivers WHERE id = 1")
        self.assertEqual((d["contact_no"], bool(d["is_active"])), ("0999", False))
        self.prepare()
        self.login("whmgr")
        page = self.c.get("/delivery/transfers/%d/assign" % self.ts_id()).get_data(as_text=True)
        self.assertNotIn("Dan Driver", page)                       # inactive now
        self.assertIn("New Driver", page)

    def test_vehicle_crud_and_duplicate_plate(self):
        self.login("whmgr")
        r = self.c.post("/delivery/vehicles/new", data={"plate_no": "XYZ 789", "vehicle_type": "Truck", "is_active": "on"})
        self.assertEqual(r.status_code, 302)
        r = self.c.post("/delivery/vehicles/new", data={"plate_no": "xyz 789", "is_active": "on"})
        self.assertEqual(r.status_code, 200)
        self.assertIn("already on another vehicle", " ".join(self.flashes(r)))
        self.assertEqual(self.be.one("SELECT COUNT(*) AS n FROM vehicles")["n"], 3)


class PermissionTests(Base):
    def test_screen_permissions(self):
        self.login("shopmgr")
        self.assertEqual(self.c.get("/delivery/transfers").status_code, 403)
        self.assertEqual(self.c.get("/delivery/transfers/new").status_code, 403)
        self.login("whstaff")
        self.assertEqual(self.c.get("/delivery/transfers/new").status_code, 403)
        self.assertEqual(self.c.get("/delivery/drivers").status_code, 403)
        self.assertEqual(self.c.get("/delivery/receiving").status_code, 200)
        self.assertEqual(self.c.get("/delivery/discrepancies").status_code, 403)
        self.login("whmgr")
        self.assertEqual(self.c.get("/delivery/deliveries").status_code, 403)
        self.assertEqual(self.c.get("/delivery/transfers").status_code, 200)

    def test_shop_cannot_open_a_draft_transfer(self):
        self.prepare()
        self.login("shopmgr")
        self.assertEqual(self.c.get("/delivery/transfers/%d" % self.ts_id()).status_code, 404)
        self.login("whmgr")
        self.assertEqual(self.c.get("/delivery/transfers/%d" % self.ts_id()).status_code, 200)

    def test_owner_can_view_a_transfer_but_not_change_it(self):
        self.prepare()
        self.login("owner")
        self.assertEqual(self.c.get("/delivery/transfers").status_code, 200)
        self.assertEqual(self.c.get("/delivery/transfers/%d" % self.ts_id()).status_code, 200)
        self.assertEqual(self.c.get("/delivery/transfers/%d/assign" % self.ts_id()).status_code, 403)

    def test_audit_rows_written(self):
        self.chain()
        self.receive()
        self.receive_po()
        actions = {r["action"] for r in self.be.all("SELECT action FROM audit_log")}
        self.assertTrue({"create", "assign", "load", "release", "receive"} <= actions)


if __name__ == "__main__":
    unittest.main()
