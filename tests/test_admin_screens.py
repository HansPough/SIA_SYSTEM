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
