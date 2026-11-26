from itertools import zip_longest

from flask import Blueprint, render_template, request, redirect, url_for, flash
from ..roles import roles_required
from .. import inventory_service as inv
from .. import document_ops as docs
from .. import purchasing_ops as pur
from ..inventory_service import InventoryError
from ..session_user import current_user

bp = Blueprint("purchasing", __name__, url_prefix="/purchasing")


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


def _lines(*names):
    return list(zip_longest(*[request.form.getlist(n) for n in names], fillvalue=""))


def _alert_from_request(warehouse_id):
    try:
        alert_id = int(request.values.get("alert_id", ""))
    except ValueError:
        return None
    return pur.get_alert(alert_id, warehouse_id)


# ---------------------------------------------------------------------
# Restock alerts (warehouse manager)
# ---------------------------------------------------------------------

@bp.route("/alerts")
@roles_required("warehouse_manager")
def alert_list():
    user = current_user()
    loc = _own_location(user)
    if loc is None:
        return redirect(url_for("dashboard.index"))
    res = pur.list_alerts(loc, request.args.get("status", ""), request.args.get("q", ""), _page())
    return render_template("purchasing/alert_list.html", res=res, f=request.args)


@bp.route("/alerts/<int:alert_id>")
@roles_required("warehouse_manager")
def alert_detail(alert_id):
    loc = current_user()["location_id"]
    alert = pur.get_alert(alert_id, loc) if loc else None
    if alert is None:
        return render_template("404.html"), 404
    return render_template("purchasing/alert_detail.html", alert=alert,
                           existing_ref=pur.ref_for_alert(alert_id))


@bp.route("/alerts/<int:alert_id>/snooze", methods=["POST"])
@roles_required("warehouse_manager")
def alert_snooze(alert_id):
    """Silent for a few days. The alert stays open and counts again by itself."""
    user = current_user()
    try:
        no, until = pur.snooze_alert(user, alert_id, request.form.get("days"))
        inv.commit()
    except InventoryError as e:
        inv.rollback()
        flash(str(e), "error")
    else:
        flash("%s is silent until %s." % (no, until.strftime("%d %b %Y")), "success")
    return redirect(url_for("purchasing.alert_list"))


@bp.route("/alerts/<int:alert_id>/wake", methods=["POST"])
@roles_required("warehouse_manager")
def alert_wake(alert_id):
    """Make a snoozed alert count again right now."""
    user = current_user()
    try:
        no = pur.wake_alert(user, alert_id)
        inv.commit()
    except InventoryError as e:
        inv.rollback()
        flash(str(e), "error")
    else:
        flash("%s is active again." % no, "success")
    return redirect(url_for("purchasing.alert_detail", alert_id=alert_id))


@bp.route("/alerts/<int:alert_id>/dismiss", methods=["GET", "POST"])
@roles_required("warehouse_manager")
def alert_dismiss(alert_id):
    user = current_user()
    loc = _own_location(user)
    if loc is None:
        return redirect(url_for("dashboard.index"))
    alert = pur.get_alert(alert_id, loc)
    if alert is None:
        return render_template("404.html"), 404
    if alert["status"] != "active":
        flash("This alert is already %s." % alert["status"], "error")
        return redirect(url_for("purchasing.alert_detail", alert_id=alert_id))
    if request.method == "POST":
        try:
            no = pur.dismiss_alert(user, alert_id, request.form.get("dismissed_reason"))
            inv.commit()
        except InventoryError as e:
            inv.rollback()
            flash(str(e), "error")
        else:
            flash("Alert %s was dismissed." % no, "success")
            return redirect(url_for("purchasing.alert_list"))
    return render_template("purchasing/alert_dismiss.html", alert=alert, f=request.form)


# ---------------------------------------------------------------------
# REF (replenishment form)
# ---------------------------------------------------------------------

