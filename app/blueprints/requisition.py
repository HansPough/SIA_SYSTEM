from itertools import zip_longest

from flask import Blueprint, render_template, request, redirect, url_for, flash
from ..roles import roles_required
from .. import inventory_service as inv
from .. import document_ops as docs
from .. import requisition_ops as rq
from .. import inventory_ops as inventory_ops
from ..inventory_service import InventoryError
from ..session_user import current_user

bp = Blueprint("requisition", __name__, url_prefix="/requisition")


def _page():
    try:
        return max(1, int(request.args.get("page", 1)))
    except ValueError:
        return 1


def _own_location(user):
    """The shop a shop user works in, or None (with a message) if the account has none."""
    if not user["location_id"]:
        flash("Your account has no location. Ask the owner to set it.", "error")
        return None
    return user["location_id"]


def _lines(*names):
    """Table rows from the form, one tuple per row. A missing column becomes ''."""
    return list(zip_longest(*[request.form.getlist(n) for n in names], fillvalue=""))


def _alert_from_request(shop_id):
    """The alert_id in the query string or form, if it belongs to this shop."""
    try:
        alert_id = int(request.values.get("alert_id", ""))
    except ValueError:
        return None
    return rq.get_alert(alert_id, shop_id)


# ---------------------------------------------------------------------
# Low-stock alerts (shop manager)
# ---------------------------------------------------------------------

@bp.route("/alerts")
@roles_required("shop_manager")
def alert_list():
    user = current_user()
    loc = _own_location(user)
    if loc is None:
        return redirect(url_for("dashboard.index"))
    res = rq.list_alerts(loc, request.args.get("status", ""), request.args.get("q", ""), _page())
    return render_template("requisition/alert_list.html", res=res, f=request.args)


@bp.route("/alerts/<int:alert_id>")
@roles_required("shop_manager")
def alert_detail(alert_id):
    loc = current_user()["location_id"]
    alert = rq.get_alert(alert_id, loc) if loc else None
    if alert is None:
        return render_template("404.html"), 404
    return render_template("requisition/alert_detail.html", alert=alert)


@bp.route("/alerts/<int:alert_id>/snooze", methods=["POST"])
@roles_required("shop_manager")
def alert_snooze(alert_id):
    """Silent for a few days. The alert stays open and counts again by itself."""
    user = current_user()
    try:
        no, until = rq.snooze_alert(user, alert_id, request.form.get("days"))
        inv.commit()
    except InventoryError as e:
        inv.rollback()
        flash(str(e), "error")
    else:
        flash("%s is silent until %s. It stays open and counts again then." % (no, until.strftime("%d %b %Y")), "success")
    return redirect(url_for("requisition.alert_list"))


@bp.route("/alerts/<int:alert_id>/wake", methods=["POST"])
@roles_required("shop_manager")
def alert_wake(alert_id):
    """Make a snoozed alert count again right now."""
    user = current_user()
    try:
        no = rq.wake_alert(user, alert_id)
        inv.commit()
    except InventoryError as e:
        inv.rollback()
        flash(str(e), "error")
    else:
        flash("%s is active again." % no, "success")
    return redirect(url_for("requisition.alert_detail", alert_id=alert_id))


@bp.route("/alerts/<int:alert_id>/dismiss", methods=["GET", "POST"])
@roles_required("shop_manager")
def alert_dismiss(alert_id):
    user = current_user()
    loc = _own_location(user)
    if loc is None:
        return redirect(url_for("dashboard.index"))
    alert = rq.get_alert(alert_id, loc)
    if alert is None:
        return render_template("404.html"), 404
    if alert["status"] != "active":
        flash("This alert is already %s." % alert["status"], "error")
        return redirect(url_for("requisition.alert_detail", alert_id=alert_id))
    if request.method == "POST":
        try:
            no = rq.dismiss_alert(user, alert_id, request.form.get("dismissed_reason"))
            inv.commit()
        except InventoryError as e:
            inv.rollback()
            flash(str(e), "error")
        else:
            flash("Alert %s was dismissed." % no, "success")
            return redirect(url_for("requisition.alert_list"))
    return render_template("requisition/alert_dismiss.html", alert=alert, f=request.form)


# ---------------------------------------------------------------------
# Requisition forms (RF)
# ---------------------------------------------------------------------

