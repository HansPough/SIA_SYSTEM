import csv
import datetime as dt
import io

from flask import abort, current_app, Response, request, Blueprint, redirect, render_template, session, url_for
from ..roles import roles_required
from .. import dashboard_ops
from .. import document_ops as docs
from .. import signing
from .. import inventory_ops as ops
from .. import report_ops
from ..session_user import current_user, scope_location

bp = Blueprint("dashboard", __name__)
signing_roles = ("owner", "shop_manager", "warehouse_manager")


@bp.route("/")
def index():
    role = session.get("role")
    if role is None:
        return redirect(url_for("auth.login"))
    stats = dash = None
    to_sign, no_signature = 0, False
    try:
        user = current_user()
        if role in signing_roles:
            no_signature = not signing.user_has_signature(user["id"])
        if role in ("owner", "warehouse_manager"):
            to_sign = len(docs.pending_signatures(user))
    except Exception as e:
        current_app.logger.warning("Signature status unavailable: %s", e)
    try:
        stats = ops.dashboard_stats(scope_location(current_user()))
        dash = dashboard_ops.summary(current_user())
    except Exception as e:  # no database yet: show the dashboard with zeros
        current_app.logger.warning("Dashboard numbers unavailable: %s", e)
    return render_template("dashboard/%s.html" % role, stats=stats, dash=dash,
                           to_sign=to_sign, no_signature=no_signature)


@bp.route("/reports")
@roles_required("owner")
def reports():
    return render_template("dashboard/reports.html", reports=report_ops.REPORTS)


@bp.route("/reports/<name>")
@roles_required("owner")
def report(name):
    result = report_ops.run(name, request.args)
    if result is None:
        abort(404)
    cols, rows = result
    if request.args.get("format") == "csv":
        buf = io.StringIO()
        w = csv.writer(buf)
        w.writerow(cols)
        for r in rows:
            w.writerow(["" if r.get(c) is None else r.get(c) for c in cols])
        resp = Response("\ufeff" + buf.getvalue(), mimetype="text/csv")      # BOM so Excel reads it as UTF-8
        resp.headers["Content-Disposition"] = "attachment; filename=%s_%s.csv" % (name, dt.date.today().isoformat())
        return resp
    d_from, d_to = report_ops._dates(request.args)
    return render_template("dashboard/report.html", name=name, title=report_ops.REPORTS[name][0], cols=cols,
                           rows=rows[:report_ops.HTML_LIMIT], total=len(rows), limit=report_ops.HTML_LIMIT,
                           locations=ops.list_locations(), f=request.args, d_from=d_from, d_to=d_to,
                           has_dates=name in ("movements", "sales", "adjustments"),
                           has_category=name == "stock")