@bp.route("/ref")
@roles_required("warehouse_manager", "owner")
def ref_list():
    res = pur.list_refs(current_user(), request.args.get("status", ""), request.args.get("q", ""), _page())
    return render_template("purchasing/ref_list.html", res=res, f=request.args)


@bp.route("/ref/new", methods=["GET", "POST"])
@roles_required("warehouse_manager")
def ref_new():
    user = current_user()
    loc = _own_location(user)
    if loc is None:
        return redirect(url_for("purchasing.ref_list"))

    alert = None
    if request.values.get("alert_id"):
        alert = _alert_from_request(loc)
        if alert is None:
            flash("That restock alert was not found for your warehouse.", "error")
            return redirect(url_for("purchasing.alert_list"))
        existing = pur.ref_for_alert(alert["id"])
        if existing:
            flash("That alert already has %s." % existing["ref_no"], "info")
            return redirect(url_for("purchasing.ref_detail", ref_id=existing["id"]))

    if request.method == "POST":
        try:
            ref_id, ref_no, status = pur.create_ref(
                user, loc, request.form.get("remarks"),
                _lines("item_id[]", "qty_requested[]", "reason[]"),
                request.form.get("action") == "submit_ref",
                alert_id=alert["id"] if alert else None)
            if status == "pending_owner":      # sending the form signs it, in the same save
                docs.sign_on_action(user, "REF", ref_id, "prepared_by")
            inv.commit()
        except InventoryError as e:
            inv.rollback()
            flash(str(e), "error")
        else:
            if status == "pending_owner":
                flash("%s signed and sent to the owner for approval.%s" % (ref_no, " Alert completed."
                                                                          if alert else ""), "success")
            else:
                flash("%s saved as draft." % ref_no, "success")
            return redirect(url_for("purchasing.ref_detail", ref_id=ref_id))

    try:
        data = pur.ref_form_data(loc)
    except InventoryError as e:
        flash(str(e), "error")
        return redirect(url_for("purchasing.ref_list"))
    return render_template("purchasing/ref_new.html", data=data, alert=alert, f=request.form,
                           created_by=user["name"], next_no=inv.peek_doc_no("REF"))


@bp.route("/ref/<int:ref_id>", methods=["GET", "POST"])
@roles_required("warehouse_manager", "owner")
def ref_detail(ref_id):
    user = current_user()
    ref = pur.get_ref(ref_id, user)
    if ref is None:
        return render_template("404.html"), 404
    if request.method == "POST":
        if user["role"] == "warehouse_manager" and request.form.get("action") == "submit_ref":
            try:
                ref_no = pur.submit_ref(user, ref_id)
                docs.sign_on_action(user, "REF", ref_id, "prepared_by")
                inv.commit()
            except InventoryError as e:
                inv.rollback()
                flash(str(e), "error")
            else:
                flash("%s signed and sent to the owner for approval." % ref_no, "success")
        elif user["role"] == "owner" and request.form.get("action") in ("approve_ref", "reject_ref"):
            try:
                if request.form["action"] == "approve_ref":
                    ref_no = pur.approve_ref(user, ref_id, request.form.get("remarks"))
                    docs.sign_on_action(user, "REF", ref_id, "approved_by", skip_if_signed=True)
                    msg = "%s approved and signed. The warehouse manager can now send the RFQ." % ref_no
                else:
                    ref_no = pur.reject_ref(user, ref_id, request.form.get("remarks"))
                    msg = "%s rejected." % ref_no
                inv.commit()
            except InventoryError as e:
                inv.rollback()
                flash(str(e), "error")
            else:
                flash(msg, "success")
                return redirect(url_for("purchasing.approval_list"))
        return redirect(url_for("purchasing.ref_detail", ref_id=ref_id))
    can_cancel, _why = pur.can_cancel(user, ref)
    return render_template("purchasing/ref_detail.html", ref=ref, can_cancel=can_cancel,
                           flow=pur.ref_flow(ref, user["role"]))


