"""Admin: users and locations (owner only).

Kept out of the blueprint so the routes stay thin, following the same pattern
as inventory_ops / purchasing_ops / delivery_ops.
"""
import re

from werkzeug.security import generate_password_hash

from . import inventory_service as inv
from .inventory_service import InventoryError

PAGE_SIZE = 50
USERNAME_RE = re.compile(r"^[A-Za-z0-9._-]{3,30}$")
SHOP_ROLES = ("shop_manager", "shop_staff")
WAREHOUSE_ROLES = ("warehouse_manager", "warehouse_staff")


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


def list_roles():
    return _b().all("SELECT id, code, name FROM roles ORDER BY id")


def list_locations(active_only=False):
    sql = "SELECT id, code, name, loc_type, is_active FROM locations"
    if active_only:
        sql += " WHERE is_active"
    return _b().all(sql + " ORDER BY code")


# ---------------------------------------------------------------------
# Users
# ---------------------------------------------------------------------

def list_users(q="", page=1):
    where, p = " WHERE 1 = 1", {}
    if q.strip():
        where += (" AND (LOWER(u.username) LIKE :q OR LOWER(u.full_name) LIKE :q "
                  "OR LOWER(COALESCE(u.email, '')) LIKE :q)")
        p["q"] = _like(q)
    frm = ("FROM users u JOIN roles r ON r.id = u.role_id "
           "LEFT JOIN locations l ON l.id = u.location_id" + where)
    cols = ("SELECT u.id, u.username, u.full_name, u.email, u.is_active, u.created_at, "
            "r.name AS role_name, r.code AS role_code, l.code AS location_code ")
    return _paged("SELECT COUNT(*) AS n " + frm,
                  cols + frm + " ORDER BY u.is_active DESC, u.full_name", page, **p)


def get_user(user_id):
    return _b().one(
        "SELECT u.*, r.code AS role_code, r.name AS role_name, l.code AS location_code "
        "FROM users u JOIN roles r ON r.id = u.role_id "
        "LEFT JOIN locations l ON l.id = u.location_id WHERE u.id = :i", i=user_id)


def save_user(actor, user_id, data):
    """Create or update a user. Returns (user_id, username, created).

    Password is required on create, optional on edit (blank keeps the old one).
    An owner cannot remove their own owner role or deactivate themselves.
    """
    username = (data.get("username") or "").strip()
    full_name = (data.get("full_name") or "").strip()
    email = (data.get("email") or "").strip()
    password = data.get("password") or ""
    is_active = bool(data.get("is_active"))
    if not username:
        raise InventoryError("Username is required.")
    if not USERNAME_RE.fullmatch(username):
        raise InventoryError("Username must be 3-30 characters: letters, numbers, dot, dash or underscore.")
    if not full_name:
        raise InventoryError("Full name is required.")
    if len(full_name) > 100:
        raise InventoryError("Full name can be at most 100 characters.")
    if email and ("@" not in email or len(email) > 120):
        raise InventoryError("Enter a valid email address (or leave it blank).")
    if not user_id and not password:
        raise InventoryError("A password is required for a new user.")
    if password and len(password) < 8:
        raise InventoryError("The password must be at least 8 characters.")

    role = _b().one("SELECT id, code, name FROM roles WHERE id = :i",
                    i=(data.get("role_id") or "").strip())
    if role is None:
        raise InventoryError("Choose a role.")
    if user_id and int(user_id) == int(actor["id"]) and role["code"] != "owner":
        raise InventoryError("You cannot remove your own owner role.")
    location_id = (data.get("location_id") or "").strip() or None
    location = None
    if location_id is not None:
        location = _b().one("SELECT id, code, loc_type FROM locations WHERE id = :i", i=location_id)
        if location is None:
            raise InventoryError("Choose a valid location.")
    if role["code"] == "owner":
        if location is not None:
            raise InventoryError("An owner is not tied to one shop or warehouse. Leave Location blank.")
    else:
        if location is None:
            raise InventoryError("Choose the shop or warehouse this user works in.")
        if role["code"] in SHOP_ROLES and location["loc_type"] != "shop":
            raise InventoryError("A shop role needs a shop location.")
        if role["code"] in WAREHOUSE_ROLES and location["loc_type"] != "warehouse":
            raise InventoryError("A warehouse role needs a warehouse location.")

    if user_id:
        old = get_user(user_id)
        if old is None:
            raise InventoryError("User not found.")
        if int(user_id) == int(actor["id"]) and not is_active:
            raise InventoryError("You cannot deactivate your own account.")
        dup = _b().one("SELECT id FROM users WHERE LOWER(username) = :u AND id <> :i",
                       u=username.lower(), i=user_id)
        if dup:
            raise InventoryError("That username is already used.")
        if password:
            _b().execute(
                "UPDATE users SET username = :u, full_name = :f, email = :e, password_hash = :p, "
                "role_id = :r, location_id = :l, is_active = :a WHERE id = :i",
                u=username, f=full_name, e=email or None, p=generate_password_hash(password),
                r=role["id"], l=location["id"] if location else None, a=is_active, i=user_id)
        else:
            _b().execute(
                "UPDATE users SET username = :u, full_name = :f, email = :e, "
                "role_id = :r, location_id = :l, is_active = :a WHERE id = :i",
                u=username, f=full_name, e=email or None,
                r=role["id"], l=location["id"] if location else None, a=is_active, i=user_id)
        inv.write_audit(actor["id"], "update", "users", user_id,
                        old={"username": old["username"], "role": old["role_code"],
                             "location": old["location_code"], "active": bool(old["is_active"])},
                        new={"username": username, "role": role["code"],
                             "location": location["code"] if location else None,
                             "active": is_active, "password_changed": bool(password)})
        return int(user_id), username, False

    dup = _b().one("SELECT id FROM users WHERE LOWER(username) = :u", u=username.lower())
    if dup:
        raise InventoryError("That username is already used.")
    row = _b().one(
        "INSERT INTO users (username, full_name, email, password_hash, role_id, location_id, is_active) "
        "VALUES (:u, :f, :e, :p, :r, :l, :a) RETURNING id",
        u=username, f=full_name, e=email or None, p=generate_password_hash(password),
        r=role["id"], l=location["id"] if location else None, a=is_active)
    inv.write_audit(actor["id"], "create", "users", row["id"],
                    new={"username": username, "role": role["code"],
                         "location": location["code"] if location else None, "active": is_active})
    return row["id"], username, True


