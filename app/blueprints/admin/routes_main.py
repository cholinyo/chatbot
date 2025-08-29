from __future__ import annotations
from flask import Blueprint, render_template, current_app
from markupsafe import escape

bp = Blueprint("admin_home", __name__, url_prefix="/")

@bp.route("/")
def index():
    return render_template("admin/index.html")

@bp.route("/admin/rutas")
def routes():
    rules = []
    for r in current_app.url_map.iter_rules():
        # Oculta endpoints internos estáticos si quieres
        if r.endpoint == 'static':
            continue
        rules.append({
            "endpoint": r.endpoint,
            "rule": str(r),
            "methods": ",".join(sorted(m for m in r.methods if m not in ("HEAD", "OPTIONS")))
        })
    rules.sort(key=lambda x: x["rule"])
    return render_template("admin/routes.html", rules=rules)
