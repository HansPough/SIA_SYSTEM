# Admin: users and locations
from flask import render_template, request, redirect, url_for, flash, abort
from ...roles import roles_required
from ... import admin_ops as ops
from ... import inventory_service as inv
from ...inventory_service import InventoryError
from ...session_user import current_user
from . import bp


def _page():
    try:
        return max(1, int(request.args.get("page", 1)))
    except ValueError:
        return 1


# ---------------------------------------------------------------------
# Users
# ---------------------------------------------------------------------

@bp.route("/users")
@roles_required("owner")
def user_list():
    res = ops.list_users(request.args.get("q", ""), _page())
    return render_template("admin/user_list.html", res=res, f=request.args)


def _user_form_context(**extra):
    return dict(roles=ops.list_roles(), locations=ops.list_locations(active_only=True), **extra)


@bp.route("/users/new", methods=["GET", "POST"])
@roles_required("owner")
def user_new():
    if request.method == "POST":
        try:
            _uid, username, _created = ops.save_user(current_user(), None, request.form)
            inv.commit()
        except InventoryError as e:
            inv.rollback()
            flash(str(e), "error")
        else:
            flash("User %s saved." % username, "success")
            return redirect(url_for("admin.user_list"))
    return render_template("admin/user_new.html", f=request.form, **_user_form_context())


@bp.route("/users/<int:user_id>/edit", methods=["GET", "POST"])
@roles_required("owner")
def user_edit(user_id):
    user = ops.get_user(user_id)
    if user is None:
        return render_template("404.html"), 404
    if request.method == "POST":
        try:
            _uid, username, _created = ops.save_user(current_user(), user_id, request.form)
            inv.commit()
        except InventoryError as e:
            inv.rollback()
            flash(str(e), "error")
        else:
            flash("User %s saved." % username, "success")
            return redirect(url_for("admin.user_list"))
        user = ops.get_user(user_id)
    return render_template("admin/user_edit.html", user=user, f=request.form, **_user_form_context())


# ---------------------------------------------------------------------
# Locations
# ---------------------------------------------------------------------

@bp.route("/locations")
@roles_required("owner")
def location_list():
    res = ops.list_locations_page(request.args.get("q", ""), _page())
    return render_template("admin/location_list.html", res=res, f=request.args)


@bp.route("/locations/new", methods=["GET", "POST"])
@roles_required("owner")
def location_new():
    if request.method == "POST":
        try:
            _lid, code, _created = ops.save_location(current_user(), None, request.form)
            inv.commit()
        except InventoryError as e:
            inv.rollback()
            flash(str(e), "error")
        else:
            flash("Location %s saved." % code, "success")
            return redirect(url_for("admin.location_list"))
    return render_template("admin/location_new.html", f=request.form)


@bp.route("/locations/<int:location_id>/edit", methods=["GET", "POST"])
@roles_required("owner")
def location_edit(location_id):
    location = ops.get_location(location_id)
    if location is None:
        return render_template("404.html"), 404
    if request.method == "POST":
        try:
            _lid, code, _created = ops.save_location(current_user(), location_id, request.form)
            inv.commit()
        except InventoryError as e:
            inv.rollback()
            flash(str(e), "error")
        else:
            flash("Location %s saved." % code, "success")
            return redirect(url_for("admin.location_list"))
        location = ops.get_location(location_id)
    return render_template("admin/location_edit.html", location=location, f=request.form)
