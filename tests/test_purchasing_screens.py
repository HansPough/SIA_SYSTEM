"""Click-through tests for Module 3 (Purchasing): alerts, REF, RFQ, quotations, approvals,
purchase orders and suppliers. Uses the same in-memory SQLite copy as the other tests.
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
SUP_A, SUP_B = 1, 2          # Sup A is a regular supplier with agreed prices


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
        b.execute("INSERT INTO item_location_settings (item_id, location_id, rop, max_level, lead_time_days) VALUES (1, 1, 8, 33, 20)")
        b.execute("INSERT INTO item_location_settings (item_id, location_id, rop, max_level, lead_time_days) VALUES (2, 1, 5, 17, 20)")
        inv.change_stock(A, WH, 3, "opening")          # below ROP 8 -> suggested 30
        # B has no stock -> shortage, suggested 17
        b.commit()

        b.execute("INSERT INTO suppliers (supplier_name, contact_person, email, is_regular) "
                  "VALUES ('Sup A', 'Ana', 'ana@supa.test', 1)")
        b.execute("INSERT INTO suppliers (supplier_name, contact_person, email, is_regular) "
                  "VALUES ('Sup B', 'Ben', 'ben@supb.test', 0)")
        b.execute("INSERT INTO supplier_items (supplier_id, item_id, agreed_price, valid_from, valid_to) "
                  "VALUES (1, 1, 10.00, '2026-01-01', NULL)")
        b.execute("INSERT INTO supplier_items (supplier_id, item_id, agreed_price, valid_from, valid_to) "
                  "VALUES (1, 2, 20.00, '2026-01-01', NULL)")

        # a shop RF still waiting for B, so a shortage line can point at it
        b.execute("INSERT INTO requisitions (rf_no, shop_id, warehouse_id, status, requested_by) "
                  "VALUES ('RF-2026-9001', 2, 1, 'open', (SELECT id FROM users WHERE username = 'shopmgr'))")
        b.execute("INSERT INTO requisition_items (rf_id, item_id, qty_requested) VALUES (1, 2, 4)")

        b.execute("INSERT INTO notifications (notif_type, location_id, status) VALUES ('restock', 1, 'active')")
        b.execute("INSERT INTO notification_items (notification_id, item_id, current_qty, rop, suggested_qty, reason) "
                  "VALUES (1, 1, 3, 8, 30, 'below_rop')")
        b.execute("INSERT INTO notification_items (notification_id, item_id, current_qty, rop, suggested_qty, reason) "
                  "VALUES (1, 2, 0, 5, 17, 'shortage')")
        b.commit()

        self.app = create_app()
        self.app.config["TESTING"] = True
        self.app.config["DEV_LOGIN"] = True
        self.c = self.app.test_client()

    def login(self, name):
        self.c.get("/dev/user/%d" % self.uid[name])

    def flashes(self, resp):
        return re.findall(r'class="flash [a-z]+">([^<]*)<', resp.get_data(as_text=True))

    def make_ref(self, submit=True, item=A, qty=5, reason="below_rop", alert_id=1):
        data = {"remarks": "test", "item_id[]": [str(item)], "qty_requested[]": [str(qty)],
                "reason[]": [reason], "action": "submit_ref" if submit else "save_draft"}
        if alert_id:
            data["alert_id"] = str(alert_id)
        self.login("whmgr")
        return self.c.post("/purchasing/ref/new", data=data)

    def ref_id(self):
        return self.be.one("SELECT id FROM replenishments ORDER BY id DESC LIMIT 1")["id"]

    def line_ids(self, ref_id):
        return {r["item_id"]: r["id"] for r in
                self.be.all("SELECT id, item_id FROM replenishment_items WHERE ref_id = :r", r=ref_id)}

    def send_rfq(self, ref_id, suppliers=(SUP_A, SUP_B)):
        self.login("whmgr")
        return self.c.post("/purchasing/ref/%d/rfq/new" % ref_id,
                           data={"supplier_ids[]": [str(s) for s in suppliers],
                                 "reply_deadline": "2026-12-01", "terms": "30 days"})

    def quote(self, ref_id, supplier=SUP_A, lines=None, **extra):
        """lines: list of (item_id, qty, price, lead). Defaults to the REF's own items."""
        ids = self.line_ids(ref_id)
        if lines is None:
            lines = []
            for item_id in ids:
                lines.append((item_id, 5, "10.00", 7) if item_id == A else (item_id, 2, "20.00", 7))
        data = {"supplier_id": str(supplier), "quotation_date": "2026-11-01",
                "ref_item_id[]": [str(ids[i]) for i, _q, _p, _l in lines],
                "qty_offered[]": [str(q) for _i, q, _p, _l in lines],
                "unit_price[]": [p for _i, _q, p, _l in lines],
                "lead_time_days[]": [str(lead) for _i, _q, _p, lead in lines]}
        data.update(extra)
        self.login("whmgr")
        return self.c.post("/purchasing/ref/%d/quotation/new" % ref_id, data=data)

    def choose_offer(self, ref_id, supplier=SUP_A, basis="quotation"):
        """Post per-item choices: everything this supplier offers, other lines keep open."""
        if basis == "quotation":
            q = self.be.one("SELECT id FROM quotations WHERE supplier_id = :s ORDER BY id DESC LIMIT 1", s=supplier)
            offered = {r["replenishment_item_id"] for r in self.be.all(
                "SELECT replenishment_item_id FROM quotation_items WHERE quotation_id = :q AND qty_offered > 0",
                q=q["id"])}
            key = "q:%d" % q["id"]
        else:
            offered = None
            key = "a:%d" % supplier
        data = {}
        for r in self.be.all("SELECT id FROM replenishment_items WHERE ref_id = :r", r=ref_id):
            if offered is None or r["id"] in offered:
                data["choice_%d" % r["id"]] = key
            else:
                data["choice_%d" % r["id"]] = "open"
        self.login("whmgr")
        return self.c.post("/purchasing/ref/%d/compare" % ref_id, data=data)

    def compare_quote(self, ref_id, supplier=SUP_A):
        return self.choose_offer(ref_id, supplier, "quotation")

    def compare_agreed(self, ref_id, supplier=SUP_A):
        return self.choose_offer(ref_id, supplier, "agreed_price")

    def approval_id(self):
        return self.be.one("SELECT id FROM purchase_approvals ORDER BY id DESC LIMIT 1")["id"]


class AlertTests(Base):
    def test_restock_alert_pages_and_scope(self):
        self.login("whmgr")
        page = self.c.get("/purchasing/alerts").get_data(as_text=True)
        self.assertIn("ALT-0001", page)
        detail = self.c.get("/purchasing/alerts/1").get_data(as_text=True)
        self.assertIn("ANSU 12R22.5 18PR AL505", detail)
        self.assertIn("30", detail)                  # 3 available, ROP 8, suggested 30
        for who in ("owner", "shopmgr", "whstaff"):
            self.login(who)
            self.assertEqual(self.c.get("/purchasing/alerts").status_code, 403)
            self.assertEqual(self.c.get("/purchasing/alerts/1").status_code, 403)

    def test_dismiss_needs_a_reason(self):
        self.login("whmgr")
        r = self.c.post("/purchasing/alerts/1/dismiss", data={"dismissed_reason": " "})
        self.assertEqual(r.status_code, 200)
        self.assertIn("reason is required", " ".join(self.flashes(r)))
        self.assertEqual(self.be.one("SELECT status FROM notifications WHERE id = 1")["status"], "active")
        r = self.c.post("/purchasing/alerts/1/dismiss", data={"dismissed_reason": "already ordered"})
        self.assertEqual(r.status_code, 302)
        row = self.be.one("SELECT * FROM notifications WHERE id = 1")
        self.assertEqual((row["status"], row["dismissed_reason"], row["closed_by"]),
                         ("dismissed", "already ordered", self.uid["whmgr"]))


class ReplenishmentFormTests(Base):
    def test_ref_from_alert_is_prefilled_and_submitting_removes_only_its_items(self):
        self.login("whmgr")
        page = self.c.get("/purchasing/ref/new?alert_id=1").get_data(as_text=True)
        self.assertIn("ANSU 12R22.5 18PR AL505", page)
        self.assertIn('value="30"', page)
        r = self.make_ref(qty=30)
        self.assertEqual(r.status_code, 302)
        ref = self.be.one("SELECT * FROM replenishments")
        self.assertEqual((ref["status"], ref["warehouse_id"], ref["notification_id"]), ("submitted", WH, 1))
        self.assertIsNotNone(ref["submitted_at"])
        # B is not on this REF, so it stays on the alert for the next order
        alert = self.be.one("SELECT status, closed_by FROM notifications WHERE id = 1")
        self.assertEqual((alert["status"], alert["closed_by"]), ("active", None))
        left = self.be.all("SELECT item_id FROM notification_items WHERE notification_id = 1")
        self.assertEqual([r["item_id"] for r in left], [B])
        self.assertEqual(ref["ref_no"], "REF-%d-0001" % __import__("datetime").date.today().year)

    def test_ref_covering_every_alert_item_completes_the_alert(self):
        self.login("whmgr")
        r = self.c.post("/purchasing/ref/new", data={
            "remarks": "both lines", "alert_id": "1",
            "item_id[]": [str(A), str(B)], "qty_requested[]": ["30", "17"],
            "reason[]": ["below_rop", "shortage"], "action": "submit_ref"})
        self.assertEqual(r.status_code, 302)
        alert = self.be.one("SELECT status, closed_by FROM notifications WHERE id = 1")
        self.assertEqual((alert["status"], alert["closed_by"]), ("completed", self.uid["whmgr"]))
        self.assertEqual(self.be.one("SELECT COUNT(*) AS n FROM notification_items WHERE notification_id = 1")["n"], 0)

    def test_draft_keeps_alert_active_until_submitted(self):
        r = self.make_ref(submit=False)
        self.assertEqual(r.status_code, 302)
        ref = self.be.one("SELECT * FROM replenishments")
        self.assertEqual((ref["status"], ref["submitted_at"]), ("draft", None))
        self.assertEqual(self.be.one("SELECT status FROM notifications WHERE id = 1")["status"], "active")
        self.login("whmgr")
        detail = self.c.get("/purchasing/ref/%d" % ref["id"]).get_data(as_text=True)
        self.assertIn("Sign &amp; submit REF", detail)
        self.assertEqual(self.be.one("SELECT COUNT(*) AS n FROM document_signatures")["n"], 0)    # draft: unsigned
        self.c.post("/purchasing/ref/%d" % ref["id"], data={"action": "submit_ref"})
        self.assertEqual(self.be.one("SELECT status FROM replenishments")["status"], "submitted")
        sig = self.be.one("SELECT * FROM document_signatures")
        self.assertEqual((sig["doc_type"], sig["slot"], sig["signer_name"]), ("REF", "prepared_by", "whmgr"))
        self.assertEqual(self.be.one("SELECT status FROM notifications WHERE id = 1")["status"], "active")
        left = self.be.all("SELECT item_id FROM notification_items WHERE notification_id = 1")
        self.assertEqual([r["item_id"] for r in left], [B])

    def test_ref_progress_is_one_items_table_not_boxes(self):
        self.make_ref()
        self.login("whmgr")
        page = self.c.get("/purchasing/ref/1").get_data(as_text=True)
        progress = page[page.index("<h2>Progress</h2>"):page.index("<h2>RFQs and quotations</h2>")]
        self.assertIn('<table class="data">', progress)
        for heading in ("Item", "Requested", "Still open", "Ordered", "Received", "Dropped", "For RF"):
            self.assertIn(">%s<" % heading, progress)
        self.assertIn("TIR-0001", progress)
        self.assertIn('name="reason"', progress)                    # Give up sits in the row
        self.assertNotIn('class="side-item"', page)

    def test_shortage_line_links_the_waiting_rf(self):
        self.make_ref(item=B, qty=4, reason="shortage")
        line = self.be.one("SELECT * FROM replenishment_items")
        self.assertEqual((line["reason"], line["rf_id"]), ("shortage", 1))

    def test_cancel_needs_reason_and_is_blocked_later(self):
        self.make_ref(submit=True)
        ref_id = self.ref_id()
        self.login("whmgr")
        r = self.c.post("/purchasing/ref/%d/cancel" % ref_id, data={"cancel_reason": ""})
        self.assertEqual(r.status_code, 200)
        self.assertIn("reason is required", " ".join(self.flashes(r)))
        r = self.c.post("/purchasing/ref/%d/cancel" % ref_id, data={"cancel_reason": "raised twice"})
        self.assertEqual(r.status_code, 302)
        row = self.be.one("SELECT * FROM replenishments WHERE id = :r", r=ref_id)
        self.assertEqual((row["status"], row["cancel_reason"]), ("cancelled", "raised twice"))

        self.make_ref(submit=True)
        rid = self.ref_id()
        self.be.execute("UPDATE replenishments SET status = 'approved' WHERE id = :r", r=rid)
        self.be.commit()
        self.login("whmgr")
        self.assertEqual(self.c.get("/purchasing/ref/%d/cancel" % rid).status_code, 302)
        self.assertEqual(self.be.one("SELECT status FROM replenishments WHERE id = :r", r=rid)["status"], "approved")

    def test_owner_cannot_create_and_shop_cannot_see(self):
        self.login("owner")
        self.assertEqual(self.c.get("/purchasing/ref/new").status_code, 403)
        self.login("shopmgr")
        self.assertEqual(self.c.get("/purchasing/ref").status_code, 403)
        self.assertEqual(self.c.get("/purchasing/ref/new").status_code, 403)