@bp.route("/ref/<int:ref_id>/cancel", methods=["GET", "POST"])
@roles_required("warehouse_manager")
def ref_cancel(ref_id):
    user = current_user()
    ref = pur.get_ref(ref_id, user)
    if ref is None:
        return render_template("404.html"), 404
    ok, why = pur.can_cancel(user, ref)
    if not ok:
        flash(why, "error")
        return redirect(url_for("purchasing.ref_detail", ref_id=ref_id))
    if request.method == "POST":
        try:
            ref_no = pur.cancel_ref(user, ref_id, request.form.get("cancel_reason"))
            inv.commit()
        except InventoryError as e:
            inv.rollback()
            flash(str(e), "error")
        else:
            flash("%s was cancelled." % ref_no, "success")
            return redirect(url_for("purchasing.ref_list"))
    return render_template("purchasing/ref_cancel.html", ref=ref, f=request.form)


# ---------------------------------------------------------------------
# RFQ, quotations and choosing a supplier
# ---------------------------------------------------------------------

@bp.route("/ref/<int:ref_id>/rfq/new", methods=["GET", "POST"])
@roles_required("warehouse_manager")
def rfq_new(ref_id):
    user = current_user()
    try:
        data = pur.rfq_form_data(ref_id, user)
    except InventoryError as e:
        flash(str(e), "error")
        return redirect(url_for("purchasing.ref_list"))
    if request.method == "POST":
        try:
            rfq_no, n, rfq_id = pur.create_rfq(user, ref_id, request.form.getlist("supplier_ids[]"),
                                               request.form.get("reply_deadline"), request.form.get("terms"))
            docs.sign_on_action(user, "RFQ", rfq_id, "prepared_by")     # signed in the same save
            inv.commit()
        except InventoryError as e:
            inv.rollback()
            flash(str(e), "error")
        else:
            flash("%s signed and created for %d supplier%s. Download or print it to send."
                  % (rfq_no, n, "" if n == 1 else "s"), "success")
            return redirect(url_for("purchasing.rfq_detail", ref_id=ref_id, rfq_id=rfq_id))
    return render_template("purchasing/rfq_new.html", data=data, f=request.form,
                           next_no=inv.peek_doc_no("RFQ"))


@bp.route("/ref/<int:ref_id>/rfq/<int:rfq_id>")
@roles_required("warehouse_manager", "owner")
def rfq_detail(ref_id, rfq_id):
    """The RFQ document first; below it who was asked, who replied and what to do next."""
    user = current_user()
    rfq = pur.get_rfq(rfq_id, user)
    if rfq is None or rfq["ref_id"] != ref_id:
        return render_template("404.html"), 404
    return render_template("purchasing/rfq_detail.html", rfq=rfq,
                           supplier_id=request.args.get("supplier", type=int))


@bp.route("/ref/<int:ref_id>/quotation/new", methods=["GET", "POST"])
@roles_required("warehouse_manager")
def quotation_new(ref_id):
    user = current_user()
    try:
        data = pur.quotation_form_data(ref_id, user, request.values.get("supplier_id"))
    except InventoryError as e:
        flash(str(e), "error")
        return redirect(url_for("purchasing.ref_list"))
    if not data["rfq"]:
        flash("This REF has no RFQ yet. Create the RFQ first.", "error")
        return redirect(url_for("purchasing.ref_detail", ref_id=ref_id))
    if request.method == "POST":
        try:
            _qid, supplier_name = pur.save_quotation(
                user, ref_id, request.form.get("supplier_id"), request.form.get("quotation_ref"),
                request.form.get("quotation_date"), request.form.get("valid_until"),
                request.form.get("notes"),
                _lines("ref_item_id[]", "qty_offered[]", "unit_price[]", "lead_time_days[]"))
            inv.commit()
        except InventoryError as e:
            inv.rollback()
            flash(str(e), "error")
        else:
            flash("Quotation from %s saved." % supplier_name, "success")
            if request.form.get("action") == "save_and_next":
                try:
                    done = int(request.form.get("supplier_id"))
                except (TypeError, ValueError):
                    done = 0
                nxt = pur.next_supplier_to_quote(ref_id, done)
                if nxt:
                    return redirect(url_for("purchasing.quotation_new", ref_id=ref_id, supplier_id=nxt))
                flash("Every supplier on this RFQ has replied. Choose suppliers when you are ready.", "info")
                return redirect(url_for("purchasing.ref_detail", ref_id=ref_id))
            return redirect(url_for("purchasing.quotation_new", ref_id=ref_id,
                                    supplier_id=request.form.get("supplier_id")))
    return render_template("purchasing/quotation_new.html", data=data, f=request.form)


