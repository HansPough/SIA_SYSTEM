from itertools import zip_longest

from flask import Blueprint, render_template, request, redirect, url_for, flash
from ..roles import roles_required
from .. import inventory_service as inv
from .. import inventory_ops as ops
from .. import document_ops as docs
from ..inventory_service import InventoryError
from ..session_user import current_user, scope_location

bp = Blueprint("inventory", __name__, url_prefix="/inventory")


def _page():
    try:
        return max(1, int(request.args.get("page", 1)))
    except ValueError:
        return 1


def _own_location(user):
    """The location a shop / warehouse user works in, or None (with a message) if the account has none."""
    if not user["location_id"]:
        flash("Your account has no location. Ask the owner to set it.", "error")
        return None
    return user["location_id"]


def _lines(*names):
    """Table rows from the form, one tuple per row. A missing column becomes ''."""
    return list(zip_longest(*[request.form.getlist(n) for n in names], fillvalue=""))


# ---------------------------------------------------------------------
# Stock
# ---------------------------------------------------------------------

@bp.route("/stock")
@roles_required("owner", "shop_manager", "shop_staff", "warehouse_manager", "warehouse_staff")
def stock_list():
    user = current_user()
    scope = scope_location(user)
    if user["role"] != "owner" and scope is None:
        _own_location(user)
    loc_arg = request.args.get("location", type=int)
    res = ops.stock_rows(scope, request.args.get("q", ""), loc_arg,
                         request.args.get("category", ""), request.args.get("status", ""), _page())
    return render_template("inventory/stock_list.html", res=res, locations=ops.list_locations(scope),
                           f=request.args)


@bp.route("/stock/<int:item_id>")
@roles_required("owner", "shop_manager", "shop_staff", "warehouse_manager", "warehouse_staff")
def stock_detail(item_id):
    card = ops.stock_card(item_id, scope_location(current_user()))
    if card is None:
        return render_template("404.html"), 404
    return render_template("inventory/stock_detail.html", item_id=item_id, card=card)


# ---------------------------------------------------------------------
# Stock count sheet (one bulk adjustment request)
# ---------------------------------------------------------------------

@bp.route("/count", methods=["GET", "POST"])
@roles_required("owner", "shop_manager", "shop_staff", "warehouse_manager", "warehouse_staff")
def count_sheet():
    user = current_user()
    scope = scope_location(user)
    locations = ops.list_locations(scope)
    if not locations:
        flash("No active location is available for a stock count.", "error")
        return redirect(url_for("inventory.stock_list"))
    loc_id = request.values.get("location", type=int)
    if scope is not None:
        loc_id = scope
    if loc_id is None or not any(l["id"] == loc_id for l in locations):
        loc_id = locations[0]["id"]
    items = ops.items_for_select(loc_id)
    if request.method == "POST":
        rows = list(zip_longest(request.form.getlist("item_id[]"),
                                request.form.getlist("qty_actual[]"),
                                request.form.getlist("item_note[]"), fillvalue=""))
        current = {i["id"]: i["on_hand"] for i in items}
        triples = []
        for item_id, actual, note in rows:
            if not str(item_id).strip() or not str(actual).strip():
                continue
            try:
                iid = int(str(item_id).strip())
                qty = int(str(actual).strip())
            except ValueError:
                flash("Every counted quantity must be a whole number.", "error")
                return render_template("inventory/count_sheet.html", items=items, locations=locations,
                                       loc_id=loc_id, f=request.form)
            if qty < 0:
                flash("A counted quantity cannot be negative.", "error")
                return render_template("inventory/count_sheet.html", items=items, locations=locations,
                                       loc_id=loc_id, f=request.form)
            if current.get(iid) is not None and qty != current[iid]:
                triples.append((iid, qty, (note or "").strip()))
        if not triples:
            flash("No differences were entered, so no adjustment was created.", "info")
            return redirect(url_for("inventory.count_sheet", location=loc_id))
        try:
            auto = user["role"] in ops.MANAGER_ROLES
            adj_id, adj_no = ops.create_adjustment(
                user["id"], loc_id, "count difference", None,
                request.form.get("remarks"), triples, auto_approve=auto)
            inv.commit()
        except InventoryError as e:
            inv.rollback()
            flash(str(e), "error")
        else:
            if auto:
                flash("%s saved. Stock updated for %d item(s)." % (adj_no, len(triples)), "success")
            else:
                flash("%s saved. Waiting for approval." % adj_no, "success")
            return redirect(url_for("inventory.adjustment_detail", adj_id=adj_id))
    return render_template("inventory/count_sheet.html", items=items, locations=locations,
                           loc_id=loc_id, f=request.form)


