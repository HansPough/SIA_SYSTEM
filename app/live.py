"""Live state for signed-in users.

One small JSON payload powers everything that must change without a page refresh:

* the bell and the sidebar badges ("something is waiting for you"),
* the revision number the browser compares to notice work done by another user,
* the "Live" light in the top bar.

The revision is the newest audit row (log in/out excluded) plus the attention
total. A login by somebody else therefore changes nothing on your screen, while
a real save by a colleague changes it at once even if their count is unchanged.

Refreshing a whole page is safe only where the user is not in the middle of
typing, so `is_live_page()` marks the data-entry screens that must never be
replaced under the user's hands.
"""
from . import inventory_service as inv
from .roles import MENU

# Screens with fields the user is filling in: never auto-replace these.
LIVE_OFF_ENDPOINTS = {
    "auth.login", "auth.signature",
    "inventory.count_sheet", "inventory.rfqueue_review", "inventory.adjustment_review",
    "purchasing.approval_detail", "purchasing.ref_compare", "purchasing.rfq_new",
    "purchasing.quotation_new", "purchasing.alert_dismiss",
    "requisition.rf_new", "requisition.alert_dismiss",
    "delivery.transfer_new", "delivery.transfer_assign", "delivery.transfer_load",
    "delivery.transfer_release", "delivery.transfer_driver_sign",
    "delivery.delivery_receive", "delivery.receiving_check", "delivery.discrepancy_detail",
}
LIVE_OFF_SUFFIXES = ("_new", "_edit", "_cancel", "_review", "_sign", "_dismiss")


def is_live_page(endpoint):
    """True on list and read-only detail screens that can refresh themselves."""
    if not endpoint or endpoint in LIVE_OFF_ENDPOINTS:
        return False
    return not endpoint.endswith(LIVE_OFF_SUFFIXES)


def revision():
    """A token that changes when another user saves something. Never raises."""
    try:
        row = inv.backend().one(
            "SELECT COALESCE(MAX(id), 0) AS n FROM audit_log "
            "WHERE action NOT IN ('login', 'logout')")
        return int(row["n"]) if row else 0
    except Exception:
        try:
            inv.rollback()
        except Exception:
            pass
        return 0


def groups(role, counts, url_for):
    """The sidebar as data: group label, group total, and one entry per link."""
    out = []
    for label, endpoint in MENU.get(role, []):
        if label == "--":
            out.append({"label": endpoint, "count": 0, "items": []})
        elif out:
            n = int(counts.get(endpoint, 0) or 0)
            out[-1]["items"].append({"label": label, "url": url_for(endpoint), "n": n})
            out[-1]["count"] += n
    return out


def attention(role, counts, url_for):
    """Only the entries with a number, in menu order."""
    out = []
    for label, endpoint in MENU.get(role, []):
        n = int(counts.get(endpoint, 0) or 0)
        if label != "--" and n > 0:
            out.append({"label": label, "url": url_for(endpoint), "n": n})
    return out