class RfqQuotationTests(Base):
    def test_rfq_goes_to_chosen_suppliers_once(self):
        self.make_ref()
        ref_id = self.ref_id()
        r = self.send_rfq(ref_id)
        self.assertEqual(r.status_code, 302)
        rfq = self.be.one("SELECT * FROM rfqs")
        self.assertEqual(rfq["status"], "sent")
        self.assertIsNotNone(rfq["sent_at"])
        self.assertEqual(self.be.one("SELECT COUNT(*) AS n FROM rfq_suppliers")["n"], 2)
        self.assertEqual(self.be.one("SELECT status FROM replenishments")["status"], "rfq_sent")
        self.login("whmgr")
        page = self.c.get("/purchasing/ref/%d" % ref_id).get_data(as_text=True)
        self.assertIn("Suppliers and replies", page)
        # one row for the RFQ, with both suppliers inside it (not one row per supplier)
        self.assertEqual(page.count('<td class="nowrap">%s</td>' % rfq["rfq_no"]), 1)
        self.assertIn("Sup A", page)
        self.assertIn("Sup B", page)
        # a second round is allowed while items still have no supplier (see SplitOrderingTests); once
        # nothing is left to ask about, it is refused
        self.be.execute("UPDATE replenishment_items SET qty_dropped = qty_requested, dropped_reason = 'no longer needed'")
        self.be.commit()
        r = self.send_rfq(ref_id)
        self.assertEqual(r.status_code, 200)
        self.assertIn("nothing left to ask suppliers about", " ".join(self.flashes(r)))

    def test_quotation_total_and_replacement(self):
        self.make_ref()
        ref_id = self.ref_id()
        self.send_rfq(ref_id)
        r = self.quote(ref_id)
        self.assertEqual(r.status_code, 302)
        q = self.be.one("SELECT * FROM quotations")
        self.assertAlmostEqual(float(q["total_amount"]), 50.0, places=2)     # 5*10 (REF has item A only)
        self.assertEqual(self.be.one("SELECT COUNT(*) AS n FROM quotation_items")["n"], 1)
        self.assertIsNotNone(self.be.one("SELECT replied_at FROM rfq_suppliers WHERE supplier_id = 1")["replied_at"])
        # same supplier again replaces the quotation
        self.quote(ref_id, lines=[(A, 3, "10.00", 5)])
        self.assertAlmostEqual(float(self.be.one("SELECT total_amount FROM quotations")["total_amount"]), 30.0, places=2)
        self.assertEqual(self.be.one("SELECT COUNT(*) AS n FROM quotation_items")["n"], 1)

    def test_quotation_page_lists_every_supplier_and_fills_agreed_prices(self):
        self.make_ref()
        ref_id = self.ref_id()
        self.send_rfq(ref_id, suppliers=(SUP_B,))          # only B was recorded as sent
        self.login("whmgr")
        html = self.c.get("/purchasing/ref/%d/quotation/new" % ref_id).get_data(as_text=True)
        self.assertIn("Sup A", html)                        # every active supplier is listed
        self.assertIn("Sup B", html)
        self.assertIn("Sent this RFQ", html)
        # B has no agreed prices, so A (the regular supplier) is not selected; asking
        # for A explicitly fills its agreed price for item A into the form.
        html = self.c.get("/purchasing/ref/%d/quotation/new?supplier_id=%d" % (ref_id, SUP_A)).get_data(as_text=True)
        self.assertRegex(html, r'name="unit_price\[\]"[^>]*value="10\.00"')
        # the filled price is labelled, so it is not mistaken for the supplier's own reply
        self.assertIn("data-from-agreed", html)
        self.assertIn("agreed price", html)
        # a saved reply is shown as the supplier's own answer, without the label
        self.quote(ref_id, supplier=SUP_A, lines=[(A, 5, "11.00", 2)])
        html = self.c.get("/purchasing/ref/%d/quotation/new?supplier_id=%d" % (ref_id, SUP_A)).get_data(as_text=True)
        self.assertIn('value="11.00"', html)
        self.assertNotIn("data-from-agreed", html)

    def test_a_later_round_loads_the_last_reply_and_replaces_its_price(self):
        self.make_ref()
        ref_id = self.ref_id()
        self.send_rfq(ref_id)
        self.quote(ref_id, lines=[(A, 5, "12.00", 7)])
        first_q = self.be.one("SELECT id FROM quotations")["id"]
        self.send_rfq(ref_id, suppliers=(SUP_A,))                 # round 2 for the still-open item
        round2 = self.be.one("SELECT rfq_no FROM rfqs ORDER BY id DESC LIMIT 1")["rfq_no"]
        self.login("whmgr")
        page = self.c.get("/purchasing/ref/%d/quotation/new?supplier_id=%d"
                          % (ref_id, SUP_A)).get_data(as_text=True)
        self.assertIn('value="12.00"', page)                      # the round-1 reply is loaded
        self.assertIn("Loaded this supplier's last reply on", page)
        self.assertIn(round2, page)
        # re-quoting the item supersedes the older line and its price
        self.quote(ref_id, lines=[(A, 5, "9.50", 3)])
        second_q = self.be.one("SELECT id FROM quotations ORDER BY id DESC LIMIT 1")["id"]
        self.assertNotEqual(first_q, second_q)
        old = self.be.one("SELECT superseded_at FROM quotation_items WHERE quotation_id = :q", q=first_q)
        new = self.be.one("SELECT superseded_at FROM quotation_items WHERE quotation_id = :q", q=second_q)
        self.assertIsNotNone(old["superseded_at"])
        self.assertIsNone(new["superseded_at"])
        self.assertAlmostEqual(float(self.be.one("SELECT total_amount FROM quotations WHERE id = :q",
                                                 q=first_q)["total_amount"]), 0.0, places=2)
        page = self.c.get("/purchasing/ref/%d" % ref_id).get_data(as_text=True)
        self.assertIn("replaced by a later quotation", page)
        self.login("whmgr")
        compare = self.c.get("/purchasing/ref/%d/compare" % ref_id).get_data(as_text=True)
        self.assertIn("9.50", compare)
        self.assertNotIn("12.00", compare)

    def test_quotation_reloads_a_saved_reply(self):
        self.make_ref()
        ref_id = self.ref_id()
        self.send_rfq(ref_id)
        self.quote(ref_id, lines=[(A, 3, "12.34", 4)])
        self.login("whmgr")
        html = self.c.get("/purchasing/ref/%d/quotation/new?supplier_id=%d" % (ref_id, SUP_A)).get_data(as_text=True)
        self.assertIn('value="12.34"', html)
        self.assertIn('value="3"', html)

    def test_quotation_accepts_a_reply_from_a_supplier_not_on_the_rfq(self):
        self.make_ref()
        ref_id = self.ref_id()
        self.login("whmgr")
        r = self.c.get("/purchasing/ref/%d/quotation/new" % ref_id)
        self.assertEqual(r.status_code, 302)                      # no RFQ yet
        self.send_rfq(ref_id, suppliers=(SUP_B,))
        r = self.quote(ref_id, supplier=SUP_A)                    # replied, but not recorded as sent
        self.assertEqual(r.status_code, 302)
        self.assertEqual(self.be.one("SELECT COUNT(*) AS n FROM quotations")["n"], 1)
        row = self.be.one("SELECT sent_at, replied_at FROM rfq_suppliers WHERE supplier_id = 1")
        self.assertIsNone(row["sent_at"])
        self.assertIsNotNone(row["replied_at"])

    def test_quotation_rejects_zero_or_negative_price(self):
        self.make_ref()
        ref_id = self.ref_id()
        self.send_rfq(ref_id)
        for bad in ("-5", "0", "0.00"):
            r = self.quote(ref_id, lines=[(A, 2, bad, 3)])
            self.assertEqual(r.status_code, 200, bad)
            self.assertIn("enter the unit price", " ".join(self.flashes(r)), bad)
            self.assertIn("set its quantity to 0", " ".join(self.flashes(r)), bad)
        self.assertEqual(self.be.one("SELECT COUNT(*) AS n FROM quotations")["n"], 0)

    def test_an_expired_quotation_falls_back_to_the_agreed_price(self):
        self.make_ref()
        ref_id = self.ref_id()
        self.send_rfq(ref_id)
        self.quote(ref_id, lines=[(A, 5, "12.00", 7)], valid_until="2026-11-02")
        self.be.execute("UPDATE quotations SET valid_until = '2020-01-01'")
        self.be.commit()
        self.login("whmgr")
        page = self.c.get("/purchasing/ref/%d/compare" % ref_id).get_data(as_text=True)
        qid = self.be.one("SELECT id FROM quotations")["id"]
        self.assertNotIn('value="q:%d"' % qid, page)         # expired: replaced by the agreed price
        self.assertIn('value="a:%d"' % SUP_A, page)
        self.assertIn("10.00", page)
        self.assertIn("Sup A (agreed price)", page)          # the summary keeps the tag
        r = self.compare_agreed(ref_id)
        self.assertEqual(r.status_code, 302)
        self.assertEqual(self.be.one("SELECT basis FROM purchase_approvals")["basis"], "agreed_price")

    def test_choose_suppliers_puts_one_offer_per_supplier_and_the_qty_in_the_row(self):
        self.make_ref()
        ref_id = self.ref_id()
        self.send_rfq(ref_id)
        self.quote(ref_id)
        self.login("whmgr")
        page = self.c.get("/purchasing/ref/%d/compare" % ref_id).get_data(as_text=True)
        rid = self.line_ids(ref_id)[A]
        qid = self.be.one("SELECT id FROM quotations")["id"]
        self.assertNotIn("One offer for every item", page)
        self.assertNotIn('id="bulk-offer"', page)
        self.assertNotIn('value="a:%d"' % SUP_A, page)       # quoted it: no second agreed-price radio
        self.assertEqual(page.count('value="q:%d"' % qid), 1)
        self.assertIn('name="qty_%d"' % rid, page)
        self.assertIn('value="5"', page)                     # prefilled with the 5 still to order
        self.assertIn("50.00", page)                         # 5 x 10.00 line subtotal
        self.assertIn("1 item &middot; 50.00", page)         # split preview shows the PO total
        self.assertNotIn(">Record quotation</a>", page)      # the extra button is gone
        self.assertIn(">Send for approval</button>", page)
        self.assertIn(">Back</a>", page)

    def test_the_ref_page_shows_each_next_step_once_and_renames_drop(self):
        self.make_ref()
        ref_id = self.ref_id()
        self.send_rfq(ref_id)
        self.quote(ref_id)
        self.login("whmgr")
        page = self.c.get("/purchasing/ref/%d" % ref_id).get_data(as_text=True)
        head = page[page.index('<div class="page-head no-print">'):page.index("<h2>Progress</h2>")]
        self.assertNotIn('class="btn', head.split('<div class="doc-split">')[0])   # no buttons above the document
        bar = page[page.rindex('<div class="actions">'):]
        for label in ("Record quotation", "Choose suppliers", "Ask more suppliers"):
            self.assertEqual(page.count(">%s</a>" % label), 1, label)             # one place, not two
            self.assertIn(">%s</a>" % label, bar)
        self.assertIn('placeholder="Reason to drop..."', page)
        self.assertIn(">Drop</button>", page)
        self.assertNotIn("why give up", page)
        self.assertNotIn(">Give up</button>", page)


