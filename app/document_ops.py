"""Printable document data and e-signature slots.

The screens show HTML; this module turns each document into one common shape so
the printable page and the PDF share the same data. Signatures live in
`document_signatures` and are created by app/signing.py (name and role always
come from the account; a signed line is final).
"""
from . import delivery_ops as dop
from . import inventory_service as inv
from . import purchasing_ops as pur
from . import requisition_ops as req
from . import signing
from .inventory_service import InventoryError

DOC_TYPES = signing.DOC_TYPES
SLOTS = signing.SLOTS

TITLES = {
    "RF": "Requisition Form",
    "REF": "Replenishment Form",
    "PO": "Purchase Order",
    "TS": "Transfer Slip",
    "AR": "Acknowledgment Receipt",
    "RM": "Receiving Memo",
    "DR": "Discrepancy Report",
}


def _b():
    return inv.backend()


def _text(value, length=None):
    if value is None:
        return ""
    text = str(value)
    return text[:length] if length else text


def _date(value):
    return _text(value, 10)


def _status_label(doc_type, status):
    """What the status badge says. An approved PO is 'Ready to send', not 'Draft'."""
    raw = _text(status)
    if doc_type == "PO" and raw == "draft":
        return "Ready to send"
    if raw == "partly_ordered":
        return "Partly ordered"
    return raw.replace("_", " ").title()


def _slot_data(doc_type, doc_id, user, doc_hash):
    """One entry per signature line, ready for the page and the PDF."""
    signatures = signing.signatures_for(doc_type, doc_id)
    out = []
    for spec in SLOTS.get(doc_type, []):
        sig = signatures.get(spec["key"])
        entry = {"key": spec["key"], "label": spec["label"], "mode": spec["mode"],
                 "signed": sig, "role_title": "", "changed": False,
                 "can_capture": (sig is None and spec["mode"] == "captured"
                                 and user.get("role") in spec["roles"]),
                 # only a manager signs, and only lines that are signed from the document page
                 "can_sign": (sig is None and spec["mode"] == "manual"
                              and user.get("role") in spec["roles"] and bool(user.get("id")))}
        if sig:
            entry["role_title"] = signing.role_title(sig.get("signer_role"))
            # tamper check: the fingerprint taken when signing must still match
            entry["changed"] = bool(sig.get("doc_hash")) and sig["doc_hash"] != doc_hash
        out.append(entry)
    return out


def _document(doc_type, title, doc_no, status, date, party_from, party_to,
              meta, items, user, doc_id, show_prices=False, total=None, notes=None,
              show_amounts=True, prepared=None):
    doc_hash = signing.content_hash(doc_type, doc_no, party_from, party_to, items, total)
    return {
        "doc_type": doc_type,
        "title": title,
        "doc_no": doc_no,
        "status": _status_label(doc_type, status),
        "date": _date(date),
        "party_from": party_from,
        "party_to": party_to,
        "meta": meta,
        "lines": items,
        "show_prices": show_prices,
        "show_amounts": show_amounts and show_prices,
        "total": total,
        "notes": [n for n in (notes or []) if n],
        # people who prepared the record: shown as plain text, they do not sign
        "prepared": [(label, name) for label, name in (prepared or []) if name],
        "slots": _slot_data(doc_type, doc_id, user, doc_hash),
        "doc_hash": doc_hash,
        "doc_id": doc_id,
    }


def _location(location_id):
    row = _b().one("SELECT code, name FROM locations WHERE id = :i", i=location_id)
    return ("%s — %s" % (row["code"], row["name"])) if row else "–"


# ---------------------------------------------------------------------
# Loaders
# ---------------------------------------------------------------------

def _load_rf(doc_id, user):
    head = req.get_rf(doc_id, user)
    if head is None:
        return None
    items = [{"code": l["item_code"], "name": l["item_name"], "qty": l["qty_requested"],
              "note": "%s delivered" % l["qty_delivered"] if l["qty_delivered"] else ""}
             for l in head["lines"]]
    return _document(
        "RF", TITLES["RF"], head["rf_no"], head["status"], head.get("submitted_at") or head.get("created_at"),
        ("Shop", "%s — %s" % (head["shop"], head["shop_name"])),
        ("Warehouse", "%s — %s" % (head["warehouse"], head["warehouse_name"])),
        [("Requested by", head["requested_by_name"]),
         ("Alert", head["alert_no"] or "–")],
        items, user, doc_id, notes=[head.get("remarks")])


