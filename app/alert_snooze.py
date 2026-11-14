"""Snooze: make an alert silent for a few days without closing it.

A snoozed alert stays OPEN. It does not count in the sidebar, the bell or the dashboard until the
snooze date passes; then it counts again by itself. Nothing is dismissed and no reason is needed.
The same rules serve the shop manager's low-stock alerts (which lead to an RF) and the warehouse
manager's restock alerts (which lead to a REF).
"""
import datetime as dt

from . import inventory_service as inv
from .inventory_service import InventoryError

SNOOZE_CHOICES = ((1, "1 day"), (3, "3 days"), (7, "1 week"), (14, "2 weeks"), (30, "1 month"))
ALLOWED_DAYS = tuple(d for d, _ in SNOOZE_CHOICES)

# SQL fragments (alias n = notifications). CURRENT_DATE works in PostgreSQL and SQLite.
AWAKE = "(n.snoozed_until IS NULL OR n.snoozed_until <= CURRENT_DATE)"
ASLEEP = "(n.snoozed_until IS NOT NULL AND n.snoozed_until > CURRENT_DATE)"


def is_snoozed(row):
    """True when this alert is open but silent right now."""
    until = row.get("snoozed_until")
    return bool(row.get("status") == "active" and until and str(until)[:10] > dt.date.today().isoformat())


def _own_alert(user, alert_id, notif_type):
    alert = inv.backend().one("SELECT * FROM notifications WHERE id = :i AND notif_type = :t", i=alert_id, t=notif_type)
    if alert is None or alert["location_id"] != user.get("location_id"):
        raise InventoryError("Alert not found for your location.")
    if alert["status"] != "active":
        raise InventoryError("This alert is already %s, so it cannot be snoozed." % alert["status"])
    return alert


def snooze(user, alert_id, notif_type, days):
    """Silence an open alert for `days` days. Returns the date it wakes up."""
    try:
        days = int(str(days).strip())
    except (TypeError, ValueError):
        raise InventoryError("Choose how long to snooze it.")
    if days not in ALLOWED_DAYS:
        raise InventoryError("Choose how long to snooze it.")
    alert = _own_alert(user, alert_id, notif_type)
    until = dt.date.today() + dt.timedelta(days=days)
    inv.backend().execute("UPDATE notifications SET snoozed_until = :u, snoozed_by = :b WHERE id = :i",
                          u=until.isoformat(), b=user["id"], i=alert_id)
    inv.write_audit(user["id"], "snooze", "notifications", alert_id,
                    old={"snoozed_until": str(alert.get("snoozed_until") or "")}, new={"snoozed_until": until.isoformat()})
    return until


def wake(user, alert_id, notif_type):
    """Make a snoozed alert count again right now."""
    alert = _own_alert(user, alert_id, notif_type)
    if not is_snoozed(alert):
        raise InventoryError("This alert is not snoozed.")
    inv.backend().execute("UPDATE notifications SET snoozed_until = NULL, snoozed_by = NULL WHERE id = :i", i=alert_id)
    inv.write_audit(user["id"], "wake", "notifications", alert_id,
                    old={"snoozed_until": str(alert["snoozed_until"])[:10]}, new={"snoozed_until": None})