@bp.route("/ref/<int:ref_id>/compare", methods=["GET", "POST"])
@roles_required("warehouse_manager")
def ref_compare(ref_id):
    user = current_user()
    try:
        data = pur.compare_data(ref_id, user)
    except InventoryError as e:
        flash(str(e), "error")
        return redirect(url_for("purchasing.ref_list"))
    if request.method == "POST":
        try:
            choices = {}
            for k in request.form:
                if k.startswith("choice_"):
                    rid = int(k[len("choice_"):])
                    choices[rid] = (request.form.get(k), request.form.get("qty_%d" % rid))
            _first, _no, n = pur.submit_choices(user, ref_id, choices)
            inv.commit()
        except (InventoryError, ValueError) as e:
            inv.rollback()
            flash(str(e) if isinstance(e, InventoryError) else "Choose a supplier for every item.", "error")
        else:
            left = pur.get_ref(ref_id, user)["has_open"]
            flash("Sent for approval: %d supplier%s. Each approved supplier gets its own purchase order.%s" % (
                n, "" if n == 1 else "s",
                " Some items are still open." if left else ""),
                "success")
            return redirect(url_for("purchasing.ref_detail", ref_id=ref_id))
    return render_template("purchasing/ref_compare.html", data=data, f=request.form)


@bp.route("/ref/<int:ref_id>/restore", methods=["POST"])
@roles_required("warehouse_manager")
def ref_restore(ref_id):
    """Undo a give-up: the item needs a supplier again."""
    user = current_user()
    try:
        ref_no, item = pur.restore_dropped(user, ref_id, request.form.get("ref_item_id"))
        inv.commit()
    except InventoryError as e:
        inv.rollback()
        flash(str(e), "error")
    else:
        flash("%s is back on %s and needs a supplier again." % (item, ref_no), "success")
    return redirect(url_for("purchasing.ref_detail", ref_id=ref_id))


@bp.route("/ref/<int:ref_id>/drop", methods=["POST"])
@roles_required("warehouse_manager")
def ref_drop(ref_id):
    """Give up on an item nobody can supply, with a reason."""
    user = current_user()
    try:
        ref_no, _n = pur.drop_open_lines(user, ref_id, [(request.form.get("ref_item_id"),
                                                          request.form.get("reason"))])
        notes = list(getattr(pur.drop_open_lines, "notes", []))
        inv.commit()
    except InventoryError as e:
        inv.rollback()
        flash(str(e), "error")
    else:
        flash("Item dropped from %s." % ref_no, "success")
        for note in notes:
            flash(note, "info")
    return redirect(url_for("purchasing.ref_detail", ref_id=ref_id))


# ---------------------------------------------------------------------
# Owner approval
# ---------------------------------------------------------------------

@bp.route("/approvals")
@roles_required("owner", "warehouse_manager")
def approval_list():
    user = current_user()
    res = pur.list_approvals(user, request.args.get("decision", ""),
                             request.args.get("q", ""), _page())
    return render_template("purchasing/approval_list.html", res=res, f=request.args,
                           refs_to_approve=pur.list_refs_to_approve(user))


