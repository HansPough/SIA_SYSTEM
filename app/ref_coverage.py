"""How much of each REF line is still to be ordered.

A REF line (item + quantity requested) can be:
  on a PO         ordered from a supplier (the PO is not cancelled)
  pending         part of an approval the owner has not decided yet
  dropped         given up on, with a reason (qty_dropped)
  open            none of the above: still needs a supplier

open = requested - dropped - on a PO - pending   (never below zero)

The REF is only "approved" (everything is covered) or "closed" (everything
covered AND received) when no open quantity is left. This is what lets one REF
be ordered from several suppliers, one PO each.
"""
from . import inventory_service as inv


def _b():
    return inv.backend()


def coverage(ref_id):
    """One dict per REF line with requested, dropped, on_po, pending, open and the PO numbers."""
    lines = _b().all(
        "SELECT ri.id AS ref_item_id, ri.item_id, ri.qty_requested, ri.qty_received, ri.qty_dropped, "
        "ri.dropped_reason, i.item_code, i.item_name "
        "FROM replenishment_items ri JOIN items i ON i.id = ri.item_id "
        "WHERE ri.ref_id = :r ORDER BY i.item_name", r=ref_id)
    on_po, po_nos = {}, {}
    for row in _b().all(
            "SELECT poi.replenishment_item_id AS rid, poi.qty_ordered AS q, po.po_no FROM purchase_order_items poi "
            "JOIN purchase_orders po ON po.id = poi.po_id WHERE po.ref_id = :r AND po.status <> 'cancelled'",
            r=ref_id):
        on_po[row["rid"]] = on_po.get(row["rid"], 0) + row["q"]
        po_nos.setdefault(row["rid"], []).append(row["po_no"])
    pending = {}
    for row in _b().all(
            "SELECT pai.replenishment_item_id AS rid, pai.qty AS q FROM purchase_approval_items pai "
            "JOIN purchase_approvals pa ON pa.id = pai.approval_id "
            "WHERE pa.ref_id = :r AND pa.decision = 'pending'", r=ref_id):
        pending[row["rid"]] = pending.get(row["rid"], 0) + row["q"]
    for ln in lines:
        rid = ln["ref_item_id"]
        ln["on_po"] = on_po.get(rid, 0)
        ln["pending"] = pending.get(rid, 0)
        ln["po_nos"] = po_nos.get(rid, [])
        ln["open"] = max(0, ln["qty_requested"] - ln["qty_dropped"] - ln["on_po"] - ln["pending"])
    return lines


def open_lines(ref_id):
    return [ln for ln in coverage(ref_id) if ln["open"] > 0]


def all_received(ref_id):
    """True when every line has received what was wanted (requested minus what was dropped)."""
    lines = coverage(ref_id)
    return bool(lines) and all(ln["qty_received"] >= ln["qty_requested"] - ln["qty_dropped"] for ln in lines)


def refresh_status(ref_id):
    """Set the REF status from what is covered. Call after submit, approve/reject, cancel PO, drop.

    for_approval  an approval is waiting for the owner
    approved      every line is covered by a PO (or dropped)
    closed        covered and everything received
    submitted /   POs exist or not, but some quantity is still open and needs a supplier
    rfq_sent
    """
    ref = _b().one("SELECT id, status FROM replenishments WHERE id = :r", r=ref_id)
    if ref is None or ref["status"] in ("draft", "pending_owner", "closed", "cancelled"):
        return ref["status"] if ref else None
    pending = _b().one("SELECT COUNT(*) AS n FROM purchase_approvals WHERE ref_id = :r AND decision = 'pending'",
                       r=ref_id)["n"]
    pos = _b().one("SELECT COUNT(*) AS n FROM purchase_orders WHERE ref_id = :r AND status <> 'cancelled'",
                   r=ref_id)["n"]
    has_rfq = _b().one("SELECT COUNT(*) AS n FROM rfqs WHERE ref_id = :r", r=ref_id)["n"] > 0
    still_open = bool(open_lines(ref_id))
    if pending:
        new = "for_approval"
    elif still_open:
        new = "rfq_sent" if has_rfq else "submitted"
    elif pos and all_received(ref_id):
        new = "closed"
    elif pos:
        new = "receiving" if ref["status"] == "receiving" else "approved"
    else:
        new = "rfq_sent" if has_rfq else "submitted"
    if new != ref["status"]:
        if new == "closed":
            _b().execute("UPDATE replenishments SET status = 'closed', closed_at = CURRENT_TIMESTAMP WHERE id = :r",
                         r=ref_id)
        else:
            _b().execute("UPDATE replenishments SET status = :s WHERE id = :r", s=new, r=ref_id)
    return new
