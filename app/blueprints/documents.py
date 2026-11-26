# Printable, e-signed documents (RF, REF, RFQ, PO, TS, AR, RM, DR)
import re

from flask import Blueprint, Response, current_app, flash, redirect, render_template, request, url_for

from ..roles import roles_required
from .. import document_ops as docs
from .. import inventory_service as inv
from .. import pdf_service
from ..inventory_service import InventoryError
from ..session_user import current_user

bp = Blueprint("documents", __name__, url_prefix="/documents")

ALL_ROLES = ("owner", "shop_manager", "shop_staff", "warehouse_manager", "warehouse_staff")


def _supplier_arg(doc_type):
    """?supplier=<id> addresses an RFQ copy to one supplier. Ignored for other documents."""
    return request.args.get("supplier", type=int) if (doc_type or "").upper() == "RFQ" else None


def _back_url(doc_type, doc_id, data=None):
    if doc_type == "RFQ" and data and data.get("ref_id"):
        return url_for("purchasing.rfq_detail", ref_id=data["ref_id"], rfq_id=doc_id)
    if doc_type == "RF":
        return url_for("requisition.rf_detail", rf_id=doc_id)
    if doc_type == "REF":
        return url_for("purchasing.ref_detail", ref_id=doc_id)
    if doc_type == "PO":
        return url_for("purchasing.po_detail", po_id=doc_id)
    if doc_type == "TS":
        return url_for("delivery.transfer_detail", ts_id=doc_id)
    if doc_type == "AR":
        return url_for("delivery.delivery_list")
    if doc_type == "RM":
        return url_for("delivery.receiving_detail", rm_id=doc_id)
    if doc_type == "DR":
        return url_for("delivery.discrepancy_detail", report_id=doc_id)
    return url_for("dashboard.index")


@bp.route("/to-sign")
@roles_required("owner", "warehouse_manager")
def to_sign():
    """Documents waiting for this manager's signature."""
    return render_template("print/to_sign.html", rows=docs.pending_signatures(current_user()))


@bp.route("/<doc_type>/<int:doc_id>")
@roles_required(*ALL_ROLES)
def view(doc_type, doc_id):
    user = current_user()
    data = docs.load(doc_type, doc_id, user, supplier_id=_supplier_arg(doc_type))
    if data is None:
        return render_template("404.html"), 404
    data["back_url"] = _back_url(data["doc_type"], doc_id, data)
    return render_template("print/document.html", d=data)


@bp.route("/<doc_type>/<int:doc_id>.pdf")
@roles_required(*ALL_ROLES)
def pdf(doc_type, doc_id):
    """The document as a real PDF file (not the browser's Print dialog)."""
    data = docs.load(doc_type, doc_id, current_user(), supplier_id=_supplier_arg(doc_type))
    if data is None:
        return render_template("404.html"), 404
    body = pdf_service.build_pdf(data, current_app.config.get("COMPANY_NAME", "3T Tire Supply"))
    resp = Response(body, mimetype="application/pdf")
    name = data["doc_no"]
    if data.get("supplier_name"):          # one file per supplier: RFQ-2026-0001-Sup-A.pdf
        name += "-" + re.sub(r"[^A-Za-z0-9]+", "-", data["supplier_name"]).strip("-")
    resp.headers["Content-Disposition"] = 'inline; filename="%s.pdf"' % name
    resp.headers["Cache-Control"] = "private, no-store"
    return resp


@bp.route("/<doc_type>/<int:doc_id>/sign", methods=["POST"])
@roles_required(*ALL_ROLES)
def sign(doc_type, doc_id):
    """Manager presses Sign on a document that staff may have recorded (RM, DR).
    The name and role come from the account. A signed line cannot be replaced."""
    user = current_user()
    try:
        label = docs.sign_manual(user, doc_type, doc_id, request.form.get("slot"))
        inv.commit()
    except InventoryError as e:
        inv.rollback()
        flash(str(e), "error")
    else:
        flash("Signed: %s." % label, "success")
    return redirect(url_for("documents.view", doc_type=doc_type.upper(), doc_id=doc_id))