def _load_ref(doc_id, user):
    head = pur.get_ref(doc_id, user)
    if head is None:
        return None
    items = [{"code": l["item_code"], "name": l["item_name"], "qty": l["qty_requested"],
              "note": "%s received" % l["qty_received"] if l["qty_received"] else ""}
             for l in head["lines"]]
    return _document(
        "REF", TITLES["REF"], head["ref_no"], "partly_ordered" if head.get("partly_ordered") else head["status"],
        head.get("submitted_at") or head.get("created_at"),
        ("From", "Warehouse %s" % head["warehouse"]),
        ("To", "Purchasing / Supplier"),
        [("Warehouse", "%s — %s" % (head["warehouse"], head["warehouse_name"])),
         ("Created by", head["created_by_name"])],
        items, user, doc_id, notes=[head.get("remarks")])


def _load_po(doc_id, user):
    po = pur.get_po(doc_id, user)
    if po is None:
        return None
    items = [{"code": l["item_code"], "name": l["item_name"], "qty": l["qty_ordered"],
              "price": l["unit_price"], "amount": l["amount"],
              "note": "%s received" % l["qty_received"] if l["qty_received"] else ""}
             for l in po["lines"]]
    return _document(
        "PO", TITLES["PO"], po["po_no"], po["status"], po["po_date"],
        ("Supplier", po["supplier_name"]),
        ("Deliver to", _location(po["warehouse_id"])),
        [("REF no.", po["ref_no"]),
         ("Expected date", _date(po["expected_date"]) or "–"),
         ("Terms", po.get("terms") or "–"),
         ("Approved by", po["approved_by_name"])],
        items, user, doc_id, show_prices=True, total=po["total_amount"],
        notes=[po.get("remarks")])


def _load_ts(doc_id, user):
    ts = dop.get_transfer(doc_id, user)
    if ts is None:
        return None
    items = [{"code": l["item_code"], "name": l["item_name"], "qty": l["qty_released"],
              "note": ("received %s" % l["qty_received"]) if l.get("qty_received") is not None else ""}
             for l in ts["lines"]]
    return _document(
        "TS", TITLES["TS"], ts["ts_no"], ts["status"],
        ts.get("released_at") or ts.get("created_at"),
        ("From", "%s — %s" % (ts["from_code"], ts["from_name"])),
        ("To", "%s — %s" % (ts["shop_code"], ts["shop_name"])),
        [("RF no.", ts["rf_no"]),
         ("Driver", ts["driver_name"] or "–"),
         ("Vehicle", ts["vehicle_plate"] or "–"),
         ("Driver contact", ts.get("driver_contact") or "–")],
        items, user, doc_id,
        notes=[ts.get("remarks"),
               ("The driver could not sign on screen. Released with this reason: %s" % ts["driver_paper_reason"])
               if ts.get("driver_paper_reason") else None],
        prepared=[("Prepared by", ts["prepared_by_name"]),
                  ("Loaded and checked by", ts.get("loaded_by_name"))])


def _load_ar(doc_id, user):
    ar = _b().one(
        "SELECT ar.*, u.full_name AS received_by_name, t.ts_no, t.from_location_id, t.to_location_id "
        "FROM acknowledgment_receipts ar JOIN transfer_slips t ON t.id = ar.ts_id "
        "JOIN users u ON u.id = ar.received_by WHERE ar.id = :i", i=doc_id)
    if ar is None:
        return None
    ts = dop.get_transfer(ar["ts_id"], user)
    if ts is None:
        return None
    lines = _b().all(
        "SELECT ari.*, i.item_code, i.item_name FROM acknowledgment_receipt_items ari "
        "JOIN items i ON i.id = ari.item_id WHERE ari.ar_id = :i ORDER BY i.item_name", i=doc_id)
    items = [{"code": l["item_code"], "name": l["item_name"], "qty": l["qty_received"],
              "note": ("%s rejected/short" % (l["qty_expected"] - l["qty_received"]))
                      if l["qty_received"] < l["qty_expected"] else ""}
             for l in lines]
    return _document(
        "AR", TITLES["AR"], ar["ar_no"], "delivered_with_variance" if ar["has_variance"] else "delivered",
        ar["received_at"], ("From", "%s — %s" % (ts["from_code"], ts["from_name"])),
        ("To", "%s — %s" % (ts["shop_code"], ts["shop_name"])),
        [("TS no.", ar["ts_no"]),
         ("Variance", "Yes" if ar["has_variance"] else "No")],
        items, user, doc_id, notes=[ar.get("remarks")])


