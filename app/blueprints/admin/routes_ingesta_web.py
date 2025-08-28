from __future__ import annotations
import json, shlex, subprocess, sys
from pathlib import Path
from datetime import datetime
from flask import Blueprint, render_template, request, redirect, url_for, flash, send_file
from app.extensions.db import db
from app.models import Source, IngestionRun

bp_ingesta_web = Blueprint("ingesta_web", __name__, url_prefix="/admin/ingesta-web")

RUNS_ROOT = Path("data/processed/runs")

def _default_config():
    return {
        "strategy": "sitemap",           # "requests" | "sitemap"
        "depth": 1,                      # solo requests
        "allowed_domains": [],
        "include": [],
        "exclude": [],
        "robots_policy": "strict",       # strict|ignore|list
        "ignore_robots_for": [],
        "rate_per_host": 1.0,
        "timeout": 15,
        "force_https": True,
        "user_agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36",
        "max_pages": 100
    }

@bp_ingesta_web.route("/", methods=["GET"])
def index():
    sources = Source.query.filter_by(type="web").order_by(Source.id.desc()).all()
    runs = IngestionRun.query.order_by(IngestionRun.id.desc()).limit(20).all()
    return render_template("admin/ingesta_web.html", sources=sources, runs=runs, cfg_defaults=_default_config())

@bp_ingesta_web.route("/save", methods=["POST"])
def save():
    src_id = request.form.get("id")
    url = (request.form.get("url") or "").strip()
    if not url:
        flash("La URL es obligatoria", "danger")
        return redirect(url_for("ingesta_web.index"))

    cfg = _default_config()
    cfg["strategy"] = request.form.get("strategy") or "sitemap"
    cfg["depth"] = int(request.form.get("depth") or 1)
    cfg["allowed_domains"] = [d.strip() for d in (request.form.get("allowed_domains") or "").split(",") if d.strip()]
    cfg["include"] = [s.strip() for s in (request.form.get("include") or "").splitlines() if s.strip()]
    cfg["exclude"] = [s.strip() for s in (request.form.get("exclude") or "").splitlines() if s.strip()]
    cfg["robots_policy"] = request.form.get("robots_policy") or "strict"
    cfg["ignore_robots_for"] = [d.strip() for d in (request.form.get("ignore_robots_for") or "").split(",") if d.strip()]
    cfg["rate_per_host"] = float(request.form.get("rate_per_host") or 1.0)
    cfg["timeout"] = int(request.form.get("timeout") or 15)
    cfg["force_https"] = bool(request.form.get("force_https"))
    cfg["user_agent"] = request.form.get("user_agent") or _default_config()["user_agent"]
    cfg["max_pages"] = int(request.form.get("max_pages") or 100)

    if src_id:
        src = Source.query.get(int(src_id))
        if not src:
            flash("Source no encontrado.", "danger")
            return redirect(url_for("ingesta_web.index"))
        src.url = url
        src.type = "web"
        src.config = {**(src.config or {}), **cfg}
    else:
        src = Source(url=url, type="web", config=cfg)
        db.session.add(src)

    db.session.commit()
    flash("Fuente guardada", "success")
    return redirect(url_for("ingesta_web.index"))

@bp_ingesta_web.route("/run/<int:source_id>", methods=["POST"])
def run(source_id: int):
    src = Source.query.get_or_404(source_id)
    cfg = {**_default_config(), **(src.config or {})}
    # Crear run en DB
    run = IngestionRun(source_id=src.id, status="running", metadata={"web_config": cfg})
    db.session.add(run)
    db.session.commit()

    # Construir comando
    py = sys.executable
    script = Path("scripts/ingest_web.py").as_posix()
    args = [
        py, script,
        "--seed", src.url,
        "--strategy", cfg.get("strategy", "sitemap"),
        "--max-pages", str(cfg.get("max_pages", 100)),
        "--timeout", str(cfg.get("timeout", 15)),
        "--rate", str(cfg.get("rate_per_host", 1.0)),
        "--user-agent", cfg.get("user_agent", _default_config()["user_agent"]),
    ]
    if cfg.get("force_https"):
        args.append("--force-https")
    # robots
    policy = cfg.get("robots_policy", "strict")
    if policy == "ignore":
        args.append("--no-robots")  # compat directa
    else:
        args += ["--robots-policy", policy]
        if policy == "list" and cfg.get("ignore_robots_for"):
            args += ["--ignore-robots-for", ",".join(cfg["ignore_robots_for"])]
    # requests depth y dominios
    if cfg.get("strategy") == "requests":
        args += ["--depth", str(cfg.get("depth", 1))]
    if cfg.get("allowed_domains"):
        args += ["--allowed-domains", ",".join(cfg["allowed_domains"])]
    # include/exclude
    for pat in cfg.get("include", []):
        args += ["--include", pat]
    for pat in cfg.get("exclude", []):
        args += ["--exclude", pat]
    # artefactos y verbosidad
    args += ["--dump-html", "--preview", "--verbose"]

    # Ejecutar sincrónicamente (MVP)
    try:
        proc = subprocess.run(args, capture_output=True, text=True, check=False)
        out = (proc.stdout or "") + "\n" + (proc.stderr or "")

        # === NUEVO: extraer y guardar run_dir desde el stdout ===
        run_dir = None
        for line in out.splitlines():
            if line.startswith("[RUN_DIR]"):
                run_dir = line.replace("[RUN_DIR]", "").strip()
                break

        meta = run.metadata or {}
        meta["stdout"] = out[-20000:]  # última ventana de salida
        if run_dir:
            meta["run_dir"] = run_dir
        run.metadata = meta
        # ========================================================

        run.status = "done" if proc.returncode == 0 else "error"
        db.session.add(run)
        db.session.commit()
        if run.status == "error":
            flash("Ingesta finalizada con error. Revisa la salida.", "danger")
        else:
            flash("Ingesta finalizada con éxito.", "success")
    except Exception as e:
        run.status = "error"
        run.metadata = {**(run.metadata or {}), "exception": str(e)}
        db.session.add(run)
        db.session.commit()
        flash(f"Fallo al ejecutar la ingesta: {e}", "danger")

    return redirect(url_for("ingesta_web.index"))


@bp_ingesta_web.route("/preview/<int:run_id>")
def preview(run_id: int):
    run = IngestionRun.query.get_or_404(run_id)
    text = (run.metadata or {}).get("stdout", "")
    return render_template("admin/ingesta_web.html",
                           sources=Source.query.filter_by(type="web").order_by(Source.id.desc()).all(),
                           runs=IngestionRun.query.order_by(IngestionRun.id.desc()).limit(20).all(),
                           cfg_defaults=_default_config(),
                           preview=text)

@bp_ingesta_web.route("/artifact/<path:relpath>")
def artifact(relpath: str):
    # Sirve un archivo dentro de data/processed/runs/*
    file_path = RUNS_ROOT / relpath
    if not file_path.exists() or not file_path.is_file():
        flash("Archivo no encontrado.", "warning")
        return redirect(url_for("ingesta_web.index"))
    return send_file(file_path, as_attachment=True)
