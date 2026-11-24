"""Login and owner administration (users, locations)."""
import os
import hashlib
from pathlib import Path
import re
import sys
import unittest

from werkzeug.security import check_password_hash, generate_password_hash

sys.path.insert(0, os.path.dirname(__file__))
from app import create_app
from app import inventory_service as inv
from sqlite_schema import SqliteBackend

WH, SHOP = 1, 2
DEMO_HASH = generate_password_hash("secret123")   # scrypt is slow; hash once for all tests


class Base(unittest.TestCase):
    def setUp(self):
        self.be = SqliteBackend()
        inv.set_backend(self.be)
        inv._backend_supports_locking = False
        b = self.be
        for code, name in (("owner", "Owner"), ("shop_manager", "Shop Manager"),
                           ("shop_staff", "Shop Staff"), ("warehouse_manager", "Warehouse Manager"),
                           ("warehouse_staff", "Warehouse Staff")):
            b.execute("INSERT INTO roles (code, name) VALUES (:c, :n)", c=code, n=name)
        b.execute("INSERT INTO locations (code, name, loc_type) VALUES ('WH', 'Warehouse', 'warehouse')")
        b.execute("INSERT INTO locations (code, name, loc_type) VALUES ('SHOP', 'Shop', 'shop')")
        pw = DEMO_HASH
        for username, full, role, loc in (
                ("owner", "Owner One", "owner", None),
                ("whmgr", "Warehouse Manager", "warehouse_manager", WH),
                ("shopmgr", "Shop Manager", "shop_manager", SHOP)):
            b.execute("INSERT INTO users (username, full_name, password_hash, role_id, location_id, is_active) "
                      "VALUES (:u, :f, :p, (SELECT id FROM roles WHERE code = :r), :l, 1)",
                      u=username, f=full, p=pw, r=role, l=loc)
        b.commit()
        self.app = create_app()
        self.app.config["TESTING"] = True
        self.c = self.app.test_client()

    def login(self, username="owner", password="secret123"):
        return self.c.post("/login", data={"username": username, "password": password})

    def flashes(self, resp):
        return " ".join(re.findall(r'class="flash [a-z]+">([^<]*)<', resp.get_data(as_text=True)))


class LoginTests(Base):
    def test_login_rejects_wrong_password_and_accepts_correct_one(self):
        r = self.login(password="wrong")
        self.assertEqual(r.status_code, 200)
        self.assertIn("Invalid username", self.flashes(r))
        r = self.login()
        self.assertEqual(r.status_code, 302)
        with self.c.session_transaction() as s:
            self.assertEqual((s["role"], s["user_id"]), ("owner", 1))

    def test_dev_role_switch_can_be_disabled(self):
        self.app.config["DEV_LOGIN"] = False
        self.assertEqual(self.c.get("/dev/user/1").status_code, 404)
        self.assertEqual(self.c.get("/dev/role/owner").status_code, 404)
        self.app.config["DEV_LOGIN"] = True
        self.assertEqual(self.c.get("/dev/user/1").status_code, 302)


class NavigationTests(Base):
    def test_layout_versions_assets_by_content(self):
        html = self.c.get("/login").get_data(as_text=True)
        for name in ("css/style.css", "js/app.js"):
            digest = hashlib.sha256((Path(self.app.static_folder) / name).read_bytes()).hexdigest()[:12]
            self.assertIn(f"/static/{name}?v={digest}", html)
            self.assertEqual(self.c.get(f"/static/{name}?v={digest}").status_code, 200)

    def test_detail_page_keeps_parent_navigation_open(self):
        self.login()
        response = self.c.get("/admin/users/2/edit")
        self.assertEqual(response.status_code, 200)
        html = response.get_data(as_text=True)
        self.assertRegex(html, r'<details class="nav-group has-active" data-group="Administration" open>')
        self.assertRegex(html, r'href="/admin/users" class="nav-link active" aria-current="page"')
        # the other groups stay closed so the menu does not clutter
        self.assertRegex(html, r'<details class="nav-group " data-group="Purchasing" >')

    def test_role_navigation_and_error_pages_render(self):
        for role in ("owner", "shop_manager", "shop_staff", "warehouse_manager", "warehouse_staff"):
            with self.c.session_transaction() as session:
                session["role"] = role
            response = self.c.get("/missing-page")
            self.assertEqual(response.status_code, 404)
            self.assertIn('aria-label="Main navigation"', response.get_data(as_text=True))


