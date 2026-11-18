# Delivery: transfer slips (prepare, assign, load, release), drivers and vehicles
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


@bp.route("/transfers")
@roles_required("warehouse_manager", "warehouse_staff", "owner")
def transfer_list():
    res = dop.list_transfers(current_user(), request.args.get("status", ""),
                             request.args.get("q", ""), _page())
    return render_template("delivery/transfer_list.html", res=res, f=request.args)


@bp.route("/transfers/new", methods=["GET", "POST"])
@roles_required("warehouse_manager")
def transfer_new():
    user = current_user()
    loc = user["location_id"]
    if not loc:
        flash("Your account has no location. Ask the owner to set it.", "error")
        return redirect(url_for("dashboard.index"))
    if request.method == "POST":
        try:
            ts_id, ts_no = dop.create_transfer(user, request.form.get("rf_id"))
            assigned = bool(request.form.get("driver_id") or request.form.get("vehicle_id"))
            if assigned:        # one step: prepare and assign the driver and vehicle together
                dop.assign_transfer(user, ts_id, request.form.get("driver_id"),
                                    request.form.get("vehicle_id"), request.form.get("remarks"))
            inv.commit()
        except InventoryError as e:
            inv.rollback()
            flash(str(e), "error")
        else:
            flash("%s prepared%s." % (ts_no, " and assigned." if assigned
                                      else " as draft. Assign a driver next"), "success")
            return redirect(url_for("delivery.transfer_detail", ts_id=ts_id))
    candidates = dop.prepare_list(loc)
    selected_rf = request.values.get("rf_id", "")
    if selected_rf:
        candidates = [r for r in candidates if str(r["id"]) == selected_rf]
    selected = candidates[0] if selected_rf and candidates else None
    return render_template("delivery/transfer_new.html", candidates=candidates, selected_rf=selected_rf,
                           selected=selected, reserved_lines=dop.reserved_transfer_lines(selected["id"]) if selected else [],
                           f=request.form, drivers=[d for d in dop.list_drivers() if d["is_active"]],
                           vehicles=[v for v in dop.list_vehicles() if v["is_active"]],
                           next_no=inv.peek_doc_no("TS"))


@bp.route("/transfers/<int:ts_id>")
@roles_required("warehouse_manager", "warehouse_staff", "shop_manager", "owner")
def transfer_detail(ts_id):
    ts = dop.get_transfer(ts_id, current_user())
    if ts is None:
        return render_template("404.html"), 404
    return render_template("delivery/transfer_detail.html", ts=ts, driver_sig=docs.driver_signature(ts),
                           d=docs.load("TS", ts_id, current_user()))


@bp.route("/transfers/<int:ts_id>/driver-sign", methods=["GET", "POST"])
@roles_required("warehouse_staff", "warehouse_manager")
def transfer_driver_sign(ts_id):
    """After loading, the driver signs on the warehouse screen (staff or manager holds the device)."""
    user = current_user()
    ts = dop.get_transfer(ts_id, user)
    if ts is None:
        return render_template("404.html"), 404
    if request.method == "POST":
        try:
            name = docs.capture_driver_signature(user, ts_id, request.form.get("signature_data"))
            inv.commit()
        except InventoryError as e:
            inv.rollback()
            flash(str(e), "error")
        else:
            flash("Signature of %s saved." % name, "success")
            return redirect(url_for("delivery.transfer_detail", ts_id=ts_id))
    elif ts["status"] != "loaded" and docs.driver_signature(ts) is None:
        flash("The driver signs after loading, before release.", "error")
        return redirect(url_for("delivery.transfer_detail", ts_id=ts_id))
    return render_template("delivery/transfer_driver_sign.html", ts=ts, driver_sig=docs.driver_signature(ts))


@bp.route("/transfers/<int:ts_id>/cancel", methods=["POST"])
@roles_required("warehouse_manager")
def transfer_cancel(ts_id):
    try:
        ts_no = dop.cancel_transfer(current_user(), ts_id)
        inv.commit()
    except InventoryError as e:
        inv.rollback()
        flash(str(e), "error")
    else:
        flash("%s cancelled. Stock stays reserved." % ts_no, "success")
    return redirect(url_for("delivery.transfer_detail", ts_id=ts_id))


@bp.route("/transfers/<int:ts_id>/assign", methods=["GET", "POST"])
@roles_required("warehouse_manager")
def transfer_assign(ts_id):
    user = current_user()
    ts = dop.get_transfer(ts_id, user)
    if ts is None:
        return render_template("404.html"), 404
    if ts["status"] != "draft":
        flash("Only a draft TS can be assigned.", "error")
        return redirect(url_for("delivery.transfer_detail", ts_id=ts_id))
    if request.method == "POST":
        try:
            ts_no = dop.assign_transfer(user, ts_id, request.form.get("driver_id"),
                                        request.form.get("vehicle_id"), request.form.get("remarks"))
            inv.commit()
        except InventoryError as e:
            inv.rollback()
            flash(str(e), "error")
        else:
            flash("%s assigned." % ts_no, "success")
            return redirect(url_for("delivery.transfer_detail", ts_id=ts_id))
    return render_template("delivery/transfer_assign.html", ts=ts,
                           drivers=dop.list_drivers(), vehicles=dop.list_vehicles(), f=request.form)


