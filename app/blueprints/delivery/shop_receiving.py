# Delivery: the shop receives the delivery (incoming deliveries, AR)
from flask import render_template, request, redirect, url_for, flash
from ...roles import roles_required
from ... import delivery_ops as dop
from ... import inventory_service as inv
from ... import document_ops as docs
from ... import signing
from ...inventory_service import InventoryError
from ...session_user import current_user
from . import bp


def _page():
    try:
        return max(1, int(request.args.get("page", 1)))
    except ValueError:
        return 1


@bp.route("/deliveries")
@roles_required("shop_manager")
def delivery_list():
    user = current_user()
    res = dop.list_incoming(user["location_id"] or 0, request.args.get("status", ""),
                            request.args.get("q", ""), _page())
    return render_template("delivery/delivery_list.html", res=res, f=request.args)


@bp.route("/variances")
@roles_required("shop_manager", "warehouse_manager", "owner")
def variance_list():
    res = dop.list_shop_variances(current_user(), request.args.get("status", ""), _page())
    return render_template("delivery/variance_list.html", res=res, f=request.args)


@bp.route("/deliveries/<int:ts_id>/receive", methods=["GET", "POST"])
@roles_required("shop_manager")
def delivery_receive(ts_id):
    user = current_user()
    ts = dop.receive_form(ts_id, user)
    if ts is None:
        flash("That transfer is not in transit to your shop.", "error")
        return redirect(url_for("delivery.delivery_list"))
    if request.method == "POST":
        try:
            ar_no, has_variance, resolved = dop.receive_delivery(
                user, ts_id, request.form.get("received_date"), request.form.get("remarks"),
                list(zip(request.form.getlist("ts_item_id[]"),
                         request.form.getlist("qty_accepted[]"),
                         request.form.getlist("qty_rejected[]"),
                         request.form.getlist("variance_reason[]"))),
                counted=request.form.getlist("counted[]"))
            # receiving signs the AR and the "Received by" line of the TS
            ar = inv.backend().one("SELECT id FROM acknowledgment_receipts WHERE ts_id = :t", t=ts_id)
            if ar is None:
                raise InventoryError("The acknowledgment receipt could not be created. Nothing was saved.")
            docs.sign_on_action(user, "AR", ar["id"], "received_by")
            docs.sign_on_action(user, "TS", ts_id, "received_by", skip_if_signed=True)
            inv.commit()
        except InventoryError as e:
            inv.rollback()
            flash(str(e), "error")
        else:
            msg = "%s signed. Accepted units added to shop stock." % ar_no
            if has_variance:
                msg += " Rejected units went back to the warehouse; the line is open again."
            if resolved:
                msg += " %d variance(s) resolved." % resolved
            flash(msg, "success")
            return redirect(url_for("delivery.transfer_detail", ts_id=ts_id))
    sigs = signing.signatures_for("TS", ts_id)
    return render_template("delivery/delivery_receive.html", ts=ts, f=request.form,
                           released_sig=sigs.get("released_by"), driver_sig=docs.driver_signature(ts),
                           counted_ids=request.form.getlist("counted[]"),
                           next_no=inv.peek_doc_no("AR"))