# ---------------------------------------------------------------------
# Sales / issues
# ---------------------------------------------------------------------

@bp.route("/issues")
@roles_required("shop_staff", "shop_manager", "owner")
def issue_list():
    res = ops.list_issues(scope_location(current_user()), request.args.get("q", ""),
                          request.args.get("period", ""), _page())
    return render_template("inventory/issue_list.html", res=res, f=request.args)


@bp.route("/issues/new", methods=["GET", "POST"])
@roles_required("shop_staff", "shop_manager")
def issue_new():
    user = current_user()
    loc_id = _own_location(user)
    if loc_id is None:
        return redirect(url_for("inventory.issue_list"))
    if request.method == "POST":
        try:
            _id, issue_no, low = ops.create_issue(
                user["id"], loc_id, request.form.get("issue_date"), request.form.get("customer_name"),
                request.form.get("document_ref"), request.form.get("reason"), request.form.get("remarks"),
                _lines("item_id[]", "qty[]"))
            inv.commit()
        except InventoryError as e:
            inv.rollback()
            flash(str(e), "error")
        else:
            flash("Saved %s. Stock deducted." % issue_no, "success")
            if low:
                # create_issue() already refreshed the low-stock alert; this only tells the user which items moved.
                flash("Now at or below ROP: " + ", ".join(r["item_name"] for r in low), "info")
            return redirect(url_for("inventory.issue_detail", issue_id=_id))
    return render_template("inventory/issue_new.html", items=ops.items_for_select(loc_id),
                           location=ops.get_location(loc_id), f=request.form,
                           next_no=inv.peek_doc_no("ISS"))


@bp.route("/issues/<int:issue_id>")
@roles_required("shop_staff", "shop_manager", "owner")
def issue_detail(issue_id):
    user = current_user()
    issue = ops.get_issue(issue_id, scope_location(user))
    if issue is None:
        return render_template("404.html"), 404
    can_request, _w1 = ops.can_request_void(user, issue)
    can_confirm, _w2 = ops.can_confirm_void(user, issue)
    return render_template("inventory/issue_detail.html", issue=issue, can_request=can_request, can_confirm=can_confirm)


@bp.route("/issues/<int:issue_id>/void-request", methods=["POST"])
@roles_required("shop_staff", "shop_manager")
def issue_void_request(issue_id):
    try:
        issue_no = ops.request_void(current_user(), issue_id, request.form.get("void_reason"))
        inv.commit()
    except InventoryError as e:
        inv.rollback()
        flash(str(e), "error")
    else:
        flash("Void requested for %s. Waiting for your manager." % issue_no, "success")
    return redirect(url_for("inventory.issue_detail", issue_id=issue_id))


# Staff request, the manager of the same shop (or the owner) confirms. Rules live in ops.can_confirm_void().
@bp.route("/issues/<int:issue_id>/void-review", methods=["POST"])
@roles_required("shop_manager", "owner")
def issue_void_review(issue_id):
    try:
        issue_no, result = ops.review_void(current_user(), issue_id, request.form.get("decision"), request.form.get("remarks"))
        inv.commit()
    except InventoryError as e:
        inv.rollback()
        flash(str(e), "error")
    else:
        flash("%s voided. Stock put back." % issue_no if result == "voided"
              else "Void request for %s rejected." % issue_no, "success")
    return redirect(url_for("inventory.issue_detail", issue_id=issue_id))


