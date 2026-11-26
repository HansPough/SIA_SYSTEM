"""Printable document data and e-signature slots.

The screens show HTML; this module turns each document into one common shape so
the printable page and the PDF share the same data. Signatures live in
`document_signatures` and are created by app/signing.py (name and role always
come from the account; a signed line is final).
"""
import datetime as dt
from decimal import Decimal

from . import delivery_ops as dop
from . import inventory_service as inv
from . import purchasing_ops as pur
from . import requisition_ops as req
from . import signing
from . import statuses
from .inventory_service import InventoryError

DOC_TYPES = signing.DOC_TYPES
SLOTS = signing.SLOTS

TITLES = {
    "RF": "Requisition Form",
    "REF": "Replenishment Form",
    "RFQ": "Request for Quotation",
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
    """What the status badge says, worded the same on the page and in the PDF."""
    raw = _text(status)
    if doc_type == "PO" and raw == "draft":
        return "Ready to send"
    return statuses.label(raw)


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


LIFECYCLE_TABLES = {
    "RF": "requisitions",
    "REF": "replenishments",
    "RFQ": "rfqs",
    "PO": "purchase_orders",
    "TS": "transfer_slips",
    "AR": "acknowledgment_receipts",
    "RM": "goods_receipts",
    "DR": "discrepancy_reports",
}

LIFECYCLE_ACTIONS = {
    "create": "Created",
    "submit": "Submitted",
    "approve": "Approved",
    "reject": "Rejected",
    "send": "Sent to supplier",
    "receive": "Received",
    "release": "Released",
    "reserve": "Stock reserved",
    "assign": "Driver assigned",
    "load": "Loaded",
    "cancel": "Cancelled",
    "resolve": "Resolved",
    "update": "Updated",
    "driver_paper": "Driver without signature",
    "snooze": "Snoozed",
    "wake": "Woke up",
    "complete": "Alert completed",
}


def lifecycle(doc_type, doc_id):
    """Who did what and when for one document: audit rows + signed lines.

    Read-only and safe on a missing history: an unknown document just returns [].
    """
    doc_type = (doc_type or "").upper()
    table = LIFECYCLE_TABLES.get(doc_type)
    if not table or not doc_id:
        return []
    events = []
    try:
        rows = _b().all(
            "SELECT a.action, a.created_at, u.full_name AS who FROM audit_log a "
            "LEFT JOIN users u ON u.id = a.user_id "
            "WHERE a.table_name = :t AND a.record_id = :i AND a.action <> 'sign' ORDER BY a.id",
            t=table, i=doc_id)
        for row in rows:
            events.append({
                "when": str(row.get("created_at") or "")[:16],
                "what": LIFECYCLE_ACTIONS.get((row.get("action") or "").lower(),
                                              (row.get("action") or "").replace("_", " ").capitalize()),
                "who": row.get("who") or "",
            })
    except Exception:
        inv.rollback()
    try:
        for sig in signing.signatures_for(doc_type, doc_id).values():
            role = signing.role_title(sig.get("signer_role"))
            events.append({
                "when": str(sig.get("signed_at") or "")[:16],
                "what": "Signed (%s)" % role if role else "Signed",
                "who": sig.get("signer_name") or "",
            })
    except Exception:
        inv.rollback()
    events.sort(key=lambda e: e["when"])
    return events


def _document(doc_type, title, doc_no, status, date, party_from, party_to,
              meta, items, user, doc_id, show_prices=False, total=None, notes=None,
              show_amounts=True, prepared=None, qty_columns=None, qty_totals=None,
              system_note=None, fingerprint=None, extra=None):
    # fingerprint: what the signature covers when it must be narrower than what is printed. An RFQ copy
    # shows one supplier's quoted prices, which change when the reply is edited; the signature covers only
    # what was asked (items and quantities), so it never looks "changed after signing".
    fp = fingerprint or {}
    doc_hash = signing.content_hash(doc_type, doc_no, party_from, fp.get("to", party_to),
                                    fp.get("items", items), fp.get("total", total))
    doc = {
        "doc_type": doc_type,
        "title": title,
        "doc_no": doc_no,
        "status": _status_label(doc_type, status) if status else None,   # None: no status badge
        "date": _date(date),
        "party_from": party_from,
        "party_to": party_to,
        "meta": meta,
        "lines": items,
        "show_prices": show_prices,
        "show_amounts": show_amounts and show_prices,
        "total": total,
        "qty_columns": qty_columns,
        "qty_totals": qty_totals,
        "system_note": system_note,
        "notes": [n for n in (notes or []) if n],
        # people who prepared the record: shown as plain text, they do not sign
        "prepared": [(label, name) for label, name in (prepared or []) if name],
        "slots": _slot_data(doc_type, doc_id, user, doc_hash),
        "doc_hash": doc_hash,
        "doc_id": doc_id,
    }
    if extra:
        doc.update(extra)
    return doc


def _qty_totals(lines, keys):
    """Column sums; a total stays unknown (None) while any line is still unknown."""
    totals = {}
    for key in keys:
        values = [line.get(key) for line in lines]
        totals[key] = None if any(v is None for v in values) else sum(values)
    return totals


def _line_note(*parts):
    """One Remarks cell: why the warehouse sent it short, then why the shop rejected it."""
    return " · ".join(p.strip() for p in parts if p and p.strip())


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


def _load_rfq(doc_id, user, supplier_id=None):
    """The RFQ laid out like the PO: items, unit price, amount, total.

    With supplier_id the copy is addressed to that one supplier, does not name the others (suppliers
    should not see who else is quoting) and shows the prices that supplier quoted. Without it, it is the
    internal copy that lists everyone invited and has no prices (there is more than one to show)."""
    rq = pur.get_rfq(doc_id, user)
    if rq is None:
        return None
    chosen = next((s for s in rq["suppliers"] if supplier_id and s["supplier_id"] == supplier_id), None)
    quote = pur.rfq_supplier_quote(doc_id, chosen["supplier_id"]) if chosen and chosen["quotation_id"] else None
    items, asked, total = [], [], None
    for l in rq["items"]:
        line = {"code": l["item_code"], "name": l["item_name"], "qty": l["qty_requested"],
                "price": None, "amount": None, "note": ""}
        asked.append({"code": l["item_code"], "qty": l["qty_requested"]})      # what the signature covers
        if quote is not None:
            q = quote.get(l["item_id"])
            if q is None:
                line["note"] = "Not offered"
            else:
                taken = min(q["qty_offered"], l["qty_requested"])
                line["price"] = q["unit_price"]
                line["amount"] = str(pur._amount(taken, q["unit_price"]).quantize(Decimal("0.01")))
                if q["qty_offered"] < l["qty_requested"]:
                    line["note"] = "Offers %d of %d" % (q["qty_offered"], l["qty_requested"])
        items.append(line)
    if quote is not None:
        total = str(sum((Decimal(l["amount"]) for l in items if l["amount"] is not None), Decimal("0")).quantize(Decimal("0.01")))
    deadline = _date(rq["reply_deadline"])
    if chosen:
        contact = " · ".join(x for x in (chosen.get("contact_person"), chosen.get("email")) if x)
        party_to = ("To", chosen["supplier_name"] + ("  (Attn: %s)" % contact if contact else ""))
        recipients = []
    else:
        party_to = ("To", "Invited suppliers")
        recipients = [("Sent to", ", ".join(s["supplier_name"] for s in rq["suppliers"]) or "–")]
    meta = [("REF no.", rq["ref_no"]), ("Reply by", deadline or "–"),
            ("Created by", rq["created_by_name"])] + recipients
    return _document(
        "RFQ", TITLES["RFQ"], rq["rfq_no"], None, rq.get("sent_at") or rq.get("created_at"),
        ("From", "Warehouse %s — %s" % (rq["warehouse"], rq["warehouse_name"])),
        party_to, meta, items, user, doc_id, show_prices=True, total=total,
        notes=[rq.get("terms")],
        fingerprint={"to": ("To", "Suppliers"), "items": asked, "total": None},
        extra={"ref_id": rq["ref_id"], "supplier_id": chosen["supplier_id"] if chosen else None,
               "supplier_name": chosen["supplier_name"] if chosen else None})


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
    items = []
    for l in ts["lines"]:
        received = l.get("qty_received")
        requested = l["qty_requested"]
        items.append({
            "code": l["item_code"], "name": l["item_name"], "qty": l["qty_released"],
            "requested": requested, "dispatched": l["qty_released"], "received": received,
            "diff": None if received is None else received - requested,
            "note": _line_note(l.get("dispatch_note"), l.get("variance_reason")),
        })
    ar = ts.get("ar")
    status = ts["status"]
    if ar and ar["has_variance"] and status == "delivered":
        status = "delivered_with_variance"
    return _document(
        "TS", TITLES["TS"], ts["ts_no"], status,
        ts.get("released_at") or ts.get("created_at"),
        ("From", "%s — %s" % (ts["from_code"], ts["from_name"])),
        ("To", "%s — %s" % (ts["shop_code"], ts["shop_name"])),
        [("RF no.", ts["rf_no"]),
         ("Driver", ts["driver_name"] or "–"),
         ("Vehicle", ts["vehicle_plate"] or "–"),
         ("Driver contact", ts.get("driver_contact") or "–")],
        items, user, doc_id,
        qty_columns=[("requested", "Requested"), ("dispatched", "Dispatched"),
                     ("received", "Received")],
        qty_totals=_qty_totals(items, ["requested", "dispatched", "received", "diff"]),
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
    rows = _b().all(
        "SELECT ari.*, i.item_code, i.item_name, ti.dispatch_note, "
        "COALESCE(ti.qty_needed, x.qty_requested, ari.qty_expected) AS qty_requested "
        "FROM acknowledgment_receipt_items ari JOIN items i ON i.id = ari.item_id "
        "LEFT JOIN transfer_slip_items ti ON ti.id = ari.ts_item_id "
        "LEFT JOIN requisition_items x ON x.id = ti.rf_item_id "
        "WHERE ari.ar_id = :i ORDER BY i.item_name", i=doc_id)
    items = []
    for l in rows:
        requested = l["qty_requested"]
        items.append({
            "code": l["item_code"], "name": l["item_name"], "qty": l["qty_received"],
            "requested": requested, "received": l["qty_received"],
            "diff": l["qty_received"] - requested,
            "note": _line_note(l.get("dispatch_note"), l.get("variance_reason")),
        })
    diffs = [it["diff"] for it in items]
    return _document(
        "AR", TITLES["AR"], ar["ar_no"],
        "delivered_with_variance" if any(diffs) else "delivered",
        ar["received_at"], ("From", "%s — %s" % (ts["from_code"], ts["from_name"])),
        ("To", "%s — %s" % (ts["shop_code"], ts["shop_name"])),
        [("TS no.", ar["ts_no"]),
         ("Received by", ar["received_by_name"]),
         ("Variance", dop.variance_label(diffs))],
        items, user, doc_id,
        qty_columns=[("requested", "Requested"), ("received", "Received")],
        qty_totals=_qty_totals(items, ["requested", "received", "diff"]),
        notes=[ar.get("remarks")],
        system_note="Generated automatically when the shop confirms delivery.")


def draft_ar(ts_id, user):
    """The AR as it will read once the shop confirms: no signature, nothing counted yet.

    Shown on the receiving screen so the shop sees the document it is about to
    acknowledge. doc_id is 0: there is no saved AR row until confirmation.
    """
    ts = dop.get_transfer(ts_id, user)
    if ts is None or ts["status"] != "in_transit":
        return None
    items = [{"code": l["item_code"], "name": l["item_name"], "qty": l["qty_released"],
              "requested": l["qty_requested"], "received": None, "diff": None,
              "note": _line_note(l.get("dispatch_note"))}
             for l in ts["lines"]]
    return _document(
        "AR", TITLES["AR"], inv.peek_doc_no("AR"), "pending", dt.date.today().isoformat(),
        ("From", "%s — %s" % (ts["from_code"], ts["from_name"])),
        ("To", "%s — %s" % (ts["shop_code"], ts["shop_name"])),
        [("TS no.", ts["ts_no"]), ("Variance", "–")],
        items, user, 0,
        qty_columns=[("requested", "Requested"), ("received", "Received")],
        qty_totals=_qty_totals(items, ["requested", "received", "diff"]),
        system_note="Generated automatically when the shop confirms delivery.")


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


LOADERS = {"RF": _load_rf, "REF": _load_ref, "RFQ": _load_rfq, "PO": _load_po, "TS": _load_ts,
           "AR": _load_ar, "RM": _load_rm, "DR": _load_dr}


def load(doc_type, doc_id, user, supplier_id=None):
    """supplier_id only matters for an RFQ (the copy addressed to one supplier)."""
    doc_type = (doc_type or "").upper()
    loader = LOADERS.get(doc_type)
    if loader is None:
        return None
    if doc_type == "RFQ":
        return loader(doc_id, user, supplier_id=supplier_id)
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
