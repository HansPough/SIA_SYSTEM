"""Click-through tests for Module 1 (Requisition): low-stock alerts and RFs.

Run:  python -m unittest discover tests -v
Uses the same in-memory SQLite copy of the tables as the Inventory tests.
"""
import datetime as dt
import os
import re
import sys
import unittest

sys.path.insert(0, os.path.dirname(__file__))
from app import create_app
from app import inventory_service as inv
from app import inventory_ops as inv_ops
from sqlite_schema import SqliteBackend

WH, SHOP, SHOP2 = 1, 2, 3
A, B = 1, 2


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
        b.execute("INSERT INTO locations (code, name, loc_type) VALUES ('SHOP2', 'Shop 2', 'shop')")
        for name, role, loc in (("owner", "owner", None), ("whmgr", "warehouse_manager", WH),
                                ("whstaff", "warehouse_staff", WH), ("shopmgr", "shop_manager", SHOP),
                                ("shopstaff", "shop_staff", SHOP), ("shopmgr2", "shop_manager", SHOP2)):
            b.execute("INSERT INTO users (username, full_name, role_id, location_id) VALUES (:u, :u, "
                      "(SELECT id FROM roles WHERE code = :r), :l)", u=name, r=role, l=loc)
        self.uid = {r["username"]: r["id"] for r in b.all("SELECT id, username FROM users")}
        b.execute("INSERT INTO items (item_code, item_name, brand) VALUES ('TIR-0001', 'ANSU 12R22.5 18PR AL505', 'ANSU')")
        b.execute("INSERT INTO items (item_code, item_name, brand) VALUES ('TIR-0002', 'RODEO 1200R20 20PR RT201', 'RODEO')")
        b.execute("INSERT INTO item_location_settings (item_id, location_id, rop, max_level, lead_time_days) VALUES (1, 2, 5, 20, 3)")
        b.execute("INSERT INTO item_location_settings (item_id, location_id, rop, max_level, lead_time_days) VALUES (1, 1, 8, 40, 30)")
        b.execute("INSERT INTO item_location_settings (item_id, location_id, rop, max_level, lead_time_days) VALUES (2, 1, 3, 12, 30)")
        inv.change_stock(A, SHOP, 10, "opening")
        inv.change_stock(A, WH, 20, "opening")
        inv.change_stock(B, WH, 4, "opening")
        b.commit()
        self.app = create_app()
        self.app.config["TESTING"] = True
        self.app.config["DEV_LOGIN"] = True
        self.c = self.app.test_client()

    def login(self, name):
        self.c.get("/dev/user/%d" % self.uid[name])

    def flashes(self, resp):
        return re.findall(r'class="flash [a-z]+">([^<]*)<', resp.get_data(as_text=True))

    def make_alert(self, qty=5):
        """Sell enough at the shop to reach the ROP, which makes Inventory raise the alert."""
        self.login("shopstaff")
        self.c.post("/inventory/issues/new", data={"issue_date": "2026-10-30", "reason": "sale",
                    "item_id[]": [str(A)], "qty[]": [str(qty)]})
        return self.be.one("SELECT * FROM notifications WHERE notif_type = 'low_stock'")

    def make_rf(self, shop="shopmgr", warehouse=WH, qty=6, submit=True, alert_id=None, item=A):
        self.login(shop)
        data = {"warehouse_id": str(warehouse), "remarks": "test",
                "item_id[]": [str(item)], "qty_requested[]": [str(qty)],
                "action": "submit_rf" if submit else "save_draft"}
        if alert_id:
            data["alert_id"] = str(alert_id)
        return self.c.post("/requisition/rf/new", data=data)