class RfqDocumentTests(Base):
    """The RFQ is a real document: items, signature, PDF, one copy per supplier."""

    def rfq_id(self):
        return self.be.one("SELECT id FROM rfqs ORDER BY id DESC LIMIT 1")["id"]

    def two_item_ref(self, qa=20, qb=10):
        self.login("whmgr")
        self.c.post("/purchasing/ref/new", data={
            "remarks": "split", "item_id[]": [str(A), str(B)], "qty_requested[]": [str(qa), str(qb)],
            "reason[]": ["below_rop", "below_rop"], "action": "submit_ref"})
        self.send_rfq(self.ref_id())
        return self.ref_id()

    def qid(self, supplier):
        return self.be.one("SELECT id FROM quotations WHERE supplier_id = :s ORDER BY id DESC LIMIT 1", s=supplier)["id"]

    def choose(self, ref_id, **choices):
        ids = self.line_ids(ref_id)
        data = {}
        for item, c in choices.items():
            data["choice_%d" % ids[int(item)]] = c
            data["qty_%d" % ids[int(item)]] = ""
        self.login("whmgr")
        return self.c.post("/purchasing/ref/%d/compare" % ref_id, data=data, follow_redirects=True)

    def approve(self):
        self.login("owner")
        return self.c.post("/purchasing/approvals/%d" % self.approval_id(), data={"decision": "approve", "remarks": "ok"})

    def setup_rfq(self, suppliers=(SUP_A, SUP_B)):
        self.make_ref()
        ref_id = self.ref_id()
        r = self.send_rfq(ref_id, suppliers=suppliers)
        return ref_id, self.rfq_id(), r

    def test_creating_an_rfq_signs_it_freezes_its_items_and_opens_its_page(self):
        ref_id, rfq_id, r = self.setup_rfq()
        self.assertEqual(r.status_code, 302)
        self.assertTrue(r.headers["Location"].endswith("/purchasing/ref/%d/rfq/%d" % (ref_id, rfq_id)))
        sig = self.be.one("SELECT * FROM document_signatures WHERE doc_type = 'RFQ'")
        self.assertEqual((sig["slot"], sig["signer_name"], sig["signer_role"]),
                         ("prepared_by", "whmgr", "warehouse_manager"))
        items = self.be.all("SELECT * FROM rfq_items WHERE rfq_id = :r", r=rfq_id)
        self.assertEqual([(i["item_id"], i["qty_requested"]) for i in items], [(A, 5)])
        page = self.c.get(r.headers["Location"]).get_data(as_text=True)
        self.assertIn("Request for Quotation", page)
        self.assertIn("ANSU 12R22.5", page)
        self.assertIn("Unit price", page)
        self.assertIn("Lead time (days)", page)
        self.assertIn('class="fill"', page)                         # blank boxes for the supplier
        self.assertIn("/documents/RFQ/%d.pdf" % rfq_id, page)
        self.assertIn("Prepared by (Warehouse)", page)
        self.assertNotIn("Changed after signing", page)

    def test_the_rfq_pdf_is_a_pdf_and_a_supplier_copy_hides_the_other_suppliers(self):
        ref_id, rfq_id, _r = self.setup_rfq()
        r = self.c.get("/documents/RFQ/%d.pdf" % rfq_id)
        self.assertEqual(r.mimetype, "application/pdf")
        self.assertTrue(r.get_data().startswith(b"%PDF"))
        r = self.c.get("/documents/RFQ/%d.pdf?supplier=%d" % (rfq_id, SUP_B))
        self.assertEqual(r.mimetype, "application/pdf")
        self.assertIn("Sup-B", r.headers["Content-Disposition"])
        internal = self.c.get("/documents/RFQ/%d" % rfq_id).get_data(as_text=True)
        self.assertIn("Sup A, Sup B", internal)                      # internal copy lists everyone
        copy = self.c.get("/documents/RFQ/%d?supplier=%d" % (rfq_id, SUP_B)).get_data(as_text=True)
        self.assertIn("Sup B", copy)
        self.assertIn("Attn: Ben", copy)
        self.assertNotIn("Sup A", copy)                              # competitors are not named
        self.assertNotIn("Changed after signing", copy)             # a new addressee is not a change

    def test_the_rfq_page_tracks_replies_and_links_each_supplier_copy(self):
        ref_id, rfq_id, r = self.setup_rfq()
        url = r.headers["Location"]
        page = self.c.get(url).get_data(as_text=True)
        self.assertIn("0 of 2 so far", page)
        self.assertIn("supplier=%d" % SUP_A, page)
        self.assertIn("Record reply", page)
        self.quote(ref_id, SUP_A)
        self.login("whmgr")
        page = self.c.get(url).get_data(as_text=True)
        self.assertIn("1 of 2 so far", page)
        self.assertIn("Edit reply", page)
        self.assertIn("50.00", page)                                 # what Sup A quoted

    def test_only_this_warehouse_and_the_owner_can_see_an_rfq(self):
        ref_id, rfq_id, _r = self.setup_rfq()
        self.login("owner")
        self.assertEqual(self.c.get("/documents/RFQ/%d" % rfq_id).status_code, 200)
        self.assertEqual(self.c.get("/purchasing/ref/%d/rfq/%d" % (ref_id, rfq_id)).status_code, 200)
        self.login("shopmgr")
        self.assertEqual(self.c.get("/documents/RFQ/%d" % rfq_id).status_code, 404)
        self.assertIn(self.c.get("/purchasing/ref/%d/rfq/%d" % (ref_id, rfq_id)).status_code, (302, 403))
        self.login("whmgr")
        self.assertEqual(self.c.get("/purchasing/ref/%d/rfq/%d" % (ref_id + 50, rfq_id)).status_code, 404)

    def test_a_later_round_lists_only_the_items_that_were_still_open(self):
        ref_id = self.two_item_ref()
        first = self.rfq_id()
        self.quote(ref_id, SUP_A, lines=[(B, 10, "20.00", 5)])
        self.choose(ref_id, **{str(A): "open", str(B): "q:%d" % self.qid(SUP_A)})
        self.approve()
        self.login("whmgr")
        self.c.post("/purchasing/ref/%d/rfq/new" % ref_id, data={"supplier_ids[]": [str(SUP_B)]})
        second = self.rfq_id()
        self.assertNotEqual(first, second)
        got = lambda rid: sorted(r["item_id"] for r in self.be.all(
            "SELECT item_id FROM rfq_items WHERE rfq_id = :r", r=rid))
        self.assertEqual(got(first), [A, B])
        self.assertEqual(got(second), [A])
        page = self.c.get("/documents/RFQ/%d" % second).get_data(as_text=True)
        self.assertIn("ANSU", page)
        self.assertNotIn("RODEO", page)

    def test_an_rfq_from_before_rfq_items_existed_still_opens_and_can_be_signed(self):
        ref_id, rfq_id, _r = self.setup_rfq()
        self.be.execute("DELETE FROM rfq_items")
        self.be.execute("DELETE FROM document_signatures")
        self.be.commit()
        self.login("whmgr")
        page = self.c.get("/documents/RFQ/%d" % rfq_id).get_data(as_text=True)
        self.assertIn("ANSU", page)                                  # falls back to the REF's items
        self.assertIn("Not signed yet", page)
        r = self.c.post("/documents/RFQ/%d/sign" % rfq_id, data={"slot": "prepared_by"}, follow_redirects=True)
        self.assertIn("Signed: Prepared by (Warehouse)", r.get_data(as_text=True))
        self.assertEqual(self.be.one("SELECT COUNT(*) AS n FROM document_signatures")["n"], 1)

    def test_save_and_next_goes_to_the_next_supplier_that_has_not_replied(self):
        ref_id, rfq_id, _r = self.setup_rfq()
        ids = self.line_ids(ref_id)
        data = {"supplier_id": str(SUP_A), "quotation_date": "2026-11-01", "action": "save_and_next",
                "ref_item_id[]": [str(ids[A])], "qty_offered[]": ["5"], "unit_price[]": ["10.00"],
                "lead_time_days[]": ["7"]}
        self.login("whmgr")
        r = self.c.post("/purchasing/ref/%d/quotation/new" % ref_id, data=data)
        self.assertEqual(r.status_code, 302)
        self.assertIn("supplier_id=%d" % SUP_B, r.headers["Location"])
        data.update(supplier_id=str(SUP_B))
        r = self.c.post("/purchasing/ref/%d/quotation/new" % ref_id, data=data, follow_redirects=True)
        page = r.get_data(as_text=True)
        self.assertIn("Every supplier on this RFQ has replied", page)
        self.assertIn("Replenishment REF-", page)                    # ended on the REF page

    def test_the_ref_page_has_a_stepper_and_links_to_the_rfq_document(self):
        ref_id, rfq_id, _r = self.setup_rfq()
        self.login("whmgr")
        page = self.c.get("/purchasing/ref/%d" % ref_id).get_data(as_text=True)
        self.assertIn('class="stepper"', page)
        self.assertIn('<li class="step current" aria-current="step"><span>Quotes</span></li>', page)
        self.assertIn("/purchasing/ref/%d/rfq/%d" % (ref_id, rfq_id), page)
        self.assertIn("/documents/RFQ/%d.pdf" % rfq_id, page)
        self.assertEqual(page.count("Next:"), 1)

    def test_flow_steps_follow_the_ref_status(self):
        from app import purchasing_ops as pur
        base = {"pos": [], "rfq": None, "rfq_rows": [], "has_open": True}
        state = lambda status, **kw: [s["state"] for s in pur.ref_flow(dict(base, status=status, **kw))["steps"]]
        self.assertEqual(state("draft")[0], "current")
        self.assertEqual(state("submitted")[:3], ["done", "current", "todo"])
        self.assertEqual(state("rfq_sent", rfq={"id": 1})[:3], ["done", "done", "current"])
        self.assertEqual(state("for_approval", rfq={"id": 1}, has_open=False)[3], "current")
        self.assertEqual(set(state("closed")), {"done"})
        self.assertIsNone(pur.ref_flow(dict(base, status="cancelled")))

    def test_a_pressed_button_cannot_be_pressed_twice(self):
        js = self.c.get("/static/js/app.js").get_data(as_text=True)
        self.assertIn("is-loading", js)
        self.assertIn("data-submitting", js)
        self.assertNotIn("disabled = true", js)       # a disabled button would drop name=action from the request


