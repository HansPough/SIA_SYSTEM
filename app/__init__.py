from urllib.parse import urlparse
import datetime
import hashlib
import os
from pathlib import Path
import traceback

from flask import Flask, abort, g, request, session, render_template, url_for

from .config import Config
from .extensions import db
from .roles import ALSO_ACTIVE, MENU, ROLE_LABELS
from .statuses import label as status_label, tone as status_tone
from . import live as live_state


def create_app():
    app = Flask(__name__)
    app.config.from_object(Config)
    db.init_app(app)
    # Updated styling and navigation must get new URLs after a deployment.
    asset_versions = {
        name: hashlib.sha256((Path(app.static_folder) / name).read_bytes()).hexdigest()[:12]
        for name in ("css/style.css", "js/app.js", "js/live.js")
    }

    from .blueprints import (auth, dashboard, requisition, inventory, purchasing,
                             delivery, admin, documents, live as live_bp)
    for module in (auth, dashboard, requisition, inventory, purchasing, delivery, admin,
                   documents, live_bp):
        app.register_blueprint(module.bp)

    @app.before_request
    def protect_session_and_posts():
        """Keep the login cookie permanent for the configured lifetime and block
        cross-site form posts. Flask has no built-in CSRF, so for every POST we
        require a same-host Origin (or Referer) when the browser sends one.
        Requests without either header (curl, tests, some old browsers) are
        allowed; SameSite=Lax on the session cookie is the second layer."""
        if session.get("user_id"):
            session.permanent = True
        if request.method == "POST":
            source = request.headers.get("Origin") or request.headers.get("Referer")
            if source:
                host = urlparse(source).netloc
                if host and host != request.host:
                    abort(400)

    def _nav_counts():
        """Sidebar badge numbers for the logged-in user, worked out once per request.
        A failing query must never break a page, so any error just means no badges."""
        if not session.get("user_id"):
            return {}
        cached = getattr(g, "_nav_counts", None)
        if cached is not None:
            return cached
        counts = {}
        try:
            from . import inventory_service as inv
            from . import nav_ops
            from .session_user import current_user
            counts = nav_ops.counts(current_user())
        except Exception:
            app.logger.warning("Navigation counts unavailable", exc_info=True)
            try:
                inv.rollback()
            except Exception:
                pass
        g._nav_counts = counts
        return counts

    def _live_rev():
        """The token the browser compares so other users' saves appear by themselves."""
        cached = getattr(g, "_live_rev", None)
        if cached is None:
            cached = live_state.revision()
            g._live_rev = cached
        return cached

    @app.context_processor
    def inject_layout():
        from .nav_ops import BELL_WORDS
        role = session.get("role")
        # Pick the most specific menu URL so detail and edit screens keep their
        # parent navigation item highlighted (including nested create pages).
        entries = MENU.get(role, [])
        counts = _nav_counts()
        menu_endpoints = {endpoint for label, endpoint in entries if label != "--"}
        matching = [(endpoint, url_for(endpoint)) for label, endpoint in entries if label != "--"]
        for parent, extras in ALSO_ACTIVE.items():          # e.g. the variance tab keeps "Discrepancies" lit
            if parent in menu_endpoints:
                matching += [(parent, url_for(extra)) for extra in extras if extra not in menu_endpoints]
        matching = [(endpoint, path) for endpoint, path in matching
                    if request.path == path or (path != "/" and request.path.startswith(path + "/"))]
        active_menu = max(matching, key=lambda entry: len(entry[1]))[0] if matching else None
        groups = []
        for label, endpoint in entries:
            if label == "--":
                groups.append({"label": endpoint, "items": []})
            elif groups:
                groups[-1]["items"].append((label, endpoint))
        for g_ in groups:
            g_["count"] = sum(counts.get(endpoint, 0) for _l, endpoint in g_["items"])
        attention = [(BELL_WORDS.get(endpoint, label), endpoint, counts[endpoint]) for label, endpoint in entries
                     if label != "--" and counts.get(endpoint)]
        return {
            "asset_versions": asset_versions,
            "menu_groups": groups,
            "nav_counts": counts,
            "attention": attention,
            "attention_total": sum(n for _l, _e, n in attention),
            "live_rev": "%d:%d" % (_live_rev(), sum(counts.values())),
            "live_page": live_state.is_live_page(request.endpoint),
            "active_menu": active_menu,
            "current_role": role,
            "role_label": ROLE_LABELS.get(role, ""),
            "menu": MENU.get(role, []),
            "role_labels": ROLE_LABELS,
            "current_endpoint": request.endpoint,
            "status_tone": status_tone,
            "status_label": status_label,
            "dev_login": app.config.get("DEV_LOGIN", False),
            "company": app.config.get("COMPANY_NAME", "3T Tire Supply"),
            "my_user": {"id": session.get("user_id"), "name": session.get("user_name")},
        }

    from .alert_snooze import SNOOZE_CHOICES
    app.jinja_env.globals["SNOOZE_CHOICES"] = SNOOZE_CHOICES
    # Globals (not only context values) so imported macros can use them too.
    app.jinja_env.globals["status_tone"] = status_tone
    app.jinja_env.globals["status_label"] = status_label

    @app.template_global("discrepancy_counts")
    def discrepancy_counts():
        """Open items on the two Discrepancies tabs: from suppliers (DR) and to shops (variances)."""
        from . import delivery_ops as dop
        from . import inventory_service as inv
        from .session_user import current_user
        out = {"suppliers": 0, "shops": 0}
        try:
            user = current_user()
            out["shops"] = dop.list_shop_variances(user, "open")["total"]
            out["suppliers"] = inv.backend().one(
                "SELECT COUNT(*) AS n FROM discrepancy_reports WHERE status IN ('open', 'follow_up')")["n"]
        except Exception:
            inv.rollback()
        return out

    @app.template_global("load_document")
    def load_document(doc_type, doc_id, supplier_id=None):
        """The printable document for a form, or None. Every form page shows its document first."""
        from . import document_ops as docs
        from .session_user import current_user
        try:
            return docs.load(doc_type, doc_id, current_user(), supplier_id=supplier_id)
        except Exception:
            app.logger.warning("Document %s %s unavailable", doc_type, doc_id, exc_info=True)
            return None

    @app.template_global("load_lifecycle")
    def load_lifecycle(doc_type, doc_id):
        """The history shown in Progress and details (created, sent, signed, ...)."""
        from . import document_ops as docs
        try:
            return docs.lifecycle(doc_type, doc_id)
        except Exception:
            app.logger.warning("Lifecycle %s %s unavailable", doc_type, doc_id, exc_info=True)
            return []

    @app.template_global("signer_info")
    def signer_info():
        """Who a signature button will sign as: read from the account, never typed."""
        from . import inventory_service as inv
        from .roles import ROLE_LABELS
        uid = session.get("user_id")
        row = None
        if uid:
            try:
                row = inv.backend().one("SELECT full_name, signature_data FROM users WHERE id = :i", i=uid)
            except Exception:
                inv.rollback()
        return {"name": (row or {}).get("full_name") or session.get("user_name") or "",
                "role_label": ROLE_LABELS.get(session.get("role"), ""),
                "signature_data": (row or {}).get("signature_data")}

    @app.template_filter("po_status")
    def po_status(value):
        """A new PO is already approved by the owner: show 'Ready to send', not 'Draft'."""
        value = str(value or "")
        return "Ready to send" if value == "draft" else value.replace("_", " ").capitalize()

    @app.template_filter("money")
    def money_filter(value):
        try:
            return "{:,.2f}".format(float(value or 0))
        except (TypeError, ValueError):
            return "" if value is None else str(value)

    @app.template_filter("peso")
    def peso_filter(value):
        """A price with its currency, for the places where the amount is the point (quotes)."""
        try:
            return "\u20b1{:,.2f}".format(float(value))
        except (TypeError, ValueError):
            return "\u2013"

    @app.template_filter("day")
    def day_filter(value):
        """'Nov 25', or 'Nov 25, 2025' when it is not this year. A dash for nothing."""
        text = str(value or "")[:10]
        try:
            d = datetime.date.fromisoformat(text)
        except ValueError:
            return "\u2013"
        return d.strftime("%b ") + str(d.day) + ("" if d.year == datetime.date.today().year else ", %d" % d.year)

    @app.errorhandler(400)
    def bad_request(_e):
        return render_template("400.html"), 400

    @app.errorhandler(403)
    def forbidden(_e):
        return render_template("403.html"), 403

    @app.errorhandler(404)
    def not_found(_e):
        return render_template("404.html"), 404

    @app.errorhandler(500)
    def server_error(e):
        """Keep the raw traceback for support, even when debug is off."""
        original = getattr(e, "original_exception", e)
        try:
            os.makedirs(app.instance_path, exist_ok=True)
            with open(os.path.join(app.instance_path, "error.log"), "a", encoding="utf-8") as fh:
                fh.write("\n=== %s ===\n" % datetime.datetime.now().isoformat())
                traceback.print_exception(type(original), original,
                                          original.__traceback__, file=fh)
        except Exception:
            pass
        return render_template("500.html"), 500

    return app