@bp.route("/transfers/<int:ts_id>/load", methods=["GET", "POST"])
@roles_required("warehouse_staff")
def transfer_load(ts_id):
    ts = dop.get_transfer(ts_id, current_user())
    if ts is None:
        return render_template("404.html"), 404
    if ts["status"] != "assigned":
        flash("Assign a driver and vehicle first.", "error")
        return redirect(url_for("delivery.transfer_detail", ts_id=ts_id))
    if request.method == "POST":
        try:
            ts_no = dop.load_transfer(current_user(), ts_id, request.form.getlist("loaded[]"))
            inv.commit()
        except InventoryError as e:
            inv.rollback()
            flash(str(e), "error")
        else:
            flash("%s loaded. Hand the device to the driver." % ts_no, "success")
            return redirect(url_for("delivery.transfer_driver_sign", ts_id=ts_id))
    return render_template("delivery/transfer_load.html", ts=ts,
                           d=docs.load("TS", ts_id, current_user()))


@bp.route("/transfers/<int:ts_id>/release", methods=["GET", "POST"])
@roles_required("warehouse_manager")
def transfer_release(ts_id):
    user = current_user()
    ts = dop.get_transfer(ts_id, user)
    if ts is None:
        return render_template("404.html"), 404
    if ts["status"] != "loaded":
        flash("Only a loaded TS can be released.", "error")
        return redirect(url_for("delivery.transfer_detail", ts_id=ts_id))
    if request.method == "POST":
        try:
            docs.require_driver_clearance(user, ts, request.form.get("paper_reason"))
            ts_no = dop.release_transfer(user, ts_id)
            docs.sign_on_action(user, "TS", ts_id, "released_by")   # releasing signs the TS
            inv.commit()
        except InventoryError as e:
            inv.rollback()
            flash(str(e), "error")
        else:
            flash("%s signed and released. Stock is in transit." % ts_no, "success")
            return redirect(url_for("delivery.transfer_detail", ts_id=ts_id))
    return render_template("delivery/transfer_release.html", ts=ts, driver_sig=docs.driver_signature(ts),
                           paper_reason=request.form.get("paper_reason", ""), d=docs.load("TS", ts_id, user))


# ---------------------------------------------------------------------
# Drivers and vehicles
# ---------------------------------------------------------------------

@bp.route("/drivers")
@roles_required("warehouse_manager")
def driver_list():
    return render_template("delivery/driver_list.html", rows=dop.list_drivers(request.args.get("q", "")),
                           f=request.args)


@bp.route("/drivers/new", methods=["GET", "POST"])
@roles_required("warehouse_manager")
def driver_new():
    if request.method == "POST":
        try:
            _id, name = dop.save_driver(current_user(), None, request.form)
            inv.commit()
        except InventoryError as e:
            inv.rollback()
            flash(str(e), "error")
        else:
            flash("Driver %s saved." % name, "success")
            return redirect(url_for("delivery.driver_list"))
    return render_template("delivery/driver_new.html", f=request.form)


@bp.route("/drivers/<int:driver_id>/edit", methods=["GET", "POST"])
@roles_required("warehouse_manager")
def driver_edit(driver_id):
    driver = dop.get_driver(driver_id)
    if driver is None:
        return render_template("404.html"), 404
    if request.method == "POST":
        try:
            _id, name = dop.save_driver(current_user(), driver_id, request.form)
            inv.commit()
        except InventoryError as e:
            inv.rollback()
            flash(str(e), "error")
        else:
            flash("Driver %s saved." % name, "success")
            return redirect(url_for("delivery.driver_list"))
    return render_template("delivery/driver_edit.html", driver=driver, f=request.form)


@bp.route("/vehicles")
@roles_required("warehouse_manager")
def vehicle_list():
    return render_template("delivery/vehicle_list.html",
                           rows=dop.list_vehicles(request.args.get("q", "")), f=request.args)


@bp.route("/vehicles/new", methods=["GET", "POST"])
@roles_required("warehouse_manager")
def vehicle_new():
    if request.method == "POST":
        try:
            _id, plate = dop.save_vehicle(current_user(), None, request.form)
            inv.commit()
        except InventoryError as e:
            inv.rollback()
            flash(str(e), "error")
        else:
            flash("Vehicle %s saved." % plate, "success")
            return redirect(url_for("delivery.vehicle_list"))
    return render_template("delivery/vehicle_new.html", f=request.form)


@bp.route("/vehicles/<int:vehicle_id>/edit", methods=["GET", "POST"])
@roles_required("warehouse_manager")
def vehicle_edit(vehicle_id):
    vehicle = dop.get_vehicle(vehicle_id)
    if vehicle is None:
        return render_template("404.html"), 404
    if request.method == "POST":
        try:
            _id, plate = dop.save_vehicle(current_user(), vehicle_id, request.form)
            inv.commit()
        except InventoryError as e:
            inv.rollback()
            flash(str(e), "error")
        else:
            flash("Vehicle %s saved." % plate, "success")
            return redirect(url_for("delivery.vehicle_list"))
    return render_template("delivery/vehicle_edit.html", vehicle=vehicle, f=request.form)