class ApprovalTests(Base):
    def full_quote_ref(self):
        self.make_ref()
        ref_id = self.ref_id()
        self.send_rfq(ref_id)
        self.quote(ref_id)
        return ref_id

    def test_one_ref_per_restock_alert(self):
        self.make_ref(submit=False)
        ref = self.be.one("SELECT id, ref_no FROM replenishments")
        page = self.c.get("/purchasing/alerts/1").get_data(as_text=True)
        self.assertIn("Open %s" % ref["ref_no"], page)
        before = self.be.one("SELECT COUNT(*) AS n FROM replenishments")["n"]
        self.login("whmgr")
        r = self.c.post("/purchasing/ref/new", data={
            "warehouse_id": "1", "remarks": "again", "item_id[]": [str(A)],
            "qty_requested[]": ["3"], "action": "save_draft", "alert_id": "1"}, follow_redirects=True)
        self.assertIn("already has", r.get_data(as_text=True))
        self.assertEqual(self.be.one("SELECT COUNT(*) AS n FROM replenishments")["n"], before)

    def test_an_approved_po_has_a_send_action_for_the_warehouse_manager(self):
        ref_id = self.full_quote_ref()
        self.compare_quote(ref_id)
        aid = self.be.one("SELECT id FROM purchase_approvals ORDER BY id LIMIT 1")["id"]
        self.login("owner")
        self.c.post("/purchasing/approvals/%d" % aid, data={
            "remarks": "ok", "decision": "approve", "expected_date": "2026-12-01", "terms": "30 days"})
        self.login("whmgr")
        po_list = self.c.get("/purchasing/po").get_data(as_text=True)
        self.assertIn(">Send</a>", po_list)
        ref = self.c.get("/purchasing/ref/%d" % ref_id).get_data(as_text=True)
        self.assertIn("Send PO", ref)

    def test_expected_delivery_defaults_to_quotation_date_plus_lead_time(self):
        self.make_ref()
        ref_id = self.ref_id()
        self.send_rfq(ref_id)
        quotation_date = dt.date.today() - dt.timedelta(days=2)
        self.quote(ref_id, quotation_date=quotation_date.isoformat(), lines=[(A, 5, "10.00", 7)])
        self.compare_quote(ref_id)
        self.login("owner")
        page = self.c.get("/purchasing/approvals/%d" % self.approval_id()).get_data(as_text=True)
        expected = (quotation_date + dt.timedelta(days=7)).isoformat()
        self.assertRegex(page, r'name="expected_date"[^>]*value="%s"' % expected)
        self.assertIn("from quotation date + lead time", page)

    def test_choose_a_quotation_sends_it_to_the_owner(self):
        ref_id = self.full_quote_ref()
        r = self.compare_quote(ref_id)
        self.assertEqual(r.status_code, 302)
        appr = self.be.one("SELECT * FROM purchase_approvals")
        self.assertEqual((appr["decision"], appr["basis"], appr["supplier_id"]), ("pending", "quotation", SUP_A))
        self.assertEqual(self.be.one("SELECT status FROM replenishments")["status"], "for_approval")
        r = self.compare_quote(ref_id)
        self.assertEqual(r.status_code, 200)
        self.assertIn("already waiting", " ".join(self.flashes(r)))

    def test_regular_supplier_agreed_price(self):
        self.make_ref()
        ref_id = self.ref_id()
        # no RFQ was sent: a regular supplier's agreed price is enough
        self.assertIn("Choose suppliers", self.c.get("/purchasing/ref/%d" % ref_id).get_data(as_text=True))
        self.assertIn("Sup A (agreed price)",
                      self.c.get("/purchasing/ref/%d/compare" % ref_id).get_data(as_text=True))
        r = self.compare_agreed(ref_id)
        self.assertEqual(r.status_code, 302)
        appr = self.be.one("SELECT * FROM purchase_approvals")
        self.assertEqual((appr["basis"], appr["quotation_id"]), ("agreed_price", None))
        # 5 units of A at 10.00 (B is not on this REF)
        self.assertTrue(self.be.one("SELECT 1 FROM purchase_approvals") is not None)

    def test_agreed_price_is_only_offered_for_regular_suppliers(self):
        self.make_ref()
        ref_id = self.ref_id()
        page = self.c.get("/purchasing/ref/%d/compare" % ref_id).get_data(as_text=True)
        self.assertIn("Sup A (agreed price)", page)
        self.assertNotIn("Sup B (agreed price)", page)
        r = self.compare_agreed(ref_id, supplier=SUP_B)        # not regular
        self.assertEqual(r.status_code, 200)
        self.assertIn("did not offer this item", " ".join(self.flashes(r)))
        # Sup A also has an agreed price for B, so a REF for B can be ordered the same way
        self.make_ref(item=B, qty=2)
        rid = self.ref_id()
        r = self.compare_agreed(rid, supplier=SUP_A)
        self.assertEqual(r.status_code, 302)

    def test_owner_sees_the_approval_and_others_do_not(self):
        ref_id = self.full_quote_ref()
        self.compare_quote(ref_id)
        aid = self.approval_id()
        self.login("owner")
        self.assertEqual(self.c.get("/purchasing/approvals").status_code, 200)
        page = self.c.get("/purchasing/approvals/%d" % aid).get_data(as_text=True)
        self.assertIn("Sup A", page)
        self.assertIn("50.00", page)
        self.login("whmgr")
        self.assertEqual(self.c.get("/purchasing/approvals").status_code, 200)
        self.assertEqual(self.c.get("/purchasing/approvals/%d" % aid).status_code, 403)

    def approve(self, decision="approve", remarks="ok"):
        self.login("owner")
        return self.c.post("/purchasing/approvals/%d" % self.approval_id(),
                           data={"decision": decision, "remarks": remarks})

    def test_approve_creates_draft_po_and_po_can_be_sent(self):
        ref_id = self.full_quote_ref()
        self.compare_quote(ref_id)
        r = self.approve()
        self.assertEqual(r.status_code, 302)
        po = self.be.one("SELECT * FROM purchase_orders")
        self.assertEqual((po["status"], po["supplier_id"]), ("draft", SUP_A))
        self.assertAlmostEqual(float(po["total_amount"]), 50.0, places=2)
        self.assertEqual(self.be.one("SELECT COUNT(*) AS n FROM purchase_order_items")["n"], 1)
        self.assertEqual(self.be.one("SELECT status FROM replenishments")["status"], "approved")
        appr = self.be.one("SELECT * FROM purchase_approvals")
        self.assertEqual((appr["decision"], appr["decided_by"]), ("approved", self.uid["owner"]))
        self.assertEqual(self.c.get("/purchasing/po/%d" % po["id"]).status_code, 200)
        # the owner's approval signed the REF and the new PO, with the owner's account name
        owner_sigs = [(s["doc_type"], s["slot"], s["signer_name"], s["signer_role"]) for s in
                      self.be.all("SELECT * FROM document_signatures WHERE signer_role = 'owner' ORDER BY id")]
        self.assertEqual(owner_sigs, [("REF", "approved_by", "owner", "owner"),
                                      ("PO", "approved_by", "owner", "owner")])

        self.login("whmgr")
        self.c.post("/purchasing/po/%d" % po["id"], data={"action": "mark_sent"})
        self.assertEqual(self.be.one("SELECT status FROM purchase_orders")["status"], "sent")
        self.assertEqual(self.be.one("SELECT signer_name FROM document_signatures WHERE doc_type = 'PO' "
                                     "AND slot = 'sent_by'")["signer_name"], "whmgr")
        self.assertIsNotNone(self.be.one("SELECT sent_at FROM purchase_orders")["sent_at"])

    def test_approve_records_expected_date_and_terms(self):
        ref_id = self.full_quote_ref()
        self.compare_quote(ref_id)
        self.login("owner")
        r = self.c.post("/purchasing/approvals/%d" % self.approval_id(),
                        data={"decision": "approve", "remarks": "ok",
                              "expected_date": "2026-12-15", "terms": "30 days, delivered to warehouse"})
        self.assertEqual(r.status_code, 302)
        po = self.be.one("SELECT * FROM purchase_orders")
        self.assertEqual((po["expected_date"], po["terms"]), ("2026-12-15", "30 days, delivered to warehouse"))

    def test_expired_quotation_cannot_be_chosen(self):
        self.make_ref()
        ref_id = self.ref_id()
        self.send_rfq(ref_id)
        self.quote(ref_id, supplier=SUP_B, lines=[(A, 5, "10.00", 7)],
                   quotation_date="2026-01-01", valid_until="2026-01-02")
        r = self.compare_quote(ref_id, supplier=SUP_B)   # no agreed price to fall back to
        self.assertEqual(r.status_code, 200)
        self.assertIn("expired", " ".join(self.flashes(r)))
        self.assertEqual(self.be.one("SELECT COUNT(*) AS n FROM purchase_approvals")["n"], 0)

    def test_cancel_a_sent_po_returns_the_ref(self):
        ref_id = self.full_quote_ref()
        self.compare_quote(ref_id)
        self.approve()
        po = self.be.one("SELECT * FROM purchase_orders")
        self.login("whmgr")
        self.c.post("/purchasing/po/%d" % po["id"], data={"action": "mark_sent"})
        r = self.c.post("/purchasing/po/%d/cancel" % po["id"], data={"cancel_reason": "supplier cannot supply"})
        self.assertEqual(r.status_code, 302)
        self.assertEqual(self.be.one("SELECT status FROM purchase_orders")["status"], "cancelled")
        self.assertEqual(self.be.one("SELECT status FROM replenishments")["status"], "rfq_sent")   # its RFQ is still open

    def received_po(self):
        ref_id = self.full_quote_ref()
        self.compare_quote(ref_id)
        self.approve()
        po = self.be.one("SELECT * FROM purchase_orders ORDER BY id DESC LIMIT 1")
        self.be.execute("UPDATE purchase_order_items SET qty_received = qty_ordered WHERE po_id = :p", p=po["id"])
        self.be.execute("UPDATE purchase_orders SET status = 'received' WHERE id = :p", p=po["id"])
        self.be.commit()
        return po

    def test_draft_po_can_be_edited_before_sending(self):
        ref_id = self.full_quote_ref()
        self.compare_quote(ref_id)
        self.approve()
        po = self.be.one("SELECT * FROM purchase_orders")
        self.login("whmgr")
        r = self.c.post("/purchasing/po/%d/edit" % po["id"], data={
            "expected_date": "2026-12-20", "terms": "45 days"})
        self.assertEqual(r.status_code, 302)
        po = self.be.one("SELECT * FROM purchase_orders WHERE id = :i", i=po["id"])
        self.assertEqual((po["expected_date"], po["terms"]), ("2026-12-20", "45 days"))

    def test_whstaff_can_view_po_but_not_send_it(self):
        ref_id = self.full_quote_ref()
        self.compare_quote(ref_id)
        self.approve()
        po = self.be.one("SELECT * FROM purchase_orders")
        self.login("whstaff")
        self.assertEqual(self.c.get("/purchasing/po").status_code, 200)
        self.assertEqual(self.c.get("/purchasing/po/%d" % po["id"]).status_code, 200)
        r = self.c.post("/purchasing/po/%d" % po["id"], data={"action": "mark_sent"})
        self.assertEqual(r.status_code, 302)
        self.assertEqual(self.be.one("SELECT status FROM purchase_orders")["status"], "draft")

    def test_reject_needs_remarks_and_returns_ref_to_the_warehouse(self):
        ref_id = self.full_quote_ref()
        self.compare_quote(ref_id)
        r = self.approve(decision="reject", remarks="")
        self.assertEqual(r.status_code, 200)
        self.assertIn("required", " ".join(self.flashes(r)))
        r = self.approve(decision="reject", remarks="price too high")
        self.assertEqual(r.status_code, 302)
        appr = self.be.one("SELECT * FROM purchase_approvals")
        self.assertEqual((appr["decision"], appr["remarks"]), ("rejected", "price too high"))
        self.assertEqual(self.be.one("SELECT status FROM replenishments")["status"], "rfq_sent")   # its RFQ is still open
        self.assertEqual(self.be.one("SELECT COUNT(*) AS n FROM purchase_orders")["n"], 0)

    def test_audit_rows_are_written(self):
        ref_id = self.full_quote_ref()
        self.compare_quote(ref_id)
        self.approve()
        actions = {r["action"] for r in self.be.all("SELECT action FROM audit_log")}
        self.assertTrue({"create", "submit", "approve"} <= actions)

    def test_the_manager_can_withdraw_a_pending_approval(self):
        ref_id = self.full_quote_ref()
        self.compare_quote(ref_id)
        aid = self.approval_id()
        self.login("whmgr")
        page = self.c.get("/purchasing/ref/%d" % ref_id).get_data(as_text=True)
        self.assertIn('class="btn small danger">Withdraw</button>', page)
        self.assertIn("return confirm('Withdraw this approval?", page)
        self.assertNotIn(">decide</a>", page)                  # the owner decides, not the manager
        r = self.c.post("/purchasing/approvals/%d/withdraw" % aid)
        self.assertEqual(r.status_code, 302)
        self.assertEqual(self.be.one("SELECT COUNT(*) AS n FROM purchase_approvals")["n"], 0)
        self.assertEqual(self.be.one("SELECT COUNT(*) AS n FROM purchase_approval_items")["n"], 0)
        self.assertEqual(self.be.one("SELECT status FROM replenishments")["status"], "rfq_sent")
        self.assertEqual(self.be.one("SELECT COUNT(*) AS n FROM audit_log WHERE action = 'withdraw'")["n"], 1)
        page = self.c.get(r.headers["Location"]).get_data(as_text=True)
        self.assertIn("withdrawn", page)
        self.assertNotIn("Withdraw", page)
        # the lines are open again, so a supplier can be chosen once more
        self.assertEqual(self.compare_quote(ref_id).status_code, 302)
        self.assertEqual(self.be.one("SELECT COUNT(*) AS n FROM purchase_approvals")["n"], 1)

    def test_only_the_submitting_manager_and_only_while_pending(self):
        ref_id = self.full_quote_ref()
        self.compare_quote(ref_id)
        aid = self.approval_id()
        self.be.execute("INSERT INTO users (username, full_name, role_id, location_id) VALUES "
                        "('whmgr2', 'Second WM', (SELECT id FROM roles WHERE code = 'warehouse_manager'), 1)")
        self.be.commit()
        self.c.get("/dev/user/%d" % self.be.one("SELECT id FROM users WHERE username = 'whmgr2'")["id"])
        page = self.c.get("/purchasing/ref/%d" % ref_id).get_data(as_text=True)
        self.assertNotIn("Withdraw", page)                      # not the one who submitted it
        self.c.post("/purchasing/approvals/%d/withdraw" % aid)
        self.assertEqual(self.be.one("SELECT COUNT(*) AS n FROM purchase_approvals")["n"], 1)
        self.approve()
        self.login("whmgr")
        r = self.c.post("/purchasing/approvals/%d/withdraw" % aid)
        self.assertEqual(r.status_code, 302)
        self.assertIn("already approved", self.c.get(r.headers["Location"]).get_data(as_text=True))
        self.assertEqual(self.be.one("SELECT decision FROM purchase_approvals")["decision"], "approved")