class AlertScreenTests(Base):
    def test_alert_list_and_detail_show_the_items(self):
        self.make_alert()
        self.login("shopmgr")
        page = self.c.get("/requisition/alerts").get_data(as_text=True)
        self.assertIn("ALT-0001", page)
        self.assertIn("Active", page)
        detail = self.c.get("/requisition/alerts/1").get_data(as_text=True)
        self.assertIn("Low-stock Alert ALT-0001", detail)
        self.assertIn("ANSU 12R22.5 18PR AL505", detail)
        self.assertIn("15", detail)                       # suggested qty = max 20 - available 5

    def test_alerts_are_only_for_the_shop_manager(self):
        self.make_alert()
        for who in ("whmgr", "owner", "shopstaff", "whstaff"):
            self.login(who)
            self.assertEqual(self.c.get("/requisition/alerts").status_code, 403)
            self.assertEqual(self.c.get("/requisition/alerts/1").status_code, 403)

    def test_another_shops_manager_cannot_open_the_alert(self):
        self.make_alert()
        self.login("shopmgr2")
        self.assertEqual(self.c.get("/requisition/alerts/1").status_code, 404)
        self.assertEqual(self.c.get("/requisition/alerts/1/dismiss").status_code, 404)

    def test_dismiss_needs_a_reason_and_saves_it(self):
        self.make_alert()
        self.login("shopmgr")
        r = self.c.post("/requisition/alerts/1/dismiss", data={"dismissed_reason": "   "})
        self.assertEqual(r.status_code, 200)
        self.assertIn("reason is required", " ".join(self.flashes(r)))
        self.assertEqual(self.be.one("SELECT status FROM notifications WHERE id = 1")["status"], "active")

        r = self.c.post("/requisition/alerts/1/dismiss", data={"dismissed_reason": "stock already ordered"})
        self.assertEqual(r.status_code, 302)
        row = self.be.one("SELECT * FROM notifications WHERE id = 1")
        self.assertEqual((row["status"], row["dismissed_reason"], row["closed_by"]),
                         ("dismissed", "stock already ordered", self.uid["shopmgr"]))
        self.assertIsNotNone(row["closed_at"])
        audit = self.be.one("SELECT * FROM audit_log WHERE action = 'dismiss'")
        self.assertEqual(audit["record_id"], 1)

    def test_a_dismissed_alert_is_not_reopened(self):
        self.make_alert()
        self.login("shopmgr")
        self.c.post("/requisition/alerts/1/dismiss", data={"dismissed_reason": "slow item"})
        # another sale keeps the item low: Inventory makes a fresh alert, it does not reopen the old one
        self.login("shopstaff")
        self.c.post("/inventory/issues/new", data={"issue_date": "2026-10-30", "reason": "sale",
                    "item_id[]": [str(A)], "qty[]": ["1"]})
        rows = self.be.all("SELECT id, status FROM notifications ORDER BY id")
        self.assertEqual(rows[0]["status"], "dismissed")
        self.assertEqual(rows[1]["status"], "active")