class UserAdminTests(Base):
    def test_create_user_hashes_password_and_the_user_can_log_in(self):
        self.login()
        r = self.c.post("/admin/users/new", data={
            "username": "newmgr", "full_name": "New Manager", "email": "new@example.test",
            "role_id": "2", "location_id": str(SHOP), "password": "newpass123", "is_active": "on"})
        self.assertEqual(r.status_code, 302)
        row = self.be.one("SELECT * FROM users WHERE username = 'newmgr'")
        self.assertTrue(row["password_hash"])
        self.assertNotIn("newpass123", row["password_hash"])
        self.assertTrue(check_password_hash(row["password_hash"], "newpass123"))
        self.c.get("/logout")
        r = self.login("newmgr", "newpass123")
        self.assertEqual(r.status_code, 302)
        with self.c.session_transaction() as s:
            self.assertEqual(s["role"], "shop_manager")

    def test_duplicate_username_and_role_location_are_refused(self):
        self.login()
        r = self.c.post("/admin/users/new", data={
            "username": "owner", "full_name": "Copy", "role_id": "1", "password": "newpass123"})
        self.assertEqual(r.status_code, 200)
        self.assertIn("already used", self.flashes(r))
        r = self.c.post("/admin/users/new", data={
            "username": "wrongplace", "full_name": "Wrong Place", "role_id": "4",
            "location_id": str(SHOP), "password": "newpass123"})
        self.assertEqual(r.status_code, 200)
        self.assertIn("warehouse role", self.flashes(r))
        self.assertIsNone(self.be.one("SELECT id FROM users WHERE username IN ('Copy', 'wrongplace')"))

    def test_edit_keeps_password_when_blank_and_blocks_self_demotion(self):
        self.login()
        r = self.c.post("/admin/users/2/edit", data={
            "username": "whmgr", "full_name": "Warehouse Manager Updated", "role_id": "4",
            "location_id": str(WH), "password": "", "is_active": "on"})
        self.assertEqual(r.status_code, 302)
        row = self.be.one("SELECT * FROM users WHERE id = 2")
        self.assertEqual(row["full_name"], "Warehouse Manager Updated")
        self.assertTrue(check_password_hash(row["password_hash"], "secret123"))
        r = self.c.post("/admin/users/1/edit", data={
            "username": "owner", "full_name": "Owner One", "role_id": "2", "password": ""})
        self.assertEqual(r.status_code, 200)
        self.assertIn("own owner role", self.flashes(r))

    def deactivate_shop_manager(self):
        self.login()
        return self.c.post("/admin/users/3/edit", data={
            "username": "shopmgr", "full_name": "Shop Manager", "role_id": "2", "location_id": str(SHOP)})

    def open_delivery(self, ts_status, ar_no=None):
        b = self.be
        b.execute("INSERT INTO requisitions (rf_no, shop_id, warehouse_id, status, requested_by) "
                  "VALUES ('RF-2026-0001', 2, 1, 'open', 3)")
        b.execute("INSERT INTO transfer_slips (ts_no, rf_id, from_location_id, to_location_id, status, prepared_by) "
                  "VALUES ('TS-2026-0001', 1, 1, 2, :s, 2)", s=ts_status)
        if ar_no:
            b.execute("INSERT INTO acknowledgment_receipts (ar_no, ts_id, received_by) VALUES (:a, 1, 3)", a=ar_no)
        b.commit()

    def test_the_last_shop_manager_is_kept_while_a_delivery_is_on_the_way(self):
        self.open_delivery("in_transit")
        r = self.deactivate_shop_manager()
        self.assertEqual(r.status_code, 200)
        self.assertIn("last shop manager", self.flashes(r))
        self.assertIn("TS-2026-0001", self.flashes(r))
        self.assertEqual(self.be.one("SELECT is_active FROM users WHERE id = 3")["is_active"], 1)

    def test_the_last_shop_manager_is_kept_while_a_receipt_is_unsigned(self):
        self.open_delivery("delivered", ar_no="AR-2026-0001")
        r = self.deactivate_shop_manager()
        self.assertEqual(r.status_code, 200)
        self.assertIn("AR-2026-0001", self.flashes(r))
        self.assertEqual(self.be.one("SELECT is_active FROM users WHERE id = 3")["is_active"], 1)
        # a second active shop manager can take over, so the deactivation goes through
        self.be.execute("INSERT INTO users (username, full_name, role_id, location_id, is_active) VALUES "
                        "('shopmgr2', 'Shop Manager Two', (SELECT id FROM roles WHERE code = 'shop_manager'), 2, 1)")
        self.be.commit()
        r = self.deactivate_shop_manager()
        self.assertEqual(r.status_code, 302)
        self.assertEqual(self.be.one("SELECT is_active FROM users WHERE id = 3")["is_active"], 0)

    def test_the_last_shop_manager_cannot_change_role_or_move_while_a_delivery_is_on_the_way(self):
        self.open_delivery("in_transit")
        self.login()
        r = self.c.post("/admin/users/3/edit", data={
            "username": "shopmgr", "full_name": "Shop Manager", "role_id": "3", "location_id": str(SHOP),
            "is_active": "on"})
        self.assertEqual(r.status_code, 200)
        self.assertIn("last shop manager", self.flashes(r))
        self.assertEqual(self.be.one("SELECT role_id FROM users WHERE id = 3")["role_id"], 2)
        self.be.execute("INSERT INTO locations (code, name, loc_type) VALUES ('SHOP2', 'Second Shop', 'shop')")
        self.be.commit()
        r = self.c.post("/admin/users/3/edit", data={
            "username": "shopmgr", "full_name": "Shop Manager", "role_id": "2", "location_id": "3",
            "is_active": "on"})
        self.assertEqual(r.status_code, 200)
        self.assertIn("last shop manager", self.flashes(r))
        self.assertEqual(self.be.one("SELECT location_id FROM users WHERE id = 3")["location_id"], SHOP)


