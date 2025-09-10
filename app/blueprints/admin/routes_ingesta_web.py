# -*- coding: utf-8 -*-
from __future__ import annotations

import json, os, shlex, subprocess, sys, logging, re, time
from logging.handlers import RotatingFileHandler
from dataclasses import dataclass
from pathlib import Path
from typing import Optional
from flask import (
    Blueprint, request, render_template, redirect, url_for, flash,
    send_file, abort, current_app
)

# ORM / tu app
from app.extensions import db as db
from app.models import Source, IngestionRun
from sqlalchemy.orm import selectinload

bp_ingesta_web = Blueprint("ingesta_web", __name__, url_prefix="/admin/ingesta-web")

RUNS_ROOT = Path("data/processed/runs")
RUNS_ROOT.mkdir(parents=True, exist_ok=True)
LOGS_DIR = Path("data/logs"); LOGS_DIR.mkdir(parents=True, exist_ok=True)
LOG_FILE = LOGS_DIR / "ingestion.log"


def _get_ingestion_logger() -> logging.Logger:
    logger = logging.getLogger("ingesta_web")
    logger.setLevel(logging.INFO)
    if not any(isinstance(h, RotatingFileHandler) and Path(getattr(h, "baseFilename", "")).resolve() == LOG_FILE.resolve()
               for h in logger.handlers):
        fh = RotatingFileHandler(LOG_FILE, maxBytes=2_000_000, backupCount=3, encoding="utf-8")
        fh.setLevel(logging.INFO)
        fh.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
        logger.addHandler(fh)
    return logger


def _default_config():
    return {
        "strategy": "sitemap",
        "depth": 2,
        "allowed_domains": [],
        "include": [],
        "exclude": [r"\.(png|jpg|jpeg|gif|css|js|pdf)$"],
        "robots_policy": "strict",
        "rate_per_host": 1.0,
        "timeout": 15,
        "force_https": True,
        "user_agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
        "max_pages": 100,
        # Selenium
        "driver": "chrome",
        "window_size": "1366,900",
        "render_wait_ms": 3000,
        "wait_selector": "",
        "no_headless": False,
        "scroll": False,
        "scroll_steps": 4,
        "scroll_wait_ms": 500,
    }


# ------------------------------- Vistas -------------------------------

@bp_ingesta_web.route("/", methods=["GET"])
def index():
    logger = _get_ingestion_logger()
    with db.get_session() as s:
        sources = s.query(Source).filter(Source.type=="web").order_by(Source.id.desc()).all()
        runs = (
            s.query(IngestionRun)
             .options(selectinload(IngestionRun.source))
             .join(Source, IngestionRun.source_id==Source.id)
             .filter(Source.type=="web")
             .order_by(IngestionRun.id.desc()).limit(20).all()
        )
    return render_template("admin/ingesta_web.html",
                           sources=sources, runs=runs, cfg_defaults=_default_config())


@bp_ingesta_web.route("/save", methods=["POST"])
def save():
    logger = _get_ingestion_logger()
    f = request.form
    seed = (f.get("seed") or f.get("url") or "").strip()
    name = (f.get("name") or "").strip()
    if not seed:
        flash("La URL es obligatoria", "danger")
        return redirect(url_for("ingesta_web.index"))

    cfg = _default_config()
    cfg.update({
        "strategy": f.get("strategy") or "sitemap",
        "depth": int(f.get("depth") or cfg["depth"]),
        "allowed_domains": [d.strip() for d in (f.get("allowed_domains") or "").replace("\n", ",").split(",") if d.strip()],
        "include": [s.strip() for s in (f.get("include") or "").splitlines() if s.strip()],
        "exclude": [s.strip() for s in (f.get("exclude") or "").splitlines() if s.strip()] or cfg["exclude"],
        "robots_policy": f.get("robots_policy") or "strict",
        "rate_per_host": float(f.get("rate_per_host") or cfg["rate_per_host"]),
        "timeout": int(f.get("timeout") or cfg["timeout"]),
        "force_https": bool(f.get("force_https") in ("1","true","on","yes")),
        "user_agent": f.get("user_agent") or cfg["user_agent"],
        "max_pages": int(f.get("max_pages") or cfg["max_pages"]),
        # Selenium
        "driver": f.get("driver") or cfg["driver"],
        "window_size": f.get("window_size") or cfg["window_size"],
        "render_wait_ms": int(f.get("render_wait_ms") or cfg["render_wait_ms"]),
        "wait_selector": f.get("wait_selector") or "",
        "no_headless": bool(f.get("no_headless") in ("1","true","on","yes")),
        "scroll": bool(f.get("scroll") in ("1","true","on","yes")),
        "scroll_steps": int(f.get("scroll_steps") or 4),
        "scroll_wait_ms": int(f.get("scroll_wait_ms") or 500),
    })

    with db.get_session() as s:
        src_id = f.get("id")
        if src_id:
            src = s.get(Source, int(src_id))
            if not src or src.type != "web":
                flash("Source no encontrado.", "danger")
                return redirect(url_for("ingesta_web.index"))
            src.type = "web"
            src.url = seed
            if name:
                src.name = name
            src.config = {**(src.config or {}), **cfg}
            action = "update"
        else:
            src = Source(url=seed, name=(name or None), type="web", config=cfg)
            s.add(src)
            action = "create"
        s.commit()

    logger.info("[INGEST_WEB] source_%s id=%s url=%s name=%s", action, src.id, src.url, src.name or "")
    flash("Fuente guardada", "success")
    return redirect(url_for("ingesta_web.index"))