class RequisitionFormTests(Base):
    def test_new_rf_prefills_from_the_alert_and_submits(self):
        alert = self.make_alert()
        self.login("shopmgr")
        page = self.c.get("/requisition/rf/new?alert_id=%d" % alert["id"]).get_data(as_text=True)
        self.assertIn("ANSU 12R22.5 18PR AL505", page)
        self.assertIn('value="15"', page)                 # suggested quantity is the default

        r = self.make_rf(qty=15, alert_id=alert["id"])
        self.assertEqual(r.status_code, 302)
        rf = self.be.one("SELECT * FROM requisitions")
        self.assertEqual((rf["status"], rf["shop_id"], rf["warehouse_id"], rf["notification_id"]),
                         ("open", SHOP, WH, alert["id"]))
        self.assertIsNotNone(rf["submitted_at"])
        line = self.be.one("SELECT * FROM requisition_items WHERE rf_id = :r", r=rf["id"])
        self.assertEqual((line["item_id"], line["qty_requested"]), (A, 15))
        self.assertEqual(rf["rf_no"], "RF-%d-0001" % dt.date.today().year)
        # it is now in the warehouse manager's RF queue
        self.assertEqual(inv_ops.list_rf_queue(WH)["total"], 1)
        # sending the form signed it, with the logged-in account's name
        sig = self.be.all("SELECT doc_type, slot, signer_name, signer_role FROM document_signatures ORDER BY id")
        self.assertEqual([(s["doc_type"], s["slot"], s["signer_name"], s["signer_role"]) for s in sig],
                         [("RF", "requested_by", "shopmgr", "shop_manager")])

    def test_save_draft_then_submit_from_the_detail_page(self):
        self.login("shopmgr")
        r = self.make_rf(qty=4, submit=False)
        self.assertEqual(r.status_code, 302)
        rf = self.be.one("SELECT * FROM requisitions")
        self.assertEqual((rf["status"], rf["submitted_at"]), ("draft", None))
        self.assertEqual(inv_ops.list_rf_queue(WH)["total"], 0)
        self.assertEqual(self.be.one("SELECT COUNT(*) AS n FROM document_signatures")["n"], 0)   # a draft is not signed
        # a draft is private to the shop: the warehouse cannot see it yet
        self.login("whmgr")
        self.assertNotIn(rf["rf_no"], self.c.get("/requisition/rf").get_data(as_text=True))
        self.assertEqual(self.c.get("/requisition/rf/%d" % rf["id"]).status_code, 404)

        self.login("shopmgr")
        detail = self.c.get("/requisition/rf/%d" % rf["id"]).get_data(as_text=True)
        self.assertIn("Sign &amp; send RF to warehouse", detail)
        self.c.post("/requisition/rf/%d" % rf["id"], data={"action": "submit_rf"})
        rf = self.be.one("SELECT * FROM requisitions")
        self.assertEqual(rf["status"], "open")
        self.assertIsNotNone(rf["submitted_at"])
        self.assertEqual(inv_ops.list_rf_queue(WH)["total"], 1)
        self.assertEqual(self.be.one("SELECT slot FROM document_signatures")["slot"], "requested_by")

    def test_rf_validation(self):
        self.login("shopmgr")
        base = {"warehouse_id": str(WH), "remarks": ""}
        bad = [dict(base, **{"item_id[]": [""], "qty_requested[]": [""]}),
               dict(base, **{"item_id[]": [str(A)], "qty_requested[]": ["0"]}),
               dict(base, **{"item_id[]": [str(A)], "qty_requested[]": ["1"]}, warehouse_id=""),
               dict(base, **{"item_id[]": [str(A)], "qty_requested[]": ["1"]}, warehouse_id=str(SHOP))]
        for data in bad:
            r = self.c.post("/requisition/rf/new", data=data)
            self.assertEqual(r.status_code, 200)
        self.assertEqual(self.be.one("SELECT COUNT(*) AS n FROM requisitions")["n"], 0)

    def test_shop_manager_cannot_request_from_another_shop(self):
        self.login("shopmgr2")
        # even if the form is posted with a different shop id in the open, the session's shop is used
        r = self.c.post("/requisition/rf/new", data={"warehouse_id": str(WH), "shop_id": str(SHOP),
                        "item_id[]": [str(A)], "qty_requested[]": ["2"]})
        self.assertEqual(r.status_code, 302)
        self.assertEqual(self.be.one("SELECT shop_id FROM requisitions")["shop_id"], SHOP2)

    def test_warehouse_and_owner_cannot_create_or_dismiss(self):
        self.make_alert()
        for who in ("whmgr", "owner"):
            self.login(who)
            self.assertEqual(self.c.get("/requisition/rf/new").status_code, 403)
            self.assertEqual(self.c.get("/requisition/alerts/1/dismiss").status_code, 403)

    def test_rf_list_is_scoped_by_role(self):
        self.make_rf(shop="shopmgr", qty=3)
        self.make_rf(shop="shopmgr2", qty=2)
        self.login("shopmgr")
        page = self.c.get("/requisition/rf").get_data(as_text=True)
        self.assertIn("SHOP", page)
        self.assertNotIn("SHOP2", page)
        self.login("shopmgr2")
        page = self.c.get("/requisition/rf").get_data(as_text=True)
        self.assertIn("SHOP2", page)
        self.assertNotIn(">SHOP<", page)
        self.login("whmgr")
        page = self.c.get("/requisition/rf").get_data(as_text=True)
        self.assertIn("SHOP2", page)
        self.login("owner")
        page = self.c.get("/requisition/rf").get_data(as_text=True)
        self.assertIn("SHOP2", page)

    def test_empty_and_filtered_screens_render(self):
        self.login("shopmgr2")
        for url in ("/requisition/alerts", "/requisition/alerts?status=active&q=ANSU",
                    "/requisition/rf", "/requisition/rf?status=open&q=RF"):
            self.assertEqual(self.c.get(url).status_code, 200)
        self.make_rf(qty=2)
        rf_id = self.be.one("SELECT id FROM requisitions")["id"]
        self.login("shopmgr")
        self.c.post("/requisition/rf/%d/cancel" % rf_id, data={"cancel_reason": "no longer needed"})
        self.login("owner")
        page = self.c.get("/requisition/rf/%d" % rf_id)
        self.assertEqual(page.status_code, 200)
        self.assertIn("Cancelled because", page.get_data(as_text=True))

    def test_rf_detail_is_scoped_by_role(self):
        self.make_rf(shop="shopmgr", qty=3)
        rf_id = self.be.one("SELECT id FROM requisitions")["id"]
        self.login("shopmgr2")
        self.assertEqual(self.c.get("/requisition/rf/%d" % rf_id).status_code, 404)
        self.login("whmgr")
        self.assertEqual(self.c.get("/requisition/rf/%d" % rf_id).status_code, 200)
        self.login("whstaff")
        self.assertEqual(self.c.get("/requisition/rf/%d" % rf_id).status_code, 403)
        self.login("owner")
        self.assertEqual(self.c.get("/requisition/rf/%d" % rf_id).status_code, 200)


