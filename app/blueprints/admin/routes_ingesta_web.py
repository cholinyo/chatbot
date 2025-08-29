# app/blueprints/admin/routes_ingesta_web.py
from __future__ import annotations

import os
import shlex
import subprocess
import sys
from pathlib import Path
from flask import (
    Blueprint,
    render_template,
    request,
    redirect,
    url_for,
    flash,
    send_file,
    abort,
    current_app,
)

import app.extensions.db as db
from app.models import Source, IngestionRun

bp_ingesta_web = Blueprint("ingesta_web", __name__, url_prefix="/admin/ingesta-web")

RUNS_ROOT = Path("data/processed/runs")
RUNS_ROOT.mkdir(parents=True, exist_ok=True)


def _default_config():
    return {
        "strategy": "sitemap",
        "depth": 1,
        "allowed_domains": [],
        "include": [],
        "exclude": [],
        "robots_policy": "strict",
        "ignore_robots_for": [],
        "rate_per_host": 1.0,
        "timeout": 15,
        "force_https": True,
        "user_agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36",
        "max_pages": 100,
    }


@bp_ingesta_web.route("/", methods=["GET"])
def index():
    with db.get_session() as s:
        sources = (
            s.query(Source)
            .filter(Source.type == "web")
            .order_by(Source.id.desc())
            .all()
        )
        runs = s.query(IngestionRun).order_by(IngestionRun.id.desc()).limit(20).all()

    return render_template(
        "admin/ingesta_web.html",
        sources=sources,
        runs=runs,
        cfg_defaults=_default_config(),
    )


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
    cfg["allowed_domains"] = [
        d.strip()
        for d in (request.form.get("allowed_domains") or "").split(",")
        if d.strip()
    ]
    cfg["include"] = [
        s.strip() for s in (request.form.get("include") or "").splitlines() if s.strip()
    ]
    cfg["exclude"] = [
        s.strip() for s in (request.form.get("exclude") or "").splitlines() if s.strip()
    ]
    cfg["robots_policy"] = request.form.get("robots_policy") or "strict"
    cfg["ignore_robots_for"] = [
        d.strip()
        for d in (request.form.get("ignore_robots_for") or "").split(",")
        if d.strip()
    ]
    cfg["rate_per_host"] = float(request.form.get("rate_per_host") or 1.0)
    cfg["timeout"] = int(request.form.get("timeout") or 15)
    cfg["force_https"] = bool(request.form.get("force_https"))
    cfg["user_agent"] = request.form.get("user_agent") or _default_config()["user_agent"]
    cfg["max_pages"] = int(request.form.get("max_pages") or 100)

    with db.get_session() as s:
        if src_id:
            src = s.get(Source, int(src_id))
            if not src:
                flash("Source no encontrado.", "danger")
                return redirect(url_for("ingesta_web.index"))
            src.url = url
            src.type = "web"
            src.config = {**(src.config or {}), **cfg}
        else:
            src = Source(url=url, type="web", config=cfg)
            s.add(src)
        s.commit()

    flash("Fuente guardada", "success")
    return redirect(url_for("ingesta_web.index"))