class SupplierTests(Base):
    def test_create_supplier_with_agreed_price(self):
        self.login("whmgr")
        r = self.c.post("/purchasing/suppliers/new", data={
            "supplier_name": "Sup C", "email": "c@supc.test", "is_active": "on",
            "item_id[]": [str(A)], "agreed_price[]": ["12.50"],
            "valid_from[]": ["2026-01-01"], "valid_to[]": [""]})
        self.assertEqual(r.status_code, 302)
        s = self.be.one("SELECT * FROM suppliers WHERE supplier_name = 'Sup C'")
        self.assertEqual((bool(s["is_active"]), bool(s["is_regular"])), (True, False))
        price = self.be.one("SELECT * FROM supplier_items WHERE supplier_id = :s", s=s["id"])
        self.assertAlmostEqual(float(price["agreed_price"]), 12.5, places=2)

    def test_duplicate_name_and_bad_email_are_refused(self):
        self.login("whmgr")
        r = self.c.post("/purchasing/suppliers/new", data={"supplier_name": "Sup A", "email": "x@y.test"})
        self.assertEqual(r.status_code, 200)
        self.assertIn("already exists", " ".join(self.flashes(r)))
        r = self.c.post("/purchasing/suppliers/new", data={"supplier_name": "New", "email": "nope"})
        self.assertEqual(r.status_code, 200)
        self.assertIn("valid email", " ".join(self.flashes(r)))

    def test_edit_updates_the_same_valid_from_price(self):
        self.login("whmgr")
        self.c.post("/purchasing/suppliers/1/edit", data={
            "supplier_name": "Sup A", "email": "ana@supa.test", "is_regular": "on", "is_active": "on",
            "item_id[]": [str(A)], "agreed_price[]": ["15.00"], "valid_from[]": ["2026-01-01"]})
        rows = self.be.all("SELECT * FROM supplier_items WHERE supplier_id = 1 AND item_id = 1")
        self.assertEqual(len(rows), 1)
        self.assertAlmostEqual(float(rows[0]["agreed_price"]), 15.0, places=2)

    def test_zero_agreed_price_is_refused(self):
        self.login("whmgr")
        r = self.c.post("/purchasing/suppliers/1/edit", data={
            "supplier_name": "Sup A", "email": "ana@supa.test", "is_regular": "on", "is_active": "on",
            "item_id[]": [str(A)], "agreed_price[]": ["0"], "valid_from[]": ["2026-06-01"]})
        self.assertEqual(r.status_code, 200)
        self.assertIn("must be more than zero", " ".join(self.flashes(r)))
        self.assertEqual(self.be.one(
            "SELECT COUNT(*) AS n FROM supplier_items WHERE valid_from = '2026-06-01'")["n"], 0)

    def test_owner_and_manager_manage_suppliers_but_staff_cannot_even_view(self):
        for who in ("owner", "whmgr"):
            self.login(who)
            self.assertEqual(self.c.get("/purchasing/suppliers").status_code, 200, who)
            self.assertEqual(self.c.get("/purchasing/suppliers/1/edit").status_code, 200, who)
        for who in ("whstaff", "shopmgr", "shopstaff"):
            self.login(who)
            self.assertEqual(self.c.get("/purchasing/suppliers").status_code, 403, who)
            self.assertEqual(self.c.get("/purchasing/suppliers/1/edit").status_code, 403, who)