def _load_rm(doc_id, user):
    rm = dop.get_receiving(doc_id, user)
    if rm is None:
        return None
    items = [{"code": l["item_code"], "name": l["item_name"], "qty": l["qty_accepted"],
              "note": ("%s rejected" % l["qty_rejected"]) if l["qty_rejected"] else ""}
             for l in rm["lines"]]
    return _document(
        "RM", TITLES["RM"], rm["rm_no"], "received", rm["received_date"],
        ("Supplier", rm["supplier_name"]),
        ("Received at", _location(rm["warehouse_id"])),
        [("PO no.", rm["po_no"]), ("REF no.", rm["ref_no"]), ("Supplier DR no.", rm["supplier_dr_no"] or "–")]
        + ([("Supplier invoice no.", rm["supplier_invoice_no"])] if rm.get("supplier_invoice_no") else [])
        + ([("Delivered by", rm["delivered_by"])] if rm.get("delivered_by") else []),
        items, user, doc_id, notes=[rm.get("remarks")],
        prepared=[("Received by", rm["received_by_name"])])


def _load_dr(doc_id, user):
    dr = dop.get_discrepancy(doc_id, user)
    if dr is None:
        return None
    items = [{"code": l["item_code"], "name": l["item_name"], "qty": l["qty_short"],
              "note": "rejected %s · replaced %s · open %s"
                      % (l["qty_rejected"], l["qty_replaced"], l["qty_outstanding"])}
             for l in dr["lines"]]
    return _document(
        "DR", TITLES["DR"], dr["report_no"], dr["status"], dr["created_at"],
        ("Supplier", dr["supplier_name"]),
        ("Warehouse", _location(dr["warehouse_id"])),
        [("PO no.", dr["po_no"]), ("RM no.", dr["rm_no"]),
         ("Outstanding", dr["qty_outstanding"]),
         ("Resolved", _date(dr.get("resolved_at")) or "–")],
        items, user, doc_id, notes=[dr.get("follow_up_notes")])


LOADERS = {"RF": _load_rf, "REF": _load_ref, "PO": _load_po, "TS": _load_ts,
           "AR": _load_ar, "RM": _load_rm, "DR": _load_dr}


def load(doc_type, doc_id, user):
    doc_type = (doc_type or "").upper()
    loader = LOADERS.get(doc_type)
    if loader is None:
        return None
    return loader(doc_id, user)


def sign_manual(user, doc_type, doc_id, slot):
    """The manager presses Sign on the document page (RM, DR). Returns the slot label."""
    doc_type = (doc_type or "").upper()
    spec = signing.slot_def(doc_type, slot)
    if spec is None:
        raise InventoryError("Unknown signature line.")
    if spec["mode"] != "manual":
        raise InventoryError("This line is signed by the button that sends the document, not here.")
    doc = load(doc_type, doc_id, user)
    if doc is None:
        raise InventoryError("Document not found, or it is not yours to sign.")
    return signing.sign_slot(user, doc_type, doc_id, slot, doc["doc_hash"])


def sign_on_action(user, doc_type, doc_id, *slots, skip_if_signed=False):
    """Sign inside a workflow action (same transaction). The signer is the
    logged-in account. Raises InventoryError, which rolls the whole action back."""
    doc_type = (doc_type or "").upper()
    doc = load(doc_type, doc_id, user)
    if doc is None:
        raise InventoryError("Could not sign: the document was not found.")
    labels = []
    for slot in slots:
        label = signing.sign_slot(user, doc_type, doc_id, slot, doc["doc_hash"],
                                  skip_if_signed=skip_if_signed)
        if label:
            labels.append(label)
    return labels


def driver_signature(ts):
    """The driver's signature for this TS, or None. A signature only counts for the driver
    who is assigned now (the name must still match), so reassigning the driver never reuses it."""
    sig = signing.signatures_for("TS", ts["id"]).get("driver")
    if sig and sig["signer_name"] == (ts.get("driver_name") or ""):
        return sig
    return None