class LocationAdminTests(Base):
    def test_create_location_and_duplicate_code(self):
        self.login()
        r = self.c.post("/admin/locations/new", data={
            "code": "shop2", "name": "Second Shop", "loc_type": "shop", "address": "Main St", "is_active": "on"})
        self.assertEqual(r.status_code, 302)
        row = self.be.one("SELECT * FROM locations WHERE code = 'SHOP2'")
        self.assertEqual((row["name"], row["loc_type"]), ("Second Shop", "shop"))
        r = self.c.post("/admin/locations/new", data={
            "code": "SHOP2", "name": "Copy", "loc_type": "shop", "is_active": "on"})
        self.assertEqual(r.status_code, 200)
        self.assertIn("already used", self.flashes(r))

    def test_cannot_deactivate_a_location_with_stock(self):
        self.be.execute("INSERT INTO items (item_code, item_name, brand) VALUES ('T1', 'Tire 1', 'B')")
        self.be.commit()
        inv.change_stock(1, WH, 3, "opening")
        self.be.commit()
        self.login()
        r = self.c.post("/admin/locations/1/edit", data={
            "code": "WH", "name": "Warehouse", "loc_type": "warehouse"})
        self.assertEqual(r.status_code, 200)
        self.assertIn("Cannot deactivate", self.flashes(r))
        self.assertEqual(self.be.one("SELECT is_active FROM locations WHERE id = 1")["is_active"], 1)

    def test_cannot_deactivate_a_location_with_open_documents(self):
        b = self.be
        b.execute("INSERT INTO requisitions (rf_no, shop_id, warehouse_id, status, requested_by) "
                  "VALUES ('RF-2026-0001', 2, 1, 'open', 2)")
        b.execute("INSERT INTO transfer_slips (ts_no, rf_id, from_location_id, to_location_id, status, prepared_by) "
                  "VALUES ('TS-2026-0001', 1, 1, 2, 'in_transit', 3)")
        b.execute("INSERT INTO replenishments (ref_no, warehouse_id, status, created_by) "
                  "VALUES ('REF-2026-0001', 1, 'approved', 2)")
        b.execute("INSERT INTO purchase_orders (po_no, ref_id, supplier_id, approval_id, status) "
                  "VALUES ('PO-2026-0001', 1, 1, 1, 'sent')")
        b.execute("INSERT INTO stock_adjustments (adj_no, location_id, reason, status, requested_by) "
                  "VALUES ('ADJ-2026-0001', 1, 'lost', 'pending', 2)")
        b.execute("INSERT INTO stock_issues (issue_no, location_id, reason, recorded_by, status, void_reason, "
                  "void_requested_by) VALUES ('ISS-2026-0001', 1, 'sale', 2, 'void_requested', 'wrong item', 2)")
        b.commit()
        self.login()
        r = self.c.post("/admin/locations/1/edit", data={
            "code": "WH", "name": "Warehouse", "loc_type": "warehouse"})
        self.assertEqual(r.status_code, 200)
        flash = self.flashes(r)
        for name in ("RF-2026-0001", "TS-2026-0001", "PO-2026-0001", "ADJ-2026-0001", "ISS-2026-0001"):
            self.assertIn(name, flash)
        self.assertEqual(self.be.one("SELECT is_active FROM locations WHERE id = 1")["is_active"], 1)

    def test_long_guard_lists_stop_after_five_documents(self):
        for n in range(1, 8):
            self.be.execute("INSERT INTO requisitions (rf_no, shop_id, warehouse_id, status, requested_by) "
                            "VALUES (:no, 2, 1, 'open', 2)", no="RF-2026-%04d" % n)
        self.be.commit()
        self.login()
        r = self.c.post("/admin/locations/1/edit", data={
            "code": "WH", "name": "Warehouse", "loc_type": "warehouse"})
        flash = self.flashes(r)
        self.assertIn("7 open RFs", flash)
        self.assertIn("RF-2026-0005", flash)
        self.assertIn("…and 2 more", flash)
        self.assertNotIn("RF-2026-0006", flash)


class SecurityTests(Base):
    def test_cross_site_post_is_blocked(self):
        self.login()
        r = self.c.post("/admin/locations/new", data={
            "code": "EVIL", "name": "Evil", "loc_type": "shop", "is_active": "on"},
            headers={"Origin": "http://evil.example"})
        self.assertEqual(r.status_code, 400)
        self.assertIsNone(self.be.one("SELECT id FROM locations WHERE code = 'EVIL'"))
        r = self.c.post("/admin/locations/new", data={
            "code": "OK", "name": "Good", "loc_type": "shop", "is_active": "on"},
            headers={"Origin": "http://localhost"})
        self.assertEqual(r.status_code, 302)

    def test_user_and_location_lists_render_real_rows(self):
        self.login()
        page = self.c.get("/admin/users").get_data(as_text=True)
        self.assertIn("whmgr", page)
        self.assertIn("Shop Manager", page)
        page = self.c.get("/admin/locations").get_data(as_text=True)
        self.assertIn("WH", page)
        self.assertIn("SHOP", page)


if __name__ == "__main__":
    unittest.main()