class SplitOrderingTests(Base):
    """One REF, several suppliers: supplier 1 has item B but not A, supplier 2 has A but not B."""

    def two_item_ref(self, qa=20, qb=10):
        self.login("whmgr")
        self.c.post("/purchasing/ref/new", data={
            "remarks": "split", "item_id[]": [str(A), str(B)], "qty_requested[]": [str(qa), str(qb)],
            "reason[]": ["below_rop", "below_rop"], "action": "submit_ref"})
        ref_id = self.ref_id()
        self.send_rfq(ref_id)
        return ref_id

    def qid(self, supplier):
        return self.be.one("SELECT id FROM quotations WHERE supplier_id = :s ORDER BY id DESC LIMIT 1", s=supplier)["id"]

    def choose(self, ref_id, **choices):
        """choices: {item_id: 'q:<id>' | 'a:<id>' | 'open' | (choice, qty)}"""
        ids = self.line_ids(ref_id)
        data = {}
        for item, c in choices.items():
            choice, qty = c[:2] if isinstance(c, tuple) else (c, "")
            rid = ids[int(item)]
            data["choice_%d" % rid] = choice
            data["qty_%d" % rid] = qty
        self.login("whmgr")
        return self.c.post("/purchasing/ref/%d/compare" % ref_id, data=data, follow_redirects=True)

    def owner_decides(self, **decisions):
        """decisions: {approval_id: 'approve'|'reject'}; none = approve everything."""
        aid = self.be.one("SELECT id FROM purchase_approvals ORDER BY id LIMIT 1")["id"]
        data = {"remarks": "ok", "expected_date": "2026-12-01", "terms": "30 days"}
        for k, v in decisions.items():
            data["decision_%d" % k] = v
        if not decisions:
            data["decision"] = "approve"
        self.login("owner")
        return self.c.post("/purchasing/approvals/%d" % aid, data=data, follow_redirects=True)

    def ref_status(self):
        return self.be.one("SELECT status FROM replenishments")["status"]

    def mixed_quotes(self, ref_id):
        # Sup A can only supply B; Sup B can only supply A
        self.quote(ref_id, SUP_A, lines=[(B, 10, "20.00", 5)])
        self.quote(ref_id, SUP_B, lines=[(A, 20, "10.00", 7)])

    # ---- the case you asked about ----
    def test_mixed_suppliers_make_one_po_each_from_one_ref(self):
        ref_id = self.two_item_ref()
        self.mixed_quotes(ref_id)
        self.login("whmgr")
        page = self.c.get("/purchasing/ref/%d/compare" % ref_id).get_data(as_text=True)
        self.assertIn("Sup A", page); self.assertIn("Sup B", page)
        ids = self.line_ids(ref_id)
        self.login("whmgr")
        r = self.c.post("/purchasing/ref/%d/compare" % ref_id, data={
            "choice_%d" % ids[A]: "q:%d" % self.qid(SUP_B), "choice_%d" % ids[B]: "q:%d" % self.qid(SUP_A)})
        self.assertEqual(r.status_code, 302)
        approvals = self.be.all("SELECT * FROM purchase_approvals ORDER BY id")
        self.assertEqual(sorted(a["supplier_id"] for a in approvals), [SUP_A, SUP_B])
        self.assertEqual(len({a["batch_id"] for a in approvals}), 1)          # one submission
        self.assertIsNotNone(approvals[0]["batch_id"])
        self.assertEqual(self.ref_status(), "for_approval")
        # the owner sees both suppliers on ONE screen and approves once
        self.login("owner")
        page = self.c.get("/purchasing/approvals/%d" % approvals[0]["id"]).get_data(as_text=True)
        self.assertIn("Sup A", page); self.assertIn("Sup B", page)
        r = self.owner_decides()
        pos = self.be.all("SELECT * FROM purchase_orders ORDER BY id")
        self.assertEqual(sorted(p["supplier_id"] for p in pos), [SUP_A, SUP_B])
        by_sup = {p["supplier_id"]: p for p in pos}
        item_of = lambda po: [i["item_id"] for i in self.be.all("SELECT item_id FROM purchase_order_items WHERE po_id = :p", p=po["id"])]
        self.assertEqual(item_of(by_sup[SUP_A]), [B])
        self.assertEqual(item_of(by_sup[SUP_B]), [A])
        self.assertAlmostEqual(float(by_sup[SUP_B]["total_amount"]), 200.0, places=2)   # 20 x 10
        self.assertAlmostEqual(float(by_sup[SUP_A]["total_amount"]), 200.0, places=2)   # 10 x 20
        self.assertEqual(self.ref_status(), "approved")
        # the owner's one click signed the REF once and each PO
        sigs = [(x["doc_type"], x["slot"]) for x in self.be.all("SELECT * FROM document_signatures ORDER BY id")]
        self.assertEqual(sigs.count(("REF", "approved_by")), 1)
        self.assertEqual(sigs.count(("PO", "approved_by")), 2)

    def test_one_supplier_mixing_quotation_and_agreed_price_gets_one_approval_and_po(self):
        ref_id = self.two_item_ref()
        self.quote(ref_id, SUP_A, lines=[(B, 10, "18.00", 5)])       # Sup A quotes B only
        self.login("whmgr")
        page = self.c.get("/purchasing/ref/%d/compare" % ref_id).get_data(as_text=True)
        self.assertIn("Sup A (quotation + agreed price)", page)      # one preview group
        ids = self.line_ids(ref_id)
        self.choose(ref_id, **{str(A): "a:%d" % SUP_A, str(B): "q:%d" % self.qid(SUP_A)})
        approvals = self.be.all("SELECT * FROM purchase_approvals")
        self.assertEqual(len(approvals), 1)                          # one supplier, one approval
        self.assertEqual((approvals[0]["supplier_id"], approvals[0]["basis"], approvals[0]["quotation_id"]),
                         (SUP_A, "mixed", None))
        lines = {r["replenishment_item_id"]: r for r in self.be.all("SELECT * FROM purchase_approval_items")}
        self.assertEqual(len(lines), 2)
        self.assertAlmostEqual(float(lines[ids[A]]["unit_price"]), 10.0, places=2)   # agreed price
        self.assertAlmostEqual(float(lines[ids[B]]["unit_price"]), 18.0, places=2)   # quotation
        self.login("whmgr")
        page = self.c.get("/purchasing/ref/%d" % ref_id).get_data(as_text=True)
        self.assertIn("Mixed", page)                                 # the REF says why
        self.owner_decides()
        pos = self.be.all("SELECT * FROM purchase_orders")
        self.assertEqual(len(pos), 1)                                # one supplier, one PO
        self.assertEqual(pos[0]["supplier_id"], SUP_A)
        self.assertEqual(self.be.one("SELECT COUNT(*) AS n FROM purchase_order_items WHERE po_id = :p",
                                     p=pos[0]["id"])["n"], 2)
        self.assertAlmostEqual(float(pos[0]["total_amount"]), 20 * 10.0 + 10 * 18.0, places=2)

    def test_quick_select_lists_cheapest_and_each_suppliers_coverage_and_total(self):
        ref_id = self.two_item_ref()                                  # A x20, B x10
        self.mixed_quotes(ref_id)                                     # Sup A has B, Sup B has A
        self.login("whmgr")
        page = self.c.get("/purchasing/ref/%d/compare" % ref_id).get_data(as_text=True)
        self.assertLess(page.index("data-bulk-bar"), page.index('<table class="data"'))
        self.assertIn("data-bulk-cheapest", page)
        # Sup A covers everything once the agreed price for A is added to their quotation for B
        self.assertRegex(page, r'data-bulk-supplier="%d">\s*Sup A <span class="muted">'
                               r'\(2/2 &middot; 400\.00\)' % SUP_A)
        self.assertRegex(page, r'data-bulk-supplier="%d">\s*Sup B <span class="muted">'
                               r'\(1/2 &middot; 200\.00\)' % SUP_B)

    def test_quick_select_leaves_out_an_expired_offer(self):
        ref_id = self.two_item_ref()
        self.mixed_quotes(ref_id)
        self.be.execute("UPDATE quotations SET valid_until = '2020-01-01' WHERE supplier_id = :s", s=SUP_B)
        self.be.commit()
        self.login("whmgr")
        page = self.c.get("/purchasing/ref/%d/compare" % ref_id).get_data(as_text=True)
        # Sup B has no agreed price behind the expired quotation, so they cannot be quick-selected
        self.assertNotIn('data-bulk-supplier="%d"' % SUP_B, page)
        self.assertRegex(page, r'data-bulk-supplier="%d">\s*Sup A <span class="muted">'
                               r'\(2/2 &middot; 400\.00\)' % SUP_A)

    def test_a_stored_zero_price_is_not_an_offer(self):
        """Old data with a 0 price must not become the 'cheapest' preselected supplier."""
        ref_id = self.two_item_ref()
        self.quote(ref_id, SUP_B, lines=[(A, 20, "10.00", 7)])
        self.quote(ref_id, SUP_A, lines=[(B, 10, "20.00", 5)])
        qi = self.be.one("SELECT id FROM quotation_items WHERE quotation_id = :q", q=self.qid(SUP_A))
        self.be.execute("UPDATE quotation_items SET unit_price = 0 WHERE id = :i", i=qi["id"])
        self.be.commit()
        self.login("whmgr")
        page = self.c.get("/purchasing/ref/%d/compare" % ref_id).get_data(as_text=True)
        self.assertNotIn('value="q:%d"' % self.qid(SUP_A), page)                 # not offered for B
        self.assertIn("replied without a price", page)                          # but not silently lost
        r = self.choose(ref_id, **{str(B): "q:%d" % self.qid(SUP_A), str(A): "q:%d" % self.qid(SUP_B)})
        self.assertIn("did not offer this item", r.get_data(as_text=True))
        self.assertEqual(self.be.one("SELECT COUNT(*) AS n FROM purchase_approvals")["n"], 0)

    def test_a_supplier_who_quoted_one_item_is_not_offered_twice_for_it(self):
        ref_id = self.two_item_ref()
        self.quote(ref_id, SUP_A, lines=[(B, 10, "20.00", 5)])
        self.login("whmgr")
        page = self.c.get("/purchasing/ref/%d/compare" % ref_id).get_data(as_text=True)
        self.assertEqual(page.count('value="q:%d"' % self.qid(SUP_A)), 1)   # B: the quotation
        self.assertIn('value="a:1"', page)                                  # A: agreed price, nobody quoted it
        self.assertIn("Sup A (quotation + agreed price)", page)             # one preview group, mixed base

    def test_default_picks_the_cheapest_supplier_that_can_cover_the_whole_quantity(self):
        ref_id = self.two_item_ref()
        self.quote(ref_id, SUP_A, lines=[(A, 20, "12.00", 5), (B, 10, "20.00", 5)])
        self.quote(ref_id, SUP_B, lines=[(A, 20, "9.00", 7), (B, 4, "5.00", 7)])       # cheaper A, but only 4 of B
        from app import purchasing_ops as pur
        with self.app.app_context():
            data = pur.compare_data(ref_id, {"id": self.uid["whmgr"], "role": "warehouse_manager", "location_id": WH})
        by_item = {l["item_id"]: l for l in data["lines"]}
        self.assertEqual(by_item[A]["default"], "q:%d" % self.qid(SUP_B))                # cheapest full cover
        self.assertEqual(by_item[B]["default"], "q:%d" % self.qid(SUP_A))                # B: only Sup A covers all 10

    # ---- partly covered: keep the rest open ----
    def test_keep_open_then_order_the_rest_from_another_supplier(self):
        ref_id = self.two_item_ref()
        self.quote(ref_id, SUP_A, lines=[(B, 10, "20.00", 5)])                            # nobody has A yet
        r = self.choose(ref_id, **{str(A): "open", str(B): "q:%d" % self.qid(SUP_A)})
        self.assertIn("Some items are still open", r.get_data(as_text=True))
        self.owner_decides()
        self.assertEqual(self.be.one("SELECT COUNT(*) AS n FROM purchase_orders")["n"], 1)
        self.assertEqual(self.ref_status(), "rfq_sent")                                   # NOT approved: A still open
        self.login("whmgr")
        page = self.c.get("/purchasing/ref/%d" % ref_id).get_data(as_text=True)
        self.assertIn("Partly ordered", page)
        self.assertIn("Choose suppliers", page)
        # later Sup B quotes A
        self.quote(ref_id, SUP_B, lines=[(A, 20, "10.00", 7)])
        r = self.choose(ref_id, **{str(A): "q:%d" % self.qid(SUP_B)})
        self.assertEqual(self.be.one("SELECT COUNT(*) AS n FROM purchase_approvals")["n"], 2)
        aid = self.be.one("SELECT id FROM purchase_approvals ORDER BY id DESC LIMIT 1")["id"]
        self.login("owner")
        self.c.post("/purchasing/approvals/%d" % aid, data={"decision": "approve", "remarks": "ok"})
        self.assertEqual(self.be.one("SELECT COUNT(*) AS n FROM purchase_orders")["n"], 2)
        self.assertEqual(self.ref_status(), "approved")

    def test_a_supplier_that_can_only_part_supply_leaves_the_balance_open(self):
        ref_id = self.two_item_ref(qa=20, qb=10)
        self.quote(ref_id, SUP_A, lines=[(A, 12, "10.00", 5), (B, 10, "20.00", 5)])       # only 12 of the 20 tires
        self.choose(ref_id, **{str(A): "q:%d" % self.qid(SUP_A), str(B): "q:%d" % self.qid(SUP_A)})
        self.owner_decides()
        qty = self.be.one("SELECT qty_ordered FROM purchase_order_items WHERE item_id = :i", i=A)["qty_ordered"]
        self.assertEqual(qty, 12)
        self.assertEqual(self.ref_status(), "rfq_sent")                                   # 8 more still to order
        from app import ref_coverage as cov
        with self.app.app_context():
            self.assertEqual({l["item_id"]: l["open"] for l in cov.coverage(ref_id)}, {A: 8, B: 0})

    # ---- dropping what nobody can supply (from the REF page) ----
    def test_drop_needs_a_reason_and_lets_the_ref_finish(self):
        ref_id = self.two_item_ref()
        self.quote(ref_id, SUP_A, lines=[(B, 10, "20.00", 5)])
        self.choose(ref_id, **{str(A): "open", str(B): "q:%d" % self.qid(SUP_A)})
        self.owner_decides()
        ids = self.line_ids(ref_id)
        self.login("whmgr")
        r = self.c.post("/purchasing/ref/%d/drop" % ref_id,
                        data={"ref_item_id": str(ids[A]), "reason": ""}, follow_redirects=True)
        self.assertIn("write why you are dropping it", r.get_data(as_text=True))
        self.c.post("/purchasing/ref/%d/drop" % ref_id,
                    data={"ref_item_id": str(ids[A]), "reason": "discontinued by maker"})
        row = self.be.one("SELECT qty_dropped, dropped_reason FROM replenishment_items WHERE item_id = :i", i=A)
        self.assertEqual((row["qty_dropped"], row["dropped_reason"]), (20, "discontinued by maker"))
        self.assertEqual(self.ref_status(), "approved")                                   # nothing left open
        # receiving only what was ordered closes the REF
        self.be.execute("UPDATE replenishment_items SET qty_received = qty_requested WHERE item_id = :i", i=B)
        self.be.commit()
        from app import ref_coverage as cov
        with self.app.app_context():
            self.assertTrue(cov.all_received(ref_id))
            self.assertEqual(cov.refresh_status(ref_id), "closed")

    def test_drop_an_open_item_later_from_the_ref_page(self):
        ref_id = self.two_item_ref()
        self.quote(ref_id, SUP_A, lines=[(B, 10, "20.00", 5)])
        self.choose(ref_id, **{str(A): "open", str(B): "q:%d" % self.qid(SUP_A)})
        self.owner_decides()
        ids = self.line_ids(ref_id)
        self.login("whmgr")
        r = self.c.post("/purchasing/ref/%d/drop" % ref_id, data={"ref_item_id": str(ids[A]), "reason": ""}, follow_redirects=True)
        self.assertIn("write why you are dropping it", r.get_data(as_text=True))
        self.c.post("/purchasing/ref/%d/drop" % ref_id, data={"ref_item_id": str(ids[A]), "reason": "customer cancelled"})
        self.assertEqual(self.ref_status(), "approved")
        self.assertEqual(self.be.one("SELECT qty_dropped FROM replenishment_items WHERE item_id = :i", i=A)["qty_dropped"], 20)

    def test_cannot_drop_everything_with_no_order(self):
        ref_id = self.two_item_ref()
        ids = self.line_ids(ref_id)
        self.login("whmgr")
        for item in (A,):
            self.c.post("/purchasing/ref/%d/drop" % ref_id, data={"ref_item_id": str(ids[item]), "reason": "not needed any more"})
        r = self.c.post("/purchasing/ref/%d/drop" % ref_id, data={"ref_item_id": str(ids[B]), "reason": "not needed any more"}, follow_redirects=True)
        self.assertIn("Cancel the REF instead", r.get_data(as_text=True))

    # ---- owner decides each supplier ----
    def test_owner_can_approve_one_supplier_and_reject_the_other(self):
        ref_id = self.two_item_ref()
        self.mixed_quotes(ref_id)
        ids = self.line_ids(ref_id)
        self.login("whmgr")
        self.c.post("/purchasing/ref/%d/compare" % ref_id, data={
            "choice_%d" % ids[A]: "q:%d" % self.qid(SUP_B), "choice_%d" % ids[B]: "q:%d" % self.qid(SUP_A)})
        a_sup_a = self.be.one("SELECT id FROM purchase_approvals WHERE supplier_id = :s", s=SUP_A)["id"]
        a_sup_b = self.be.one("SELECT id FROM purchase_approvals WHERE supplier_id = :s", s=SUP_B)["id"]
        # rejecting needs remarks
        self.login("owner")
        r = self.c.post("/purchasing/approvals/%d" % a_sup_a, data={"decision_%d" % a_sup_a: "approve",
                        "decision_%d" % a_sup_b: "reject", "remarks": ""}, follow_redirects=True)
        self.assertEqual(self.be.one("SELECT COUNT(*) AS n FROM purchase_orders")["n"], 0)    # all or nothing
        self.c.post("/purchasing/approvals/%d" % a_sup_a, data={"decision_%d" % a_sup_a: "approve",
                    "decision_%d" % a_sup_b: "reject", "remarks": "Sup B price too high"})
        self.assertEqual([p["supplier_id"] for p in self.be.all("SELECT supplier_id FROM purchase_orders")], [SUP_A])
        self.assertEqual(self.ref_status(), "rfq_sent")                                    # A (Sup B's) is open again
        from app import ref_coverage as cov
        with self.app.app_context():
            self.assertEqual({l["item_id"]: l["open"] for l in cov.coverage(ref_id)}, {A: 20, B: 0})

    def test_owner_must_decide_every_supplier(self):
        ref_id = self.two_item_ref()
        self.mixed_quotes(ref_id)
        ids = self.line_ids(ref_id)
        self.login("whmgr")
        self.c.post("/purchasing/ref/%d/compare" % ref_id, data={
            "choice_%d" % ids[A]: "q:%d" % self.qid(SUP_B), "choice_%d" % ids[B]: "q:%d" % self.qid(SUP_A)})
        a1 = self.be.one("SELECT id FROM purchase_approvals ORDER BY id")["id"]
        self.login("owner")
        r = self.c.post("/purchasing/approvals/%d" % a1, data={"decision_%d" % a1: "approve", "remarks": "ok"},
                        follow_redirects=True)
        self.assertIn("Choose approve or reject for", r.get_data(as_text=True))
        self.assertEqual(self.be.one("SELECT COUNT(*) AS n FROM purchase_orders")["n"], 0)

    # ---- cancelling ----
    def test_cancelling_a_po_frees_its_items_to_order_elsewhere(self):
        ref_id = self.two_item_ref()
        self.mixed_quotes(ref_id)
        ids = self.line_ids(ref_id)
        self.login("whmgr")
        self.c.post("/purchasing/ref/%d/compare" % ref_id, data={
            "choice_%d" % ids[A]: "q:%d" % self.qid(SUP_B), "choice_%d" % ids[B]: "q:%d" % self.qid(SUP_A)})
        self.owner_decides()
        po = self.be.one("SELECT id FROM purchase_orders WHERE supplier_id = :s", s=SUP_B)
        self.login("whmgr")
        self.c.post("/purchasing/po/%d/cancel" % po["id"], data={"cancel_reason": "supplier cannot deliver"})
        self.assertEqual(self.ref_status(), "rfq_sent")
        from app import ref_coverage as cov
        with self.app.app_context():
            self.assertEqual({l["item_id"]: l["open"] for l in cov.coverage(ref_id)}, {A: 20, B: 0})

    def test_a_ref_with_active_purchase_orders_cannot_be_cancelled(self):
        ref_id = self.two_item_ref()
        self.quote(ref_id, SUP_A, lines=[(B, 10, "20.00", 5)])
        self.choose(ref_id, **{str(A): "open", str(B): "q:%d" % self.qid(SUP_A)})
        self.owner_decides()
        self.login("whmgr")
        page = self.c.get("/purchasing/ref/%d" % ref_id).get_data(as_text=True)
        self.assertNotIn("Cancel REF", page)                                              # no button offered
        r = self.c.post("/purchasing/ref/%d/cancel" % ref_id, data={"cancel_reason": "oops"}, follow_redirects=True)
        self.assertIn("already has purchase orders", r.get_data(as_text=True))
        self.assertNotEqual(self.ref_status(), "cancelled")

    # ---- validation ----
    def test_choosing_rules(self):
        ref_id = self.two_item_ref()
        self.mixed_quotes(ref_id)
        ids = self.line_ids(ref_id)
        qa, qb = self.qid(SUP_A), self.qid(SUP_B)
        def attempt(**c):
            r = self.choose(ref_id, **c)
            return " ".join(self.flashes(r))
        # a line left without any choice
        self.login("whmgr")
        r = self.c.post("/purchasing/ref/%d/compare" % ref_id, data={"choice_%d" % ids[B]: "q:%d" % qa,
                        "choice_%d" % ids[A]: ""}, follow_redirects=True)
        self.assertIn("choose a supplier or keep it open", r.get_data(as_text=True))
        # a supplier that did not offer the item (Sup A never quoted A)
        self.assertIn("did not offer this item", attempt(**{str(A): "q:%d" % qa, str(B): "q:%d" % qa}))
        # more than the supplier offered
        self.assertIn("order between 1 and 10", attempt(**{str(A): "q:%d" % qb, str(B): ("q:%d" % qa, "11", "")}))
        # nothing assigned at all
        self.assertIn("at least one item", attempt(**{str(A): "open", str(B): "open"}))
        self.assertEqual(self.be.one("SELECT COUNT(*) AS n FROM purchase_approvals")["n"], 0)
        self.assertEqual(self.be.one("SELECT SUM(qty_dropped) AS n FROM replenishment_items")["n"], 0)

    def test_nothing_is_saved_when_one_line_is_invalid(self):
        ref_id = self.two_item_ref()
        self.mixed_quotes(ref_id)
        qa, qb = self.qid(SUP_A), self.qid(SUP_B)
        r = self.choose(ref_id, **{str(A): "q:%d" % qb, str(B): ("q:%d" % qa, "99", "")})
        self.assertEqual(self.be.one("SELECT COUNT(*) AS n FROM purchase_approvals")["n"], 0)
        self.assertEqual(self.be.one("SELECT COUNT(*) AS n FROM purchase_approval_items")["n"], 0)

    def test_cannot_submit_again_while_the_owner_is_deciding(self):
        ref_id = self.two_item_ref()
        self.mixed_quotes(ref_id)
        qa, qb = self.qid(SUP_A), self.qid(SUP_B)
        self.choose(ref_id, **{str(A): "q:%d" % qb, str(B): "q:%d" % qa})
        r = self.choose(ref_id, **{str(A): "open", str(B): "open"})
        self.assertEqual(self.be.one("SELECT COUNT(*) AS n FROM purchase_approvals")["n"], 2)

    def test_expired_quotation_cannot_be_chosen(self):
        ref_id = self.two_item_ref()
        self.quote(ref_id, SUP_A, lines=[(B, 10, "20.00", 5)], valid_until="2026-11-02")
        self.be.execute("UPDATE quotations SET valid_until = '2020-01-01'")
        self.be.commit()
        r = self.choose(ref_id, **{str(A): "open", str(B): "q:%d" % self.qid(SUP_A)})
        self.assertEqual(self.be.one("SELECT COUNT(*) AS n FROM purchase_approvals")["n"], 0)

    def test_regular_supplier_agreed_price_can_cover_one_item_only(self):
        ref_id = self.two_item_ref()
        self.quote(ref_id, SUP_B, lines=[(B, 10, "18.00", 5)])
        self.choose(ref_id, **{str(A): "a:%d" % SUP_A, str(B): "q:%d" % self.qid(SUP_B)})
        approvals = {a["supplier_id"]: a["basis"] for a in self.be.all("SELECT supplier_id, basis FROM purchase_approvals")}
        self.assertEqual(approvals, {SUP_A: "agreed_price", SUP_B: "quotation"})

    def test_unquoted_items_stay_open_when_choosing_one_supplier(self):
        ref_id = self.two_item_ref()
        self.quote(ref_id, SUP_A, lines=[(B, 10, "20.00", 5)])
        r = self.compare_quote(ref_id)
        self.assertEqual(r.status_code, 302)
        self.owner_decides()
        self.assertEqual(self.ref_status(), "rfq_sent")                                   # A was not lost
        from app import ref_coverage as cov
        with self.app.app_context():
            self.assertEqual({l["item_id"]: l["open"] for l in cov.coverage(ref_id)}, {A: 20, B: 0})

    def test_choose_suppliers_page_only_for_the_warehouse_manager(self):
        ref_id = self.two_item_ref()
        for who, code in (("owner", 403), ("shopmgr", 403), ("whstaff", 403)):
            self.login(who)
            self.assertEqual(self.c.get("/purchasing/ref/%d/compare" % ref_id).status_code, code, who)
            self.assertEqual(self.c.post("/purchasing/ref/%d/drop" % ref_id, data={}).status_code, code, who)