@bp_ingesta_web.route("/run/<int:source_id>", methods=["POST"])
def run(source_id: int):
    # crear run + leer cfg y seed_url
    with db.get_session() as s:
        src = s.get(Source, source_id)
        if not src:
            flash("Source no encontrado.", "danger")
            return redirect(url_for("ingesta_web.index"))

        cfg = {**_default_config(), **(src.config or {})}
        run = IngestionRun(source_id=src.id, status="running", meta={"web_config": cfg})
        s.add(run)
        s.commit()  # asegura run.id

        seed_url = src.url  # usar fuera de sesión

    # localizar script
    candidates = [
        Path("scripts/ingest_web.py"),
        Path("ingest_web.py"),
    ]
    script_path = next((p for p in candidates if p.exists()), None)
    if not script_path:
        _update_run_meta(
            run_id=run.id,
            status="error",
            stdout=f"[NO_SCRIPT_FOUND] Ninguno de: {', '.join(str(p) for p in candidates)}",
            extra={"cmd": "(sin comando)"},
        )
        flash("No encuentro el script de ingesta web. Revisa la ruta.", "danger")
        return redirect(url_for("ingesta_web.index"))

    # construir comando
    py = sys.executable
    args = [
        py,
        str(script_path),
        "--seed",
        seed_url,
        "--strategy",
        cfg.get("strategy", "sitemap"),
        "--max-pages",
        str(cfg.get("max_pages", 100)),
        "--timeout",
        str(cfg.get("timeout", 15)),
        "--rate",
        str(cfg.get("rate_per_host", 1.0)),
        "--user-agent",
        cfg.get("user_agent", _default_config()["user_agent"]),
        "--dump-html",
        "--preview",
        "--verbose",
    ]
    if cfg.get("force_https"):
        args.append("--force-https")

    policy = cfg.get("robots_policy", "strict")
    if policy == "ignore":
        args.append("--no-robots")
    else:
        args += ["--robots-policy", policy]
        if policy == "list" and cfg.get("ignore_robots_for"):
            args += ["--ignore-robots-for", ",".join(cfg["ignore_robots_for"])]

    if cfg.get("strategy") == "requests":
        args += ["--depth", str(cfg.get("depth", 1))]

    if cfg.get("allowed_domains"):
        args += ["--allowed-domains", ",".join(cfg["allowed_domains"])]

    for pat in cfg.get("include", []):
        args += ["--include", pat]
    for pat in cfg.get("exclude", []):
        args += ["--exclude", pat]

    # ejecutar con cwd en raíz del proyecto (para imports relativos/artefactos coherentes)
    project_root = Path(current_app.root_path).parent
    cmd_shown = " ".join(shlex.quote(a) for a in args)
    try:
        proc = subprocess.run(
            args,
            capture_output=True,
            text=True,
            check=False,
            cwd=str(project_root),
            env={**os.environ},
        )
        out = (proc.stdout or "") + ("\n" + proc.stderr if proc.stderr else "")

        # extraer run_dir del stdout
        run_dir = None
        for line in out.splitlines():
            if line.startswith("[RUN_DIR]"):
                run_dir = line.replace("[RUN_DIR]", "").strip()
                break

        extra = {"returncode": proc.returncode, "cmd": cmd_shown}
        if run_dir:
            extra["run_dir"] = run_dir

        _update_run_meta(
            run_id=run.id,
            status=("done" if proc.returncode == 0 else "error"),
            stdout=(out[-20000:] or "(sin salida del proceso)"),
            extra=extra,
        )

        flash(
            "Ingesta finalizada con éxito." if proc.returncode == 0
            else "Ingesta finalizada con error. Revisa la salida.",
            "success" if proc.returncode == 0 else "danger",
        )
    except Exception as e:
        _update_run_meta(
            run_id=run.id,
            status="error",
            stdout=f"[exception] {e}",
            extra={"cmd": cmd_shown},
        )
        flash(f"Fallo al ejecutar la ingesta: {e}", "danger")

    return redirect(url_for("ingesta_web.index"))


def _update_run_meta(run_id: int, *, status: str, stdout: str, extra: dict | None = None) -> None:
    """Actualizar meta/estado del run con trazas útiles (stdout, returncode, cmd, run_dir...)."""
    with db.get_session() as s:
        run_db = s.get(IngestionRun, run_id)
        if not run_db:
            return
        meta = run_db.meta or {}
        meta["stdout"] = stdout
        if extra:
            meta.update(extra)
        run_db.meta = meta
        run_db.status = status
        s.commit()


def _normalize_artifact_path(relpath: str) -> Path:
    """
    Normaliza un 'relpath' que puede venir:
      - relativo a RUNS_ROOT (p.ej. 'web_sitemap_.../fetch_index.json')
      - prefijado por RUNS_ROOT ('data/processed/runs/web_.../fetch_index.json')
      - incluso absoluto en Windows (con backslashes)
    y devuelve la ruta absoluta FINAL asegurando que permanece dentro de RUNS_ROOT.
    """
    base = RUNS_ROOT.resolve()
    rel = relpath.replace("\\", "/")

    p = Path(rel)
    if p.is_absolute():
        cand = p.resolve()
    else:
        rel_no_base = rel
        base_str = str(base).replace("\\", "/")
        if rel_no_base.startswith(base_str):
            rel_no_base = rel_no_base[len(base_str) :].lstrip("/")
        cand = (base / rel_no_base).resolve()

    if not str(cand).startswith(str(base)):
        raise PermissionError("Ruta fuera del directorio de runs")

    return cand


@bp_ingesta_web.route("/artifact/<path:relpath>")
def artifact(relpath: str):
    try:
        file_path = _normalize_artifact_path(relpath)
    except PermissionError:
        abort(403)
    except Exception:
        flash("Ruta de artefacto inválida.", "warning")
        return redirect(url_for("ingesta_web.index"))

    if not file_path.exists() or not file_path.is_file():
        flash("Archivo no encontrado.", "warning")
        return redirect(url_for("ingesta_web.index"))

    return send_file(file_path, as_attachment=True)
