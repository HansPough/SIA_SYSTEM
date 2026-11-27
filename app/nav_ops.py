"""Attention counts for the sidebar badges and the bell.

Each number means "something is waiting for THIS user". They reuse the dashboard counts, so the sidebar,
the bell and the dashboard tiles always agree. Snoozed alerts are not counted. Read-only.
"""
from . import dashboard_ops
from . import delivery_ops as dop
from . import document_ops as docs
from . import inventory_service as inv


# What the bell says when the menu label alone would not tell you what is waiting.
BELL_WORDS = {
    "purchasing.po_list": "POs ready to send",
    "purchasing.ref_list": "REFs ready for RFQ",
}


def _count(sql, **p):
    return inv.backend().one(sql, **p)["n"]


def _open_variances(user):
    """Shop-side variances still open for this user (their shop, their warehouse, or all for the owner)."""
    return dop.list_shop_variances(user, "open")["total"]


def counts(user):
    """{menu endpoint: number waiting}, only entries above zero."""
    role, loc = user.get("role"), user.get("location_id")
    s = dashboard_ops.summary(user)
    out = {}

    def put(endpoint, n):
        if n and n > 0:
            out[endpoint] = int(n)

    if role == "owner":
        put("purchasing.approval_list", s["pending_approvals"])
        put("documents.to_sign", len(docs.pending_signatures(user)))
        put("delivery.discrepancy_list", s["open_discrepancies"] + _open_variances(user))
    elif role == "shop_manager":
        put("requisition.alert_list", s["active_alerts"])
        put("delivery.delivery_list", s["incoming_deliveries"])
        put("delivery.variance_list", _open_variances(user))
        if loc:
            put("inventory.adjustment_list", _count(
                "SELECT COUNT(*) AS n FROM stock_adjustments WHERE status = 'pending' AND location_id = :l", l=loc))
            put("inventory.issue_list", _count(
                "SELECT COUNT(*) AS n FROM stock_issues WHERE status = 'void_requested' AND location_id = :l", l=loc))
    elif role == "warehouse_manager":
        put("inventory.rfqueue_list", s["rf_queue"])
        put("documents.to_sign", len(docs.pending_signatures(user)))
        put("purchasing.alert_list", s["active_restock_alerts"])
        put("purchasing.ref_list", s["refs_ready_for_rfq"])
        put("purchasing.po_list", s["pos_ready_to_send"])
        put("delivery.transfer_list", s["transfers_to_move"])
        put("delivery.receiving_list", s["pos_waiting_delivery"])
        put("delivery.discrepancy_list", s["open_discrepancies"] + _open_variances(user))
        if loc:
            put("inventory.adjustment_list", _count(
                "SELECT COUNT(*) AS n FROM stock_adjustments WHERE status = 'pending' AND location_id = :l", l=loc))
    elif role == "warehouse_staff":
        put("delivery.receiving_list", s["pos_waiting_delivery"])
        put("delivery.transfer_list", s["assigned_transfers"])
    return out