class CancelTests(Base):
    def reserve(self, rf_id, qty):
        self.login("whmgr")
        return self.c.post("/inventory/rf-queue/%d" % rf_id,
                           data={"rf_item_id[]": ["1"], "qty_reserve[]": [str(qty)]})

    def test_cancel_releases_reserved_stock(self):
        self.make_rf(qty=6)
        rf_id = self.be.one("SELECT id FROM requisitions")["id"]
        self.reserve(rf_id, 6)
        self.assertEqual(inv.get_balance(A, WH)["reserved"], 6)
        self.assertEqual(inv.get_balance(A, WH)["available"], 14)

        self.login("shopmgr")
        self.assertEqual(self.c.get("/requisition/rf/%d/cancel" % rf_id).status_code, 200)
        r = self.c.post("/requisition/rf/%d/cancel" % rf_id, data={"cancel_reason": "ordered twice"})
        self.assertEqual(r.status_code, 302)
        rf = self.be.one("SELECT * FROM requisitions WHERE id = :r", r=rf_id)
        self.assertEqual((rf["status"], rf["cancel_reason"]), ("cancelled", "ordered twice"))
        self.assertIsNotNone(rf["closed_at"])
        self.assertEqual(inv.get_balance(A, WH)["reserved"], 0)
        self.assertEqual(inv.get_balance(A, WH)["available"], 20)
        self.assertEqual(self.be.one("SELECT status FROM reservations WHERE rf_item_id = 1")["status"], "cancelled")
        self.assertEqual(self.be.one("SELECT qty_reserved FROM requisition_items WHERE rf_id = :r", r=rf_id)["qty_reserved"], 0)
        self.assertIsNotNone(self.be.one("SELECT * FROM audit_log WHERE action = 'cancel'"))

    def test_cancel_needs_a_reason(self):
        self.make_rf(qty=2)
        rf_id = self.be.one("SELECT id FROM requisitions")["id"]
        self.login("shopmgr")
        r = self.c.post("/requisition/rf/%d/cancel" % rf_id, data={"cancel_reason": " "})
        self.assertEqual(r.status_code, 200)
        self.assertIn("reason is required", " ".join(self.flashes(r)))
        self.assertEqual(self.be.one("SELECT status FROM requisitions")["status"], "open")

    def test_partially_delivered_or_closed_cannot_be_cancelled(self):
        self.make_rf(qty=2)
        rf_id = self.be.one("SELECT id FROM requisitions")["id"]
        self.be.execute("UPDATE requisitions SET status = 'partially_delivered' WHERE id = :r", r=rf_id)
        self.be.execute("UPDATE requisition_items SET qty_delivered = 1 WHERE rf_id = :r", r=rf_id)
        self.be.commit()
        self.login("shopmgr")
        r = self.c.get("/requisition/rf/%d/cancel" % rf_id)
        self.assertEqual(r.status_code, 302)
        self.assertEqual(self.be.one("SELECT status FROM requisitions")["status"], "partially_delivered")

    def test_another_shop_manager_cannot_cancel(self):
        self.make_rf(shop="shopmgr", qty=2)
        rf_id = self.be.one("SELECT id FROM requisitions")["id"]
        self.login("shopmgr2")
        self.assertEqual(self.c.get("/requisition/rf/%d/cancel" % rf_id).status_code, 404)
        # the detail page is 404 too, so nothing leaks
        self.c.post("/requisition/rf/%d/cancel" % rf_id, data={"cancel_reason": "x"})
        self.assertEqual(self.be.one("SELECT status FROM requisitions")["status"], "open")


if __name__ == "__main__":
    unittest.main()