@bp.route("/rf")
@roles_required("shop_manager", "warehouse_manager", "owner")
def rf_list():
    res = rq.list_rfs(current_user(), request.args.get("status", ""), request.args.get("q", ""), _page())
    return render_template("requisition/rf_list.html", res=res, f=request.args)


@bp.route("/rf/new", methods=["GET", "POST"])
@roles_required("shop_manager")
def rf_new():
    user = current_user()
    loc = _own_location(user)
    if loc is None:
        return redirect(url_for("requisition.rf_list"))

    alert = None
    if request.values.get("alert_id"):
        alert = _alert_from_request(loc)
        if alert is None:
            flash("That alert was not found for your shop.", "error")
            return redirect(url_for("requisition.alert_list"))

    if request.method == "POST":
        try:
            rf_id, rf_no, status = rq.create_rf(
                user, loc, request.form.get("warehouse_id"), request.form.get("remarks"),
                _lines("item_id[]", "qty_requested[]"),
                request.form.get("action") == "submit_rf",
                alert_id=alert["id"] if alert else None)
            if status == "open":      # sending the form signs it, in the same save
                docs.sign_on_action(user, "RF", rf_id, "requested_by")
                inventory_ops.register_rf_shortages(rf_id)      # what the warehouse cannot cover is flagged now
            inv.commit()
        except InventoryError as e:
            inv.rollback()
            flash(str(e), "error")
        else:
            if status == "open":
                flash("%s signed and sent to the warehouse." % rf_no, "success")
            else:
                flash("%s saved as a draft. Submit it when you are ready." % rf_no, "success")
            return redirect(url_for("requisition.rf_detail", rf_id=rf_id))

    try:
        data = rq.rf_form_data(loc)
    except InventoryError as e:
        flash(str(e), "error")
        return redirect(url_for("requisition.rf_list"))
    return render_template("requisition/rf_new.html", data=data, alert=alert, f=request.form,
                           requested_by=user["name"])


@bp.route("/rf/<int:rf_id>", methods=["GET", "POST"])
@roles_required("shop_manager", "warehouse_manager", "owner")
def rf_detail(rf_id):
    user = current_user()
    rf = rq.get_rf(rf_id, user)
    if rf is None:
        return render_template("404.html"), 404
    if request.method == "POST":
        if user["role"] == "shop_manager" and request.form.get("action") == "submit_rf":
            try:
                rf_no = rq.submit_rf(user, rf_id)
                docs.sign_on_action(user, "RF", rf_id, "requested_by")
                inventory_ops.register_rf_shortages(rf_id)      # what the warehouse cannot cover is flagged now
                inv.commit()
            except InventoryError as e:
                inv.rollback()
                flash(str(e), "error")
            else:
                flash("%s signed and sent to the warehouse." % rf_no, "success")
        return redirect(url_for("requisition.rf_detail", rf_id=rf_id))
    can_cancel, _why = rq.can_cancel(user, rf)
    queue_rf = inventory_ops.get_rf(rf_id, user["location_id"] or 0) if user["role"] == "warehouse_manager" else None
    return render_template("requisition/rf_detail.html", rf=rf, can_cancel=can_cancel,
                           d=docs.load("RF", rf_id, user), queue_rf=queue_rf)


@bp.route("/rf/<int:rf_id>/cancel", methods=["GET", "POST"])
@roles_required("shop_manager")
def rf_cancel(rf_id):
    user = current_user()
    rf = rq.get_rf(rf_id, user)
    if rf is None:
        return render_template("404.html"), 404
    ok, why = rq.can_cancel(user, rf)
    if not ok:
        flash(why, "error")
        return redirect(url_for("requisition.rf_detail", rf_id=rf_id))
    if request.method == "POST":
        try:
            rf_no, released = rq.cancel_rf(user, rf_id, request.form.get("cancel_reason"))
            inv.commit()
        except InventoryError as e:
            inv.rollback()
            flash(str(e), "error")
        else:
            msg = "%s was cancelled." % rf_no
            if released:
                msg += " %d reservation(s) were released back to the warehouse." % released
            flash(msg, "success")
            return redirect(url_for("requisition.rf_list"))
    return render_template("requisition/rf_cancel.html", rf=rf, f=request.form)