@bp_ingesta_web.route("/run/<int:source_id>", methods=["POST"])
def run(source_id: int):
    logger = _get_ingestion_logger()
    with db.get_session() as s:
        src = s.get(Source, source_id)
        if not src or src.type != "web":
            flash("Fuente web no encontrada.", "danger")
            return redirect(url_for("ingesta_web.index"))
        cfg = {**(_default_config()), **(src.config or {})}
        run = IngestionRun(source_id=src.id, status="running", meta={"web_config": cfg})
        s.add(run); s.commit()
        seed_url = src.url

    runs_web_root = RUNS_ROOT / "web"; runs_web_root.mkdir(parents=True, exist_ok=True)
    run_dir = runs_web_root / f"run_{run.id}"; run_dir.mkdir(parents=True, exist_ok=True)

    # Persistir run_dir en meta
    with db.get_session() as s2:
        db_run = s2.get(IngestionRun, run.id)
        meta = db_run.meta or {}
        meta["run_dir"] = str(run_dir)
        db_run.meta = meta
        s2.add(db_run); s2.commit()

    logger.info("[INGEST_WEB] start run source_id=%s run_id=%s url=%s strategy=%s max_pages=%s",
                source_id, run.id, seed_url, cfg.get("strategy"), cfg.get("max_pages"))

    # Localizar script orquestador
    project_root = Path(current_app.root_path).parent
    script_candidates = [project_root / "scripts" / "ingest_web.py", project_root / "ingest_web.py"]
    script_path = next((p for p in script_candidates if p.exists()), None)
    if not script_path:
        (run_dir / "stdout.txt").write_text("[NO_SCRIPT_FOUND]\n", encoding="utf-8")
        _update_run_meta(run.id, status="error", stdout="[NO_SCRIPT_FOUND]", extra={"run_dir": str(run_dir)})
        flash("No encuentro el script de ingesta web.", "danger")
        return redirect(url_for("ingesta_web.index"))

    # Construir args como lista (seguro en Windows)
    py = sys.executable
    args = [
        str(py), str(script_path),
        "--seed", seed_url,
        "--strategy", str(cfg.get("strategy", "sitemap")),
        "--max-pages", str(cfg.get("max_pages", 100)),
        "--timeout", str(cfg.get("timeout", 15)),
        "--rate-per-host", str(cfg.get("rate_per_host", 1.0)),
        "--user-agent", str(cfg.get("user_agent", _default_config()["user_agent"])),
        "--source-id", str(src.id),
        "--run-id", str(run.id),
        "--robots-policy", str(cfg.get("robots_policy", "strict")),
    ]
    if cfg.get("force_https"): args.append("--force-https")
    if cfg.get("allowed_domains"): args += ["--allowed-domains", ",".join(cfg["allowed_domains"])]
    if cfg.get("include"): args += ["--include", ",".join(cfg["include"])]
    if cfg.get("exclude"): args += ["--exclude", ",".join(cfg["exclude"])]

    if cfg.get("strategy") in ("requests", "selenium"):
        args += ["--depth", str(cfg.get("depth", 2))]
    if cfg.get("strategy") == "selenium":
        args += ["--driver", str(cfg.get("driver","chrome"))]
        if cfg.get("no_headless"):
            args += ["--no-headless"]
        args += ["--render-wait-ms", str(cfg.get("render_wait_ms", 3000)),
                 "--window-size", str(cfg.get("window_size","1366,900"))]
        if cfg.get("wait_selector"):
            args += ["--wait-selector", str(cfg.get("wait_selector"))]

    # Ejecutar
    if os.name == "nt":
        cmd_shown = " ".join([f'"{a}"' if (" " in a or "\\" in a) else a for a in args])
    else:
        cmd_shown = " ".join(shlex.quote(a) for a in args)

    try:
        logger.info("[INGEST_WEB] exec run_id=%s cmd=%s", run.id, cmd_shown)
        env = {**os.environ, "RUN_DIR": str(run_dir)}
        env["PYTHONPATH"] = f"{str(project_root)}{os.pathsep}{env.get('PYTHONPATH','')}"
        env.setdefault("PYTHONIOENCODING", "utf-8")
        proc = subprocess.run(args, capture_output=True, text=True, check=False, cwd=str(project_root), env=env)
        # guardar stdout/err de la UI
        try:
            with (run_dir / "ui_stdout.txt").open("a", encoding="utf-8") as f:
                if proc.stdout: f.write(proc.stdout if proc.stdout.endswith("\n") else proc.stdout + "\n")
                if proc.stderr: f.write(proc.stderr if proc.stderr.endswith("\n") else proc.stderr + "\n")
        except Exception:
            pass
        # forzar/leer summary
        summary = _wait_for_summary(run_dir, timeout=8.0)
        totals = (summary or {}).get("totals") or {}
        _update_run_meta(run.id, status="done" if proc.returncode==0 else "error",
                         stdout=proc.stdout, extra={"run_dir": str(run_dir), "summary": summary, "summary_totals": totals})
        flash("Ingesta finalizada correctamente." if proc.returncode==0 else "Ingesta finalizada con error. Revisa la salida.",
              "success" if proc.returncode==0 else "danger")
    except Exception as e:
        (run_dir / "stdout.txt").write_text(f"[exception] {e}", encoding="utf-8")
        _update_run_meta(run.id, status="error", stdout=f"[exception] {e}", extra={"cmd": cmd_shown, "run_dir": str(run_dir)})
        logger.exception("[INGEST_WEB] exception run_id=%s: %s", run.id, e)
        flash(f"Fallo al ejecutar la ingesta: {e}", "danger")

    return redirect(url_for("ingesta_web.index"))