# ---------------------------------------------------------------------
# Locations
# ---------------------------------------------------------------------

def list_locations_page(q="", page=1):
    where, p = " WHERE 1 = 1", {}
    if q.strip():
        where += " AND (LOWER(code) LIKE :q OR LOWER(name) LIKE :q OR LOWER(COALESCE(address, '')) LIKE :q)"
        p["q"] = _like(q)
    frm = "FROM locations" + where
    cols = ("SELECT id, code, name, loc_type, address, is_active, "
            "(SELECT COUNT(*) FROM users u WHERE u.location_id = locations.id AND u.is_active) AS n_users, "
            "(SELECT COALESCE(SUM(b.on_hand), 0) FROM stock_balances b "
            " WHERE b.location_id = locations.id) AS qty_on_hand ")
    return _paged("SELECT COUNT(*) AS n " + frm, cols + frm + " ORDER BY code", page, **p)


def get_location(location_id):
    return _b().one("SELECT * FROM locations WHERE id = :i", i=location_id)


def save_location(actor, location_id, data):
    """Create or update a shop / warehouse. Returns (id, code, created)."""
    code = (data.get("code") or "").strip().upper()
    name = (data.get("name") or "").strip()
    loc_type = (data.get("loc_type") or "").strip().lower()
    address = (data.get("address") or "").strip()
    is_active = bool(data.get("is_active"))
    if not code:
        raise InventoryError("Location code is required.")
    if len(code) > 20:
        raise InventoryError("Location code can be at most 20 characters.")
    if not name:
        raise InventoryError("Location name is required.")
    if len(name) > 100:
        raise InventoryError("Location name can be at most 100 characters.")
    if loc_type not in ("shop", "warehouse"):
        raise InventoryError("Choose Shop or Warehouse.")
    if len(address) > 200:
        raise InventoryError("Address can be at most 200 characters.")

    if location_id:
        old = get_location(location_id)
        if old is None:
            raise InventoryError("Location not found.")
        if old["is_active"] and not is_active:
            users = _b().one("SELECT COUNT(*) AS n FROM users WHERE location_id = :i AND is_active",
                             i=location_id)["n"]
            qty = _b().one("SELECT COALESCE(SUM(on_hand), 0) AS n FROM stock_balances "
                           "WHERE location_id = :i", i=location_id)["n"]
            if users or qty:
                raise InventoryError(
                    "Cannot deactivate: %d active user(s) and %d unit(s) of stock still use this location."
                    % (users, qty))
        if old["loc_type"] != loc_type:
            used = _b().one("SELECT COUNT(*) AS n FROM requisitions WHERE shop_id = :i OR warehouse_id = :i",
                            i=location_id)["n"]
            if used:
                raise InventoryError("This location is already used on requisitions, so its type cannot change.")
        dup = _b().one("SELECT id FROM locations WHERE LOWER(code) = :c AND id <> :i",
                       c=code.lower(), i=location_id)
        if dup:
            raise InventoryError("That location code is already used.")
        _b().execute("UPDATE locations SET code = :c, name = :n, loc_type = :t, address = :a, "
                     "is_active = :act WHERE id = :i",
                     c=code, n=name, t=loc_type, a=address or None, act=is_active, i=location_id)
        inv.write_audit(actor["id"], "update", "locations", location_id,
                        old={"code": old["code"], "name": old["name"], "type": old["loc_type"],
                             "active": bool(old["is_active"])},
                        new={"code": code, "name": name, "type": loc_type, "active": is_active})
        return int(location_id), code, False

    dup = _b().one("SELECT id FROM locations WHERE LOWER(code) = :c", c=code.lower())
    if dup:
        raise InventoryError("That location code is already used.")
    row = _b().one(
        "INSERT INTO locations (code, name, loc_type, address, is_active) "
        "VALUES (:c, :n, :t, :a, :act) RETURNING id",
        c=code, n=name, t=loc_type, a=address or None, act=is_active)
    inv.write_audit(actor["id"], "create", "locations", row["id"],
                    new={"code": code, "name": name, "type": loc_type, "active": is_active})
    return row["id"], code, True