@bp.route("/approvals/<int:approval_id>", methods=["GET", "POST"])
@roles_required("owner")
def approval_detail(approval_id):
    user = current_user()
    try:
        appr = pur.get_approval(approval_id, user)
    except InventoryError as e:
        flash(str(e), "error")
        return redirect(url_for("purchasing.approval_list"))
    if appr is None:
        return render_template("404.html"), 404
    if request.method == "POST":
        try:
            decisions = {r["id"]: (request.form.get("decision_%d" % r["id"]) or request.form.get("decision"))
                         for r in appr["batch_pending"]}
            results = pur.decide_batch(user, approval_id, decisions, request.form.get("remarks"),
                                       request.form.get("expected_date"), request.form.get("terms"))
            approved = [r for r in results if r[2] == "approved"]
            if approved:      # the owner's approval IS the signature: once on the REF, once on each new PO
                docs.sign_on_action(user, "REF", appr["ref_id"], "approved_by", skip_if_signed=True)
                for _aid, _sup, _res, _no, po_id in approved:
                    docs.sign_on_action(user, "PO", po_id, "approved_by")
            inv.commit()
        except InventoryError as e:
            inv.rollback()
            flash(str(e), "error")
        else:
            rejected = [r for r in results if r[2] == "rejected"]
            msg = ""
            if approved:
                msg += "Approved and signed: %s. Ready to send. " % ", ".join(
                    "%s (%s)" % (r[3], r[1]) for r in approved)
            if rejected:
                msg += "Rejected: %s. Sent back to the warehouse manager." % ", ".join(
                    r[1] for r in rejected)
            flash(msg.strip(), "success")
            if len(approved) == 1 and not rejected:
                return redirect(url_for("purchasing.po_detail", po_id=approved[0][4]))
            return redirect(url_for("purchasing.ref_detail", ref_id=appr["ref_id"]))
    return render_template("purchasing/approval_detail.html", appr=appr, f=request.form)


@bp.route("/approvals/<int:approval_id>/withdraw", methods=["POST"])
@roles_required("warehouse_manager")
def approval_withdraw(approval_id):
    try:
        ref_id, supplier = pur.withdraw_approval(current_user(), approval_id)
        inv.commit()
    except InventoryError as e:
        inv.rollback()
        flash(str(e), "error")
        ref_id = pur.approval_ref_id(approval_id)
    else:
        flash("Approval for %s withdrawn. Choose a supplier again." % supplier, "success")
    if ref_id:
        return redirect(url_for("purchasing.ref_detail", ref_id=ref_id))
    return redirect(url_for("purchasing.approval_list"))


# ---------------------------------------------------------------------
# Purchase orders (read; Delivery receives the goods)
# ---------------------------------------------------------------------

@bp.route("/po")
@roles_required("warehouse_manager", "owner", "warehouse_staff")
def po_list():
    res = pur.list_pos(current_user(), request.args.get("status", ""), request.args.get("q", ""), _page())
    return render_template("purchasing/po_list.html", res=res, f=request.args)


@bp.route("/po/<int:po_id>", methods=["GET", "POST"])
@roles_required("warehouse_manager", "owner", "warehouse_staff")
def po_detail(po_id):
    user = current_user()
    po = pur.get_po(po_id, user)
    if po is None:
        return render_template("404.html"), 404
    if request.method == "POST" and request.form.get("action") == "mark_sent":
        try:
            po_no = pur.mark_po_sent(user, po_id)
            docs.sign_on_action(user, "PO", po_id, "sent_by")
            inv.commit()
        except InventoryError as e:
            inv.rollback()
            flash(str(e), "error")
        else:
            flash("%s signed and marked as sent." % po_no, "success")
        return redirect(url_for("purchasing.po_detail", po_id=po_id))
    return render_template("purchasing/po_detail.html", po=po)