# ---------------------------------------------------------------------
# ROP settings (owner)
# ---------------------------------------------------------------------

@bp.route("/rop")
@roles_required("owner")
def rop_list():
    res = ops.rop_rows(request.args.get("q", ""), request.args.get("location", type=int),
                       request.args.get("show") == "missing", _page())
    return render_template("inventory/rop_list.html", res=res, locations=ops.list_locations(), f=request.args)


@bp.route("/rop/<int:item_id>/edit", methods=["GET", "POST"])
@roles_required("owner")
def rop_edit(item_id):
    data = ops.rop_for_item(item_id)
    if data is None:
        return render_template("404.html"), 404
    if request.method == "POST":
        entries = [{"location_id": int(l), "rop": r, "max_level": m, "lead_time": t, "rack": k}
                   for l, r, m, t, k in _lines("location_id[]", "rop[]", "max_level[]", "lead_time_days[]", "rack[]")]
        try:
            changed = ops.save_rop(item_id, entries, current_user()["id"])
            inv.commit()
        except InventoryError as e:
            inv.rollback()
            flash(str(e), "error")
            # show what the owner typed, not the old values
            for row, (_l, r, m, t, k) in zip(data["rows"], _lines("location_id[]", "rop[]", "max_level[]", "lead_time_days[]", "rack[]")):
                row["rop"], row["max_level"], row["lead_time_days"], row["rack"] = r, m, t, k
        else:
            flash("ROP settings saved for: %s." % ", ".join(changed) if changed else "Nothing changed.", "success")
            return redirect(url_for("inventory.rop_list"))
    return render_template("inventory/rop_edit.html", item_id=item_id, data=data)


@bp.route("/rf-queue")
@roles_required("warehouse_manager")
def rfqueue_list():
    user = current_user()
    loc = _own_location(user)
    res = ops.list_rf_queue(loc or 0, request.args.get("q", ""), request.args.get("status", ""), _page())
    return render_template("inventory/rfqueue_list.html", res=res, f=request.args)


@bp.route("/rf-queue/<int:rf_id>", methods=["GET", "POST"])
@roles_required("warehouse_manager")
def rfqueue_review(rf_id):
    user = current_user()
    rf = ops.get_rf(rf_id, user["location_id"] or 0)
    if rf is None:
        return render_template("404.html"), 404
    if request.method == "POST":
        try:
            units, short = ops.reserve_rf(user, rf_id, _lines("rf_item_id[]", "qty_reserve[]"))
            # acting on the RF is the warehouse manager's acknowledgement: sign "Noted by" once
            docs.sign_on_action(user, "RF", rf_id, "noted_by", skip_if_signed=True)
            inv.commit()
        except ops.WaitingForStock as e:
            inv.commit()                       # the shortage was recorded; nothing else changed
            flash(str(e), "info")
            return redirect(url_for("inventory.rfqueue_list"))
        except InventoryError as e:
            inv.rollback()
            flash(str(e), "error")
        else:
            msg = "Reserved %d unit(s) for %s." % (units, rf["rf_no"])
            if short:
                msg += " %d item(s) short, added to the restock alert." % short
            flash(msg, "success")
            updated = ops.get_rf(rf_id, user["location_id"])
            if updated["active_transfer"]:
                flash("A transfer is already open for this requisition.", "info")
                return redirect(url_for("delivery.transfer_detail", ts_id=updated["active_transfer"]["id"]))
            if updated["has_reserved"]:
                return redirect(url_for("delivery.transfer_new", rf_id=rf_id))
            return redirect(url_for("inventory.rfqueue_review", rf_id=rf_id))
        rf = ops.get_rf(rf_id, user["location_id"])
    document = docs.load("RF", rf_id, user)
    if document is None:
        return render_template("404.html"), 404
    active = rf["active_transfer"]
    open_status = rf["status"] in ("open", "partially_delivered")
    blocked = bool(active and active["status"] in ("draft", "assigned", "loaded"))
    wants_more = any(line["remaining"] > 0 for line in rf["lines"])
    can_reserve = (open_status and wants_more and not blocked
                   and any(line["suggest"] > 0 for line in rf["lines"]))      # something is actually available
    waiting_for_stock = open_status and wants_more and not blocked and not can_reserve
    return render_template("inventory/rfqueue_review.html", rf=rf, f=request.form,
                           d=document, can_reserve=can_reserve, waiting_for_stock=waiting_for_stock,
                           has_reserved=rf["has_reserved"], active_transfer=active)


