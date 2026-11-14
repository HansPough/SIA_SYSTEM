import base64

from flask import (Blueprint, current_app, flash, redirect, render_template, request,
                   session, url_for, abort)
from werkzeug.security import check_password_hash

from ..roles import ROLE_LABELS, roles_required
from .. import inventory_service as inv
from .. import signing

bp = Blueprint("auth", __name__)

ALL_ROLES = ("owner", "shop_manager", "shop_staff", "warehouse_manager", "warehouse_staff")
SIGNING_ROLES = ("owner", "shop_manager", "warehouse_manager")   # staff never sign
SIGNATURE_PREFIX = signing.DATA_PREFIX
SIGNATURE_MAX_BYTES = 400_000        # size of the base64 text the pad may send


def _check_signature(data):
    """Validate the drawn signature sent by the pad and return a clean PNG data URL."""
    if not data or not data.startswith(SIGNATURE_PREFIX):
        raise ValueError("Draw your signature before saving.")
    if len(data) > SIGNATURE_MAX_BYTES * 2:
        raise ValueError("The signature is too large. Clear it and draw again with a thinner line.")
    try:
        raw = base64.b64decode(data[len(SIGNATURE_PREFIX):], validate=True)
    except Exception:
        raise ValueError("The signature could not be read. Clear it and draw again.")
    return signing.normalize_signature(raw)


def _check_upload(file_storage):
    """Validate a signature picture uploaded from the computer or phone."""
    raw = file_storage.read(signing.SIGNATURE_MAX_UPLOAD + 1)
    if len(raw) > signing.SIGNATURE_MAX_UPLOAD:
        raise ValueError("The picture is larger than 2 MB. Use a smaller one.")
    if not raw:
        raise ValueError("Choose a picture of your signature first.")
    return signing.normalize_signature(raw, uploaded=True)


def _set_session(role, user):
    session.clear()
    session["role"] = role
    session["user_id"] = user["id"] if user else None
    session["user_name"] = user["full_name"] if user else None
    session["location_id"] = user["location_id"] if user else None
    session.permanent = True


def _safe_next(target):
    """Only allow a local path; never redirect to another site."""
    target = (target or "").strip()
    if target.startswith("/") and not target.startswith("//"):
        return target
    return None


@bp.route("/login", methods=["GET", "POST"])
def login():
    if session.get("role"):
        return redirect(url_for("dashboard.index"))
    if request.method == "POST":
        username = (request.form.get("username") or "").strip()
        password = request.form.get("password") or ""
        if not username or not password:
            flash("Enter your username and password.", "error")
            return render_template("login.html", username=username, next=request.args.get("next", ""))
        try:
            user = inv.backend().one(
                "SELECT u.id, u.username, u.full_name, u.location_id, u.password_hash, r.code AS role "
                "FROM users u JOIN roles r ON r.id = u.role_id "
                "WHERE LOWER(u.username) = :u AND u.is_active", u=username.lower())
        except Exception as e:          # database not reachable / not created yet
            current_app.logger.warning("Login query failed: %s", e)
            flash("The database is not available yet. Ask the administrator to load it.", "error")
            return render_template("login.html", username=username, next=request.args.get("next", ""))
        if user is None or not user["password_hash"] or not check_password_hash(user["password_hash"], password):
            flash("Invalid username or password.", "error")
            return render_template("login.html", username=username, next=request.args.get("next", ""))
        _set_session(user["role"], user)
        inv.write_audit(user["id"], "login", "users", user["id"],
                        new={"username": user["username"], "role": user["role"]})
        inv.commit()
        return redirect(_safe_next(request.args.get("next")) or url_for("dashboard.index"))
    return render_template("login.html", username="", next=request.args.get("next", ""))


@bp.route("/logout")
def logout():
    user_id = session.get("user_id")
    if user_id:
        try:
            inv.write_audit(user_id, "logout", "users", user_id)
            inv.commit()
        except Exception:
            inv.rollback()
    session.clear()
    return redirect(url_for("auth.login"))


@bp.route("/dev/role/<role>")
def dev_role(role):
    """TEMPORARY: preview a role. Turn DEV_LOGIN off in production."""
    if not current_app.config.get("DEV_LOGIN"):
        abort(404)
    if role not in ROLE_LABELS:
        abort(404)
    try:
        user = inv.backend().one(
            "SELECT u.id, u.full_name, u.location_id FROM users u JOIN roles r ON r.id = u.role_id "
            "WHERE r.code = :c AND u.is_active ORDER BY u.id LIMIT 1", c=role)
    except Exception:          # no database yet: still let the screens be previewed
        user = None
    _set_session(role, user)
    if user is None:
        flash("No user with this role in the database yet (run database/seed_users.sql). "
              "Screens that save data will not work.", "info")
    return redirect(url_for("dashboard.index"))


@bp.route("/dev/user/<int:user_id>")
def dev_user(user_id):
    """TEMPORARY: log in as one specific user. Turn DEV_LOGIN off in production."""
    if not current_app.config.get("DEV_LOGIN"):
        abort(404)
    user = inv.backend().one(
        "SELECT u.id, u.full_name, u.location_id, r.code AS role FROM users u JOIN roles r ON r.id = u.role_id "
        "WHERE u.id = :i AND u.is_active", i=user_id)
    if user is None:
        abort(404)
    _set_session(user["role"], user)
    return redirect(url_for("dashboard.index"))


@bp.route("/signature", methods=["GET", "POST"])
@roles_required(*SIGNING_ROLES)
def signature():
    """Let the signed-in user draw and save the signature used on documents."""
    user_id = session.get("user_id")
    if not user_id:
        return redirect(url_for("auth.login"))
    if request.method == "POST":
        action = (request.form.get("action") or "save").strip()
        try:
            if action == "remove":
                inv.backend().execute("UPDATE users SET signature_data = NULL WHERE id = :i", i=user_id)
                inv.write_audit(user_id, "update", "users", user_id, new={"signature": "removed"})
                flash("Saved signature removed.", "success")
            else:
                upload = request.files.get("signature_file")
                if upload and upload.filename:
                    data = _check_upload(upload)
                else:
                    data = _check_signature((request.form.get("signature_data") or "").strip())
                inv.backend().execute("UPDATE users SET signature_data = :d WHERE id = :i",
                                      d=data, i=user_id)
                inv.write_audit(user_id, "update", "users", user_id, new={"signature": "set"})
                flash("Signature saved. It is stamped on every document you sign from now on.", "success")
            inv.commit()
        except ValueError as e:
            inv.rollback()
            flash(str(e), "error")
        except Exception as e:
            inv.rollback()
            current_app.logger.exception("Saving signature failed")
            flash("The signature could not be saved: %s" % e, "error")
        return redirect(url_for("auth.signature"))
    row = inv.backend().one("SELECT full_name, signature_data FROM users WHERE id = :i", i=user_id)
    if row is None:
        session.clear()
        return redirect(url_for("auth.login"))
    return render_template("account/signature.html", user=row)