def capture_driver_signature(user, ts_id, data_url):
    """The driver signs on a warehouse screen after loading (status 'loaded')."""
    ts = dop.get_transfer(ts_id, user)
    if ts is None:
        raise InventoryError("Transfer slip not found.")
    if ts["status"] != "loaded":
        raise InventoryError("The driver signs after the loading check, before the manager releases the TS.")
    if not ts.get("driver_name"):
        raise InventoryError("Assign a driver to this transfer slip first.")
    try:
        png = signing.normalize_signature(signing.decode_data_url(data_url))
    except ValueError as e:
        raise InventoryError(str(e))
    doc = load("TS", ts_id, user)
    signing.capture_driver(user, ts_id, ts["driver_name"], png, doc["doc_hash"])
    return ts["driver_name"]


def require_driver_clearance(user, ts, paper_reason=""):
    """Release gate: the driver has signed on screen, or the manager gives a written reason
    (the driver signed on paper / could not sign). Nothing is released unsigned and unexplained."""
    if driver_signature(ts):
        return
    reason = (paper_reason or "").strip()
    if not reason:
        raise InventoryError("The driver has not signed yet. Ask the warehouse staff to capture the driver's "
                             "signature, or write why it could not be done on screen.")
    if len(reason) > 300:
        raise InventoryError("The reason can be at most 300 characters.")
    inv.backend().execute("UPDATE transfer_slips SET driver_paper_reason = :r WHERE id = :i", r=reason, i=ts["id"])
    inv.write_audit(user["id"], "driver_paper", "transfer_slips", ts["id"], new={"reason": reason})


def pending_signatures(user, limit=50):
    """Documents that wait for THIS manager's Sign button (receiving memos and
    discrepancy reports, which staff may have recorded). Newest first."""
    role = user.get("role")
    loc = user.get("location_id")
    rows = []
    try:
        if role == "warehouse_manager" and loc:
            for r in inv.backend().all(
                    "SELECT gr.id, gr.rm_no AS doc_no, gr.received_date AS doc_date FROM goods_receipts gr "
                    "JOIN purchase_orders po ON po.id = gr.po_id JOIN replenishments r ON r.id = po.ref_id "
                    "WHERE r.warehouse_id = :l AND NOT EXISTS (SELECT 1 FROM document_signatures s "
                    "WHERE s.doc_type = 'RM' AND s.doc_id = gr.id AND s.slot = 'checked_by') "
                    "ORDER BY gr.id DESC LIMIT :n", l=loc, n=limit):
                rows.append(dict(r, doc_type="RM", slot="checked_by", line="Checked by"))
            for r in inv.backend().all(
                    "SELECT d.id, d.report_no AS doc_no, d.created_at AS doc_date FROM discrepancy_reports d "
                    "JOIN purchase_orders po ON po.id = d.po_id JOIN replenishments r ON r.id = po.ref_id "
                    "WHERE r.warehouse_id = :l AND NOT EXISTS (SELECT 1 FROM document_signatures s "
                    "WHERE s.doc_type = 'DR' AND s.doc_id = d.id AND s.slot = 'prepared_by') "
                    "ORDER BY d.id DESC LIMIT :n", l=loc, n=limit):
                rows.append(dict(r, doc_type="DR", slot="prepared_by", line="Prepared by"))
        elif role == "owner":
            for r in inv.backend().all(
                    "SELECT d.id, d.report_no AS doc_no, d.created_at AS doc_date FROM discrepancy_reports d "
                    "WHERE EXISTS (SELECT 1 FROM document_signatures s WHERE s.doc_type = 'DR' "
                    "AND s.doc_id = d.id AND s.slot = 'prepared_by') AND NOT EXISTS (SELECT 1 FROM "
                    "document_signatures s WHERE s.doc_type = 'DR' AND s.doc_id = d.id AND s.slot = 'noted_by') "
                    "ORDER BY d.id DESC LIMIT :n", n=limit):
                rows.append(dict(r, doc_type="DR", slot="noted_by", line="Noted by"))
    except Exception:
        inv.rollback()
        return []
    for r in rows:
        r["doc_date"] = _date(r.get("doc_date"))
        r["title"] = TITLES[r["doc_type"]]
    return rows
