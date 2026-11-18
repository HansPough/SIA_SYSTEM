"""GET /api/live: the bell, sidebar badges and revision the browser polls."""
import datetime as dt

from flask import Blueprint, jsonify, session, url_for

from .. import live
from .. import nav_ops
from ..session_user import current_user

bp = Blueprint("live", __name__, url_prefix="/api")


@bp.route("/live")
def state():
    if not session.get("user_id"):
        return jsonify({"ok": False, "login": True}), 401
    user = current_user()
    try:
        counts = nav_ops.counts(user)
    except Exception:
        # Do not blank the bells because one query failed: the browser keeps what it has.
        return jsonify({"ok": False, "error": "unavailable"}), 503
    total = sum(int(n or 0) for n in counts.values())
    return jsonify({
        "ok": True,
        "rev": "%d:%d" % (live.revision(), total),
        "total": total,
        "attention": live.attention(user["role"], counts, url_for),
        "groups": live.groups(user["role"], counts, url_for),
        "time": dt.datetime.now().strftime("%H:%M"),
    })