# ---------------------------------------------------------------------
# Stock adjustments
# ---------------------------------------------------------------------

@bp.route("/adjustments")
@roles_required("warehouse_manager", "warehouse_staff", "shop_manager", "shop_staff", "owner")
def adjustment_list():
    res = ops.list_adjustments(scope_location(current_user()), request.args.get("status", ""),
                               request.args.get("q", ""), _page())
    return render_template("inventory/adjustment_list.html", res=res, f=request.args)


@bp.route("/adjustments/new", methods=["GET", "POST"])
@roles_required("warehouse_staff", "warehouse_manager", "shop_staff", "shop_manager")
def adjustment_new():
    user = current_user()
    loc_id = _own_location(user)
    if loc_id is None:
        return redirect(url_for("inventory.adjustment_list"))
    if request.method == "POST":
        try:
            auto = user["role"] in ops.MANAGER_ROLES
            adj_id, adj_no = ops.create_adjustment(
                user["id"], loc_id, request.form.get("reason"), request.form.get("ar_no"),
                request.form.get("remarks"), _lines("item_id[]", "qty_actual[]", "item_remarks[]"),
                auto_approve=auto)
            inv.commit()
        except InventoryError as e:
            inv.rollback()
            flash(str(e), "error")
        else:
            flash(("Saved %s. Stock updated." if auto else "Saved %s. Waiting for approval.") % adj_no,
                  "success")
            return redirect(url_for("inventory.adjustment_detail", adj_id=adj_id))
    return render_template("inventory/adjustment_new.html", items=ops.items_for_select(loc_id),
                           location=ops.get_location(loc_id), f=request.form,
                           next_no=inv.peek_doc_no("ADJ"))


@bp.route("/adjustments/<int:adj_id>")
@roles_required("warehouse_manager", "warehouse_staff", "shop_manager", "shop_staff", "owner")
def adjustment_detail(adj_id):
    user = current_user()
    adj = ops.get_adjustment(adj_id, scope_location(user))
    if adj is None:
        return render_template("404.html"), 404
    can, _why = ops.can_review(user, adj)
    return render_template("inventory/adjustment_detail.html", adj=adj, can_review=can)


# Each manager approves the adjustments of their OWN location only:
#   warehouse_manager -> warehouse, shop_manager -> own shop. The owner is notified and can view them.
# Only if a manager raised the request (no one else at that location can approve), the owner reviews it.
# The rules live in ops.can_review().
@bp.route("/adjustments/<int:adj_id>/review", methods=["GET", "POST"])
@roles_required("warehouse_manager", "shop_manager", "owner")
def adjustment_review(adj_id):
    user = current_user()
    adj = ops.get_adjustment(adj_id, None)
    if adj is None:
        return render_template("404.html"), 404
    ok, why = ops.can_review(user, adj)
    if not ok:
        flash(why, "error")
        return redirect(url_for("inventory.adjustment_detail", adj_id=adj_id))
    if request.method == "POST":
        try:
            adj_no, status = ops.review_adjustment(user, adj_id, request.form.get("decision"),
                                                   request.form.get("review_remarks"))
            inv.commit()
        except InventoryError as e:
            inv.rollback()
            flash(str(e), "error")
        else:
            flash("%s %s." % (adj_no, status), "success")
            return redirect(url_for("inventory.adjustment_detail", adj_id=adj_id))
    return render_template("inventory/adjustment_review.html", adj=adj, f=request.form)