class MoreSuppliersTests(Base):
    """A supplier does not have an item: ask more suppliers, never silently lose the need."""

    def two_item_ref(self, qa=20, qb=10):
        self.login("whmgr")
        self.c.post("/purchasing/ref/new", data={
            "remarks": "split", "item_id[]": [str(A), str(B)], "qty_requested[]": [str(qa), str(qb)],
            "reason[]": ["below_rop", "below_rop"], "action": "submit_ref"})
        ref_id = self.ref_id()
        self.send_rfq(ref_id)
        return ref_id

    def qid(self, supplier):
        return self.be.one("SELECT id FROM quotations WHERE supplier_id = :s ORDER BY id DESC LIMIT 1", s=supplier)["id"]

    def choose(self, ref_id, **choices):
        ids = self.line_ids(ref_id)
        data = {}
        for item, c in choices.items():
            choice, qty = c[:2] if isinstance(c, tuple) else (c, "")
            rid = ids[int(item)]
            data["choice_%d" % rid], data["qty_%d" % rid] = choice, qty
        self.login("whmgr")
        return self.c.post("/purchasing/ref/%d/compare" % ref_id, data=data, follow_redirects=True)

    def approve(self):
        aid = self.be.one("SELECT MIN(id) AS i FROM purchase_approvals WHERE decision = 'pending'")["i"]
        self.login("owner")
        self.c.post("/purchasing/approvals/%d" % aid, data={"decision": "approve", "remarks": "ok", "expected_date": "2026-12-01"})

    def reset_alerts(self, item_stock):
        """No alerts yet, and the warehouse holds item_stock = {item: (on_hand, rop)}."""
        self.be.execute("DELETE FROM notification_items")
        self.be.execute("DELETE FROM notifications")
        for item, (on_hand, rop) in item_stock.items():
            self.be.execute("DELETE FROM stock_balances WHERE item_id = :i AND location_id = :l", i=item, l=WH)
            self.be.execute("INSERT INTO stock_balances (item_id, location_id, on_hand, reserved) VALUES (:i, :l, :q, 0)",
                            i=item, l=WH, q=on_hand)
            self.be.execute("DELETE FROM item_location_settings WHERE item_id = :i AND location_id = :l", i=item, l=WH)
            self.be.execute("INSERT INTO item_location_settings (item_id, location_id, rop, max_level, lead_time_days) "
                            "VALUES (:i, :l, :r, 30, 7)", i=item, l=WH, r=rop)
        self.be.commit()

    def active_alert_items(self):
        return [r["item_id"] for r in self.be.all(
            "SELECT ni.item_id FROM notification_items ni JOIN notifications n ON n.id = ni.notification_id "
            "WHERE n.status = 'active'")]

    # ---- a second RFQ for what is still open ----
    def test_a_second_rfq_covers_only_the_items_still_open_and_can_ask_new_suppliers(self):
        ref_id = self.two_item_ref()
        self.quote(ref_id, SUP_A, lines=[(B, 10, "20.00", 5)])                 # nobody has A
        self.choose(ref_id, **{str(A): "open", str(B): "q:%d" % self.qid(SUP_A)})
        self.approve()
        self.login("whmgr")
        page = self.c.get("/purchasing/ref/%d" % ref_id).get_data(as_text=True)
        self.assertIn("Ask more suppliers", page)
        form = self.c.get("/purchasing/ref/%d/rfq/new" % ref_id).get_data(as_text=True)
        self.assertIn("Items still without a supplier", form)
        self.assertIn("ANSU", form)                                            # item A
        self.assertNotIn("RODEO", form)                                        # item B is already ordered
        self.assertIn("already asked in RFQ-", form)                           # Sup A / Sup B were on RFQ 1
        r = self.c.post("/purchasing/ref/%d/rfq/new" % ref_id, data={"supplier_ids[]": [str(SUP_B)], "reply_deadline": ""})
        self.assertEqual(r.status_code, 302)
        self.assertEqual(self.be.one("SELECT COUNT(*) AS n FROM rfqs")["n"], 2)
        self.assertEqual(self.be.one("SELECT status FROM replenishments")["status"], "rfq_sent")
        # the quotation form now only offers the open item
        q = self.c.get("/purchasing/ref/%d/quotation/new" % ref_id).get_data(as_text=True)
        self.assertIn("ANSU", q)
        self.assertNotIn("RODEO", q)
        # Sup B quotes it under the new RFQ, and the owner can approve it as a second PO
        self.quote(ref_id, SUP_B, lines=[(A, 20, "10.00", 7)])
        self.choose(ref_id, **{str(A): "q:%d" % self.qid(SUP_B)})
        self.approve()
        self.assertEqual(self.be.one("SELECT COUNT(*) AS n FROM purchase_orders")["n"], 2)
        self.assertEqual(self.be.one("SELECT status FROM replenishments")["status"], "approved")

    def test_the_same_supplier_across_two_rounds_is_one_approval_and_one_po(self):
        ref_id = self.two_item_ref()
        self.quote(ref_id, SUP_A, lines=[(B, 10, "20.00", 5)])
        first_q = self.qid(SUP_A)
        self.send_rfq(ref_id, suppliers=(SUP_A,))                              # round 2 for item A
        self.quote(ref_id, SUP_A, lines=[(A, 20, "10.00", 7)])
        second_q = self.qid(SUP_A)
        self.assertNotEqual(first_q, second_q)
        # item B was not re-quoted, so the round-1 line stays valid
        old = self.be.all("SELECT replenishment_item_id, superseded_at FROM quotation_items WHERE quotation_id = :q",
                          q=first_q)
        self.assertEqual([r["replenishment_item_id"] for r in old],
                         [self.line_ids(ref_id)[B]])
        self.assertIsNone(old[0]["superseded_at"])
        self.choose(ref_id, **{str(A): "q:%d" % second_q, str(B): "q:%d" % first_q})
        approvals = self.be.all("SELECT * FROM purchase_approvals")
        self.assertEqual(len(approvals), 1)                                    # one supplier, one approval
        self.assertEqual((approvals[0]["supplier_id"], approvals[0]["basis"], approvals[0]["quotation_id"]),
                         (SUP_A, "quotation", None))                       # two quotations, so no single ref
        self.assertEqual(self.be.one("SELECT COUNT(*) AS n FROM purchase_approval_items")["n"], 2)
        self.approve()
        pos = self.be.all("SELECT * FROM purchase_orders")
        self.assertEqual(len(pos), 1)                                          # one supplier, one PO
        self.assertEqual(self.be.one("SELECT COUNT(*) AS n FROM purchase_order_items WHERE po_id = :p",
                                     p=pos[0]["id"])["n"], 2)

    def test_no_second_rfq_when_nothing_is_open_or_while_the_owner_decides(self):
        ref_id = self.two_item_ref()
        self.quote(ref_id, SUP_A, lines=[(A, 20, "10.00", 5), (B, 10, "20.00", 5)])
        self.choose(ref_id, **{str(A): "q:%d" % self.qid(SUP_A), str(B): "q:%d" % self.qid(SUP_A)})
        self.login("whmgr")
        r = self.c.post("/purchasing/ref/%d/rfq/new" % ref_id, data={"supplier_ids[]": [str(SUP_B)]}, follow_redirects=True)
        self.assertEqual(self.be.one("SELECT COUNT(*) AS n FROM rfqs")["n"], 1)
        self.assertIn("for approval, so another RFQ cannot be sent", r.get_data(as_text=True))   # owner is deciding
        self.approve()
        self.login("whmgr")
        r = self.c.post("/purchasing/ref/%d/rfq/new" % ref_id, data={"supplier_ids[]": [str(SUP_B)]}, follow_redirects=True)
        self.assertEqual(self.be.one("SELECT COUNT(*) AS n FROM rfqs")["n"], 1)                 # everything is ordered

    def test_choose_suppliers_screen_links_to_a_new_rfq_when_nobody_quoted(self):
        ref_id = self.two_item_ref()
        self.be.execute("DELETE FROM supplier_items")                          # no agreed prices either
        self.be.commit()
        self.quote(ref_id, SUP_A, lines=[(B, 10, "20.00", 5)])
        self.login("whmgr")
        page = self.c.get("/purchasing/ref/%d/compare" % ref_id).get_data(as_text=True)
        self.assertIn("No supplier has quoted this item yet", page)
        self.assertIn("/rfq/new", page)
        self.assertIn("keep it open", page)
        self.assertNotIn("Give up on this item", page)          # giving up is not offered while choosing suppliers
        self.assertNotIn('value="drop"', page)

    # ---- giving up is not dismissing (it happens on the REF page) ----
    def test_dropping_needs_a_real_reason(self):
        ref_id = self.two_item_ref()
        ids = self.line_ids(ref_id)
        self.login("whmgr")
        for bad in ("drop", "x", "no", "abc"):
            r = self.c.post("/purchasing/ref/%d/drop" % ref_id,
                            data={"ref_item_id": str(ids[A]), "reason": bad}, follow_redirects=True)
            self.assertIn("write a real reason", r.get_data(as_text=True), bad)
        self.assertEqual(self.be.one("SELECT COALESCE(SUM(qty_dropped), 0) AS n FROM replenishment_items")["n"], 0)
        self.assertEqual(self.be.one("SELECT COUNT(*) AS n FROM purchase_approvals")["n"], 0)

    def test_dropping_an_item_still_below_rop_raises_the_alert_again(self):
        ref_id = self.two_item_ref()
        self.reset_alerts({A: (3, 5), B: (50, 5)})                             # A is below its ROP, B is fine
        ids = self.line_ids(ref_id)
        self.assertNotIn(A, self.active_alert_items())
        self.login("whmgr")
        r = self.c.post("/purchasing/ref/%d/drop" % ref_id,
                        data={"ref_item_id": str(ids[A]), "reason": "supplier stopped selling it"},
                        follow_redirects=True)
        self.assertIn(A, self.active_alert_items())                            # not forgotten: flagged again
        self.assertIn("still below its reorder point", r.get_data(as_text=True))

    def test_dropping_an_item_that_is_fine_raises_no_alert(self):
        ref_id = self.two_item_ref()
        self.reset_alerts({A: (50, 5), B: (50, 5)})                            # both are well stocked
        ids = self.line_ids(ref_id)
        self.login("whmgr")
        self.c.post("/purchasing/ref/%d/drop" % ref_id,
                    data={"ref_item_id": str(ids[A]), "reason": "customer cancelled order"})
        self.assertNotIn(A, self.active_alert_items())

    def test_dropping_from_the_ref_page_also_flags_the_item(self):
        ref_id = self.two_item_ref()
        self.reset_alerts({A: (3, 5), B: (50, 5)})
        self.quote(ref_id, SUP_A, lines=[(B, 10, "20.00", 5)])
        self.choose(ref_id, **{str(A): "open", str(B): "q:%d" % self.qid(SUP_A)})
        self.approve()
        ids = self.line_ids(ref_id)
        self.login("whmgr")
        r = self.c.post("/purchasing/ref/%d/drop" % ref_id, data={"ref_item_id": str(ids[A]), "reason": "drop"}, follow_redirects=True)
        self.assertIn("write a real reason", r.get_data(as_text=True))
        r = self.c.post("/purchasing/ref/%d/drop" % ref_id, data={"ref_item_id": str(ids[A]), "reason": "maker discontinued it"}, follow_redirects=True)
        self.assertIn(A, self.active_alert_items())
        self.assertIn("shows again in the warehouse alerts", r.get_data(as_text=True))


    # ---- restore a given-up item ----
    def test_a_dropped_item_can_be_restored_and_needs_a_supplier_again(self):
        ref_id = self.two_item_ref()
        self.quote(ref_id, SUP_A, lines=[(B, 10, "20.00", 5)])
        self.choose(ref_id, **{str(A): "open", str(B): "q:%d" % self.qid(SUP_A)})
        self.approve()
        ids = self.line_ids(ref_id)
        self.login("whmgr")
        self.c.post("/purchasing/ref/%d/drop" % ref_id, data={"ref_item_id": str(ids[A]), "reason": "supplier has no stock"})
        self.assertEqual(self.be.one("SELECT status FROM replenishments")["status"], "approved")       # nothing open
        page = self.c.get("/purchasing/ref/%d" % ref_id).get_data(as_text=True)
        self.assertIn("Restore", page)
        r = self.c.post("/purchasing/ref/%d/restore" % ref_id, data={"ref_item_id": str(ids[A])}, follow_redirects=True)
        self.assertIn("needs a supplier again", r.get_data(as_text=True))
        row = self.be.one("SELECT qty_dropped, dropped_reason FROM replenishment_items WHERE item_id = :i", i=A)
        self.assertEqual((row["qty_dropped"], row["dropped_reason"]), (0, None))
        self.assertEqual(self.be.one("SELECT status FROM replenishments")["status"], "rfq_sent")       # open again
        page = self.c.get("/purchasing/ref/%d" % ref_id).get_data(as_text=True)
        self.assertIn("Choose suppliers", page)
        self.assertIn("Ask more suppliers", page)

    def test_restore_rules(self):
        ref_id = self.two_item_ref()
        self.quote(ref_id, SUP_A, lines=[(B, 10, "20.00", 5)])
        ids = self.line_ids(ref_id)
        self.login("whmgr")
        r = self.c.post("/purchasing/ref/%d/restore" % ref_id, data={"ref_item_id": str(ids[A])}, follow_redirects=True)
        self.assertIn("was not dropped", r.get_data(as_text=True))                                    # nothing to restore
        self.c.post("/purchasing/ref/%d/drop" % ref_id, data={"ref_item_id": str(ids[A]), "reason": "customer cancelled"})
        # a closed REF cannot be reopened this way
        self.be.execute("UPDATE replenishments SET status = 'closed'")
        self.be.commit()
        r = self.c.post("/purchasing/ref/%d/restore" % ref_id, data={"ref_item_id": str(ids[A])}, follow_redirects=True)
        self.assertIn("Create a new REF for it", r.get_data(as_text=True))
        self.assertEqual(self.be.one("SELECT qty_dropped FROM replenishment_items WHERE item_id = :i", i=A)["qty_dropped"], 20)
        for who in ("owner", "shopmgr", "whstaff"):
            self.login(who)
            self.assertEqual(self.c.post("/purchasing/ref/%d/restore" % ref_id, data={"ref_item_id": str(ids[A])}).status_code, 403, who)

    def test_a_rejected_approval_no_longer_leaves_an_item_given_up_by_accident(self):
        """Choosing suppliers keeps unsupplied items OPEN (not given up), so a rejection loses nothing."""
        ref_id = self.two_item_ref()
        self.quote(ref_id, SUP_A, lines=[(B, 10, "20.00", 5)])
        self.choose(ref_id, **{str(A): "open", str(B): "q:%d" % self.qid(SUP_A)})
        aid = self.be.one("SELECT id FROM purchase_approvals")["id"]
        self.login("owner")
        self.c.post("/purchasing/approvals/%d" % aid, data={"decision": "reject", "remarks": "price too high"})
        self.assertEqual(self.be.one("SELECT SUM(qty_dropped) AS n FROM replenishment_items")["n"], 0)
        from app import ref_coverage as cov
        with self.app.app_context():
            self.assertEqual({l["item_id"]: l["open"] for l in cov.coverage(ref_id)}, {A: 20, B: 10})

    # ---- owner and warehouse manager manage suppliers and agreed prices ----
    def test_owner_and_warehouse_manager_can_both_manage_suppliers_and_agreed_prices(self):
        for who in ("owner", "whmgr"):
            self.login(who)
            page = self.c.get("/purchasing/suppliers").get_data(as_text=True)
            self.assertIn("+ New supplier", page, who)
            r = self.c.post("/purchasing/suppliers/new", data={
                "supplier_name": "Supplier by %s" % who, "email": "%s@example.com" % who, "is_active": "1", "is_regular": "1",
                "item_id[]": [str(A)], "agreed_price[]": ["99.50"], "valid_from[]": ["2026-01-01"], "valid_to[]": [""]})
            self.assertEqual(r.status_code, 302, who)
            sid = self.be.one("SELECT id FROM suppliers WHERE supplier_name = :n", n="Supplier by %s" % who)["id"]
            self.assertEqual(self.c.get("/purchasing/suppliers/%d/edit" % sid).status_code, 200, who)
            price = self.be.one("SELECT agreed_price FROM supplier_items WHERE supplier_id = :s", s=sid)
            self.assertAlmostEqual(float(price["agreed_price"]), 99.5, places=2)
        # another supplier edited by the other role
        self.login("owner")
        sid = self.be.one("SELECT id FROM suppliers WHERE supplier_name = 'Supplier by whmgr'")["id"]
        r = self.c.post("/purchasing/suppliers/%d/edit" % sid, data={
            "supplier_name": "Supplier by whmgr", "email": "whmgr@example.com", "is_active": "1", "is_regular": "1",
            "item_id[]": [str(A)], "agreed_price[]": ["88.00"], "valid_from[]": ["2026-01-01"], "valid_to[]": [""]})
        self.assertEqual(r.status_code, 302)
        self.assertAlmostEqual(float(self.be.one("SELECT agreed_price FROM supplier_items WHERE supplier_id = :s", s=sid)["agreed_price"]), 88.0, places=2)

    def test_staff_cannot_manage_suppliers(self):
        for who in ("shopmgr", "shopstaff", "whstaff"):
            self.login(who)
            self.assertEqual(self.c.get("/purchasing/suppliers/new").status_code, 403, who)


if __name__ == "__main__":
    unittest.main()
