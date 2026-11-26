"""Dashboard tile numbers for the parts of the app outside Inventory.

`inventory_ops.dashboard_stats()` already covers the Inventory tiles (stock,
ROP, adjustments, voids, sales). This module adds the Requisition, Purchasing
and Delivery counts, scoped the same way as each module's list screens, so the
dashboard does not show a hard-coded 0 that contradicts the list page.

Every function here only reads; the route commits nothing.
"""
from . import alert_snooze as snooze
from . import inventory_service as inv


def _b():
    return inv.backend()


def _count(sql, **params):
    return _b().one(sql, **params)["n"]


def summary(user):
    """Counts for the tiles on this user's dashboard. Missing keys are 0 in the template."""
    role = user["role"]
    loc = user["location_id"]
    out = {
        "pending_approvals": 0, "open_rfs": 0, "open_refs": 0, "open_pos": 0,
        "open_discrepancies": 0, "active_alerts": 0, "incoming_deliveries": 0,
        "rf_queue": 0, "transfers_to_move": 0, "assigned_transfers": 0,
        "pos_waiting_delivery": 0, "my_pending_adjustments": 0, "active_restock_alerts": 0,
    }
    if role == "owner":
        # Everything waiting for the owner's say: REFs to approve, and supplier choices to decide.
        out["pending_approvals"] = (
            _count("SELECT COUNT(*) AS n FROM purchase_approvals WHERE decision = 'pending'")
            + _count("SELECT COUNT(*) AS n FROM replenishments WHERE status = 'pending_owner'"))
        out["open_rfs"] = _count(
            "SELECT COUNT(*) AS n FROM requisitions "
            "WHERE status IN ('open', 'partially_delivered')")
        out["open_refs"] = _count(
            "SELECT COUNT(*) AS n FROM replenishments "
            "WHERE status NOT IN ('closed', 'cancelled')")
        out["open_pos"] = _count(
            "SELECT COUNT(*) AS n FROM purchase_orders "
            "WHERE status IN ('draft', 'sent', 'partially_received')")
        out["open_discrepancies"] = _count(
            "SELECT COUNT(*) AS n FROM discrepancy_reports "
            "WHERE status IN ('open', 'follow_up')")
    elif role == "shop_manager":
        out["active_alerts"] = _count(
            "SELECT COUNT(*) AS n FROM notifications n "
            "WHERE n.notif_type = 'low_stock' AND n.location_id = :l AND n.status = 'active' AND " + snooze.AWAKE, l=loc)
        out["open_rfs"] = _count(
            "SELECT COUNT(*) AS n FROM requisitions "
            "WHERE shop_id = :l AND status IN ('open', 'partially_delivered')", l=loc)
        out["incoming_deliveries"] = _count(
            "SELECT COUNT(*) AS n FROM transfer_slips "
            "WHERE to_location_id = :l AND status = 'in_transit'", l=loc)
    elif role == "warehouse_manager":
        out["rf_queue"] = _count(
            "SELECT COUNT(*) AS n FROM requisitions "
            "WHERE warehouse_id = :l AND status IN ('open', 'partially_delivered')", l=loc)
        out["transfers_to_move"] = _count(
            "SELECT COUNT(*) AS n FROM transfer_slips "
            "WHERE from_location_id = :l AND status IN ('draft', 'assigned', 'loaded')", l=loc)
        out["active_restock_alerts"] = _count(
            "SELECT COUNT(*) AS n FROM notifications n "
            "WHERE n.notif_type = 'restock' AND n.location_id = :l AND n.status = 'active' AND " + snooze.AWAKE, l=loc)
        out["open_refs"] = _count(
            "SELECT COUNT(*) AS n FROM replenishments "
            "WHERE warehouse_id = :l AND status NOT IN ('closed', 'cancelled')", l=loc)
        out["pos_waiting_delivery"] = _count(
            "SELECT COUNT(*) AS n FROM purchase_orders po "
            "JOIN replenishments r ON r.id = po.ref_id "
            "WHERE r.warehouse_id = :l AND po.status IN ('sent', 'partially_received')", l=loc)
        out["open_discrepancies"] = _count(
            "SELECT COUNT(*) AS n FROM discrepancy_reports d "
            "JOIN purchase_orders po ON po.id = d.po_id "
            "JOIN replenishments r ON r.id = po.ref_id "
            "WHERE r.warehouse_id = :l AND d.status IN ('open', 'follow_up')", l=loc)
    elif role == "warehouse_staff":
        out["pos_waiting_delivery"] = _count(
            "SELECT COUNT(*) AS n FROM purchase_orders po "
            "JOIN replenishments r ON r.id = po.ref_id "
            "WHERE r.warehouse_id = :l AND po.status IN ('sent', 'partially_received')", l=loc)
        out["assigned_transfers"] = _count(
            "SELECT COUNT(*) AS n FROM transfer_slips "
            "WHERE from_location_id = :l AND status = 'assigned'", l=loc)
        out["my_pending_adjustments"] = _count(
            "SELECT COUNT(*) AS n FROM stock_adjustments "
            "WHERE requested_by = :u AND status = 'pending'", u=user["id"])
    return out