# ----------------------------- Utilidades UI -----------------------------

@dataclass
class _SummaryTotals:
    pages: int = 0
    chunks: int = 0
    bytes: int = 0


def _build_summary(run_dir: Path) -> Optional[dict]:
    index_path = run_dir / "fetch_index.json"
    raw_dir = run_dir / "raw"
    if not index_path.exists() or not raw_dir.exists():
        return None
    try:
        index = json.loads(index_path.read_text(encoding="utf-8"))
    except Exception:
        return None

    pages = []
    totals = _SummaryTotals()
    for rec in index:
        raw_path = rec.get("raw") or rec.get("path")
        url = rec.get("url"); title = rec.get("title"); status = rec.get("status")
        b = 0; n_chunks = 0
        if raw_path:
            try:
                p = Path(raw_path) if Path(raw_path).is_absolute() else (raw_dir / Path(raw_path).name)
                b = p.stat().st_size if p.exists() else 0
                html = p.read_text(encoding="utf-8", errors="ignore") if p.exists() else ""
                html = re.sub(r"<(script|style)[\s\S]*?</\1>", " ", html, flags=re.IGNORECASE)
                text = re.sub(r"<[^>]+>", " ", html); text = re.sub(r"\s+", " ", text).strip()
                n_chunks = (len(text) + 1200 - 1) // 1200 if text else 0
            except Exception:
                pass
        pages.append({"url": url, "title": title, "status": status, "raw": str(raw_path), "bytes": b, "num_chunks": n_chunks})
        totals.pages += 1; totals.chunks += n_chunks; totals.bytes += int(b or 0)

    return {"kind": "web", "run_dir": str(run_dir),
            "totals": {"pages": totals.pages, "chunks": totals.chunks, "bytes": totals.bytes},
            "pages": pages}