@bp.route("/po/<int:po_id>/edit", methods=["GET", "POST"])
@roles_required("warehouse_manager", "owner")
def po_edit(po_id):
    user = current_user()
    po = pur.get_po(po_id, user)
    if po is None:
        return render_template("404.html"), 404
    if po["status"] != "draft":
        flash("Only a draft PO can be edited.", "error")
        return redirect(url_for("purchasing.po_detail", po_id=po_id))
    if request.method == "POST":
        try:
            po_no = pur.update_po(user, po_id, request.form.get("expected_date"),
                                  request.form.get("terms"))
            inv.commit()
        except InventoryError as e:
            inv.rollback()
            flash(str(e), "error")
        else:
            flash("%s updated." % po_no, "success")
            return redirect(url_for("purchasing.po_detail", po_id=po_id))
        po = pur.get_po(po_id, user)
    return render_template("purchasing/po_edit.html", po=po, f=request.form)


@bp.route("/po/<int:po_id>/cancel", methods=["GET", "POST"])
@roles_required("warehouse_manager", "owner")
def po_cancel(po_id):
    user = current_user()
    po = pur.get_po(po_id, user)
    if po is None:
        return render_template("404.html"), 404
    if po["status"] not in ("draft", "sent"):
        flash("This PO is %s, so it can no longer be cancelled." % po["status"].replace("_", " "), "error")
        return redirect(url_for("purchasing.po_detail", po_id=po_id))
    if request.method == "POST":
        try:
            po_no = pur.cancel_po(user, po_id, request.form.get("cancel_reason"))
            inv.commit()
        except InventoryError as e:
            inv.rollback()
            flash(str(e), "error")
        else:
            flash("%s was cancelled." % po_no, "success")
            return redirect(url_for("purchasing.po_list"))
    return render_template("purchasing/po_cancel.html", po=po, f=request.form)


# ---------------------------------------------------------------------
# Suppliers and agreed prices
# ---------------------------------------------------------------------

@bp.route("/suppliers")
@roles_required("warehouse_manager", "owner")
def supplier_list():
    res = pur.list_suppliers(request.args.get("q", ""), request.args.get("show", ""), _page())
    return render_template("purchasing/supplier_list.html", res=res,
                           items=pur.list_items_for_select(), f=request.args)


@bp.route("/suppliers/new", methods=["GET", "POST"])
@roles_required("warehouse_manager", "owner")
def supplier_new():
    user = current_user()
    if request.method == "POST":
        try:
            sid, name, _created = pur.save_supplier(
                user, None, request.form,
                _lines("item_id[]", "agreed_price[]", "valid_from[]", "valid_to[]"))
            inv.commit()
        except InventoryError as e:
            inv.rollback()
            flash(str(e), "error")
        else:
            flash("Supplier %s saved." % name, "success")
            return redirect(url_for("purchasing.supplier_edit", supplier_id=sid))
    return render_template("purchasing/supplier_new.html", items=pur.list_items_for_select(),
                           f=request.form)


@bp.route("/suppliers/<int:supplier_id>/edit", methods=["GET", "POST"])
@roles_required("warehouse_manager", "owner")
def supplier_edit(supplier_id):
    user = current_user()
    supplier = pur.get_supplier(supplier_id)
    if supplier is None:
        return render_template("404.html"), 404
    if request.method == "POST":
        try:
            sid, name, _created = pur.save_supplier(
                user, supplier_id, request.form,
                _lines("item_id[]", "agreed_price[]", "valid_from[]", "valid_to[]"))
            inv.commit()
        except InventoryError as e:
            inv.rollback()
            flash(str(e), "error")
        else:
            flash("Supplier %s saved." % name, "success")
            return redirect(url_for("purchasing.supplier_edit", supplier_id=sid))
        supplier = pur.get_supplier(supplier_id)
    return render_template("purchasing/supplier_edit.html", supplier=supplier,
                           items=pur.list_items_for_select(), f=request.form)
