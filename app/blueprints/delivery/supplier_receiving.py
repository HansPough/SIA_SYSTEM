# Delivery: receiving goods from the supplier (RM) and discrepancy reports
from flask import render_template, request, redirect, url_for, flash
from ...roles import roles_required
from ... import delivery_ops as dop
from ... import inventory_service as inv
from ... import document_ops as docs
from ...inventory_service import InventoryError
from ...session_user import current_user
from . import bp


def _page():
    try:
        return max(1, int(request.args.get("page", 1)))
    except ValueError:
        return 1


def _own_location(user):
    if not user["location_id"]:
        flash("Your account has no location. Ask the owner to set it.", "error")
        return None
    return user["location_id"]


@bp.route("/receiving")
@roles_required("warehouse_staff", "warehouse_manager")
def receiving_list():
    user = current_user()
    loc = _own_location(user)
    if loc is None:
        return redirect(url_for("dashboard.index"))
    res = dop.list_pos(loc, request.args.get("status", ""), request.args.get("q", ""), _page())
    return render_template("delivery/receiving_list.html", res=res, f=request.args)


@bp.route("/receiving/<int:po_id>/check", methods=["GET", "POST"])
@roles_required("warehouse_staff", "warehouse_manager")
def receiving_check(po_id):
    user = current_user()
    loc = _own_location(user)
    if loc is None:
        return redirect(url_for("dashboard.index"))
    po = dop.receiving_form(po_id, loc)
    if po is None:
        flash("That purchase order is not waiting for a delivery at your warehouse.", "error")
        return redirect(url_for("delivery.receiving_list"))
    if request.method == "POST":
        try:
            rm_id, rm_no, dr_no, resolved_drs = dop.receive_goods(
                user, po_id, request.form.get("supplier_dr_no"), request.form.get("received_date"),
                request.form.get("remarks"),
                list(zip(request.form.getlist("po_item_id[]"),
                         request.form.getlist("qty_delivered[]"),
                         request.form.getlist("qty_accepted[]"),
                         request.form.getlist("qty_rejected[]"),
                         request.form.getlist("reject_reason[]"))),
                supplier_invoice_no=request.form.get("supplier_invoice_no"),
                delivered_by=request.form.get("delivered_by"))
            # Staff do not sign. If a manager recorded the receipt, it is signed now;
            # if staff recorded it, the manager signs from the document page.
            if user["role"] == "warehouse_manager":
                docs.sign_on_action(user, "RM", rm_id, "checked_by")
            inv.commit()
        except InventoryError as e:
            inv.rollback()
            flash(str(e), "error")
        else:
            msg = "%s saved. The accepted quantity was added to warehouse stock." % rm_no
            if dr_no:
                msg += " Discrepancy report %s was opened." % dr_no
            if resolved_drs:
                msg += " Discrepancy report(s) %s resolved automatically." % ", ".join(resolved_drs)
            flash(msg, "success")
            return redirect(url_for("delivery.receiving_detail", rm_id=rm_id))
    return render_template("delivery/receiving_check.html", po=po, f=request.form)


@bp.route("/receiving/rm/<int:rm_id>")
@roles_required("warehouse_staff", "warehouse_manager", "owner")
def receiving_detail(rm_id):
    rm = dop.get_receiving(rm_id, current_user())
    if rm is None:
        return render_template("404.html"), 404
    return render_template("delivery/receiving_detail.html", rm=rm)


@bp.route("/discrepancies")
@roles_required("warehouse_manager", "owner")
def discrepancy_list():
    res = dop.list_discrepancies(current_user(), request.args.get("status", ""),
                                 request.args.get("q", ""), _page())
    return render_template("delivery/discrepancy_list.html", res=res, f=request.args)


@bp.route("/discrepancies/<int:report_id>", methods=["GET", "POST"])
@roles_required("warehouse_manager")
def discrepancy_detail(report_id):
    user = current_user()
    report = dop.get_discrepancy(report_id, user)
    if report is None:
        return render_template("404.html"), 404
    if request.method == "POST":
        try:
            no = dop.update_discrepancy(user, report_id, request.form.get("status"),
                                        request.form.get("follow_up_notes"))
            inv.commit()
        except InventoryError as e:
            inv.rollback()
            flash(str(e), "error")
        else:
            flash("Discrepancy report %s updated." % no, "success")
            return redirect(url_for("delivery.discrepancy_list"))
    return render_template("delivery/discrepancy_detail.html", report=report, f=request.form)