def _ensure_summary(run_dir: Path, *, write_if_missing: bool=True) -> Optional[dict]:
    summary_path = run_dir / "summary.json"
    if summary_path.exists():
        try:
            return json.loads(summary_path.read_text(encoding="utf-8"))
        except Exception:
            pass
    summary = _build_summary(run_dir)
    if summary and write_if_missing:
        try:
            summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
        except Exception:
            pass
    return summary


def _wait_for_summary(run_dir: Path, *, timeout: float=6.0, interval: float=0.5) -> Optional[dict]:
    t0 = time.time()
    while time.time() - t0 < timeout:
        s = _ensure_summary(run_dir, write_if_missing=True)
        if s and s.get("totals"):
            return s
        time.sleep(interval)
    return _ensure_summary(run_dir, write_if_missing=True)


def _update_run_meta(run_id: int, *, status: str, stdout: Optional[str]=None, extra: Optional[dict]=None):
    try:
        with db.get_session() as s:
            run = s.get(IngestionRun, run_id)
            if run:
                run.status = status
                meta = run.meta or {}
                if stdout is not None:
                    meta["stdout"] = stdout
                if extra:
                    meta.update(extra)
                run.meta = meta
                s.commit()
    except Exception:
        pass


def _normalize_artifact_path(relpath: str) -> Path:
    """Evita fugas fuera de RUNS_ROOT."""
    p = (RUNS_ROOT / relpath).resolve()
    if RUNS_ROOT.resolve() not in p.parents and RUNS_ROOT.resolve() != p:
        raise PermissionError("Ruta fuera de runs")
    return p


@bp_ingesta_web.route("/artifact/<path:relpath>")
def artifact(relpath: str):
    try:
        file_path = _normalize_artifact_path(relpath)
    except PermissionError:
        abort(403)
    except Exception:
        flash("Ruta de artefacto inválida.", "warning"); return redirect(url_for("ingesta_web.index"))
    if not file_path.exists() or not file_path.is_file():
        flash("Archivo no encontrado.", "warning"); return redirect(url_for("ingesta_web.index"))
    return send_file(file_path, as_attachment=True)


@bp_ingesta_web.route("/preview/<int:run_id>")
def preview(run_id: int):
    """Muestra stdout y lista de artefactos (botón 'Ver artefactos')."""
    with db.get_session() as s:
        sources = s.query(Source).filter(Source.type=="web").order_by(Source.id.desc()).all()
        runs = (
            s.query(IngestionRun)
             .options(selectinload(IngestionRun.source))
             .join(Source, IngestionRun.source_id==Source.id)
             .filter(Source.type=="web")
             .order_by(IngestionRun.id.desc()).limit(20).all()
        )
        run_obj = s.get(IngestionRun, run_id)

    # Localizar run_dir
    if run_obj and run_obj.meta and "run_dir" in run_obj.meta:
        run_dir = Path(str(run_obj.meta["run_dir"]))
    else:
        run_dir = RUNS_ROOT / "web" / f"run_{run_id}"

    # Leer stdout/ui_stdout
    preview_text = ""
    for fn in ("stdout.txt", "ui_stdout.txt"):
        p = run_dir / fn
        if p.exists() and p.stat().st_size > 0:
            try:
                preview_text = p.read_text(encoding="utf-8", errors="ignore")
                break
            except Exception:
                pass

    # Construir listado de artefactos
    artifacts = []
    try:
        # ficheros principales
        for name in ("summary.json", "fetch_index.json", "stdout.txt", "ui_stdout.txt"):
            p = run_dir / name
            if p.exists():
                rel = p.relative_to(RUNS_ROOT)
                artifacts.append({"name": name, "rel": str(rel), "size": p.stat().st_size})
        # raw/*
        raw_dir = run_dir / "raw"
        if raw_dir.exists():
            for p in sorted(raw_dir.glob("*.html"))[:200]:  # límite defensivo
                rel = p.relative_to(RUNS_ROOT)
                artifacts.append({"name": f"raw/{p.name}", "rel": str(rel), "size": p.stat().st_size})
    except Exception:
        pass

    show = request.args.get("show")  # 'artifacts' para abrir tarjeta
    return render_template("admin/ingesta_web.html",
                           sources=sources, runs=runs, cfg_defaults=_default_config(),
                           preview=preview_text, artifacts=artifacts, selected_run=run_id, show=show)
