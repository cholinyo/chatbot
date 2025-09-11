# -*- coding: utf-8 -*-
from __future__ import annotations

import json
import os
import re
import shlex
import subprocess
import sys
import logging
from logging.handlers import RotatingFileHandler
from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Dict, Any
from datetime import datetime, timezone
from zoneinfo import ZoneInfo  # para mostrar en Europe/Madrid

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

# Rutas de trabajo
RUNS_ROOT = Path("data/processed/runs")
RUNS_ROOT.mkdir(parents=True, exist_ok=True)
LOGS_DIR = Path("data/logs")
LOGS_DIR.mkdir(parents=True, exist_ok=True)
LOG_FILE = LOGS_DIR / "ingestion.log"


def _get_ingestion_logger() -> logging.Logger:
    logger = logging.getLogger("ingesta_web")
    logger.setLevel(logging.INFO)
    if not any(
        isinstance(h, RotatingFileHandler)
        and Path(getattr(h, "baseFilename", "")).resolve() == LOG_FILE.resolve()
        for h in logger.handlers
    ):
        fh = RotatingFileHandler(LOG_FILE, maxBytes=2_000_000, backupCount=3, encoding="utf-8")
        fh.setLevel(logging.INFO)
        fh.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
        logger.addHandler(fh)
    return logger


def _default_config() -> Dict[str, Any]:
    return {
        "strategy": "sitemap",
        "depth": 1,  # solo requests/selenium
        "allowed_domains": [],
        "include": [],
        "exclude": [],
        "robots_policy": "strict",
        "rate_per_host": 1.0,
        "timeout": 15,
        "force_https": True,  # útil en sitemap
        "include_pdfs": False,  # activar PDFs solo en sitemap
        "user_agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, como Gecko) Chrome/124.0 Safari/537.36",
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


# ---------- utilidades ----------

def _extract_run_dir(proc_output: str) -> Optional[str]:
    for line in proc_output.splitlines():
        if line.startswith("[RUN_DIR]"):
            return line.replace("[RUN_DIR]", "").strip()
    return None


def _to_local_display(iso_str: str, tz_name: str = "Europe/Madrid") -> str:
    """Convierte una ISO (UTC o con tz) a una cadena local legible."""
    try:
        s = iso_str.strip()
        if s.endswith("Z"):
            s = s[:-1] + "+00:00"
        dt = datetime.fromisoformat(s)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        local = dt.astimezone(ZoneInfo(tz_name))
        return local.strftime("%Y-%m-%d %H:%M:%S")
    except Exception:
        return iso_str


def _compute_run_rel(run_dir: str) -> Optional[str]:
    """Devuelve la ruta relativa a RUNS_ROOT (p.ej. 'web/run_35')."""
    try:
        rd = Path(run_dir).resolve()
        base = RUNS_ROOT.resolve()
        if str(rd).startswith(str(base)):
            return str(rd.relative_to(base)).replace("\\", "/")
    except Exception:
        pass
    # último recurso: intenta deducir por nombre
    p = Path(run_dir)
    if p.name.startswith("run_"):
        return f"web/{p.name}"
    return None


def _update_run_meta(run_id: int, *, status: str, stdout: str, extra: dict | None = None) -> None:
    with db.get_session() as s:
        run_db = s.get(IngestionRun, run_id)
        if not run_db:
            return
        meta = run_db.meta or {}
        meta["stdout"] = stdout
        if extra:
            meta.update(extra)
        # Normaliza fecha a local para visualización
        if "ended_at" in meta:
            meta["display_time"] = _to_local_display(str(meta["ended_at"]))
        elif "started_at" in meta:
            meta["display_time"] = _to_local_display(str(meta["started_at"]))
        # Garantiza run_rel si tenemos run_dir
        if "run_dir" in meta and "run_rel" not in meta:
            rr = _compute_run_rel(str(meta["run_dir"]))
            if rr:
                meta["run_rel"] = rr
        run_db.meta = meta
        run_db.status = status
        s.commit()


def _normalize_artifact_path(relpath: str) -> Path:
    """
    Acepta rutas RELATIVAS a RUNS_ROOT (recomendado).
    No acepta rutas absolutas tipo 'C:\\...' para evitar problemas en URL y seguridad.
    """
    rel = relpath.replace("\\", "/").lstrip("/")
    base = RUNS_ROOT.resolve()
    cand = (base / rel).resolve()
    if not str(cand).startswith(str(base)):
        raise PermissionError("Ruta fuera del directorio de runs")
    return cand


# --------- resumen/chunks ---------

@dataclass
class _SummaryTotals:
    pages: int = 0
    chunks: int = 0
    bytes: int = 0


def _count_chunks_simple(text: str, max_chars: int = 1200) -> int:
    n = len(text.strip()) if text else 0
    return (n + max_chars - 1) // max_chars if n else 0


def _html_to_text(html: str) -> str:
    # limpieza básica para contar chunks
    t = re.sub(r"<(script|style)[\s\S]*?</\1>", " ", html, flags=re.IGNORECASE)
    t = re.sub(r"<[^>]+>", " ", t)
    t = re.sub(r"\s+", " ", t).strip()
    return t


def _build_summary(run_dir: Path) -> Optional[dict]:
    """
    Lee fetch_index.json y calcula totales.
    - HTML: lee el .html -> text -> chunks
    - PDF : usa .pdf.txt si existe; si no existe, no extrae (la extracción la hace el orquestador al guardar)
    """
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
        rel_path = rec.get("path") or rec.get("raw")
        url = rec.get("url")
        status = rec.get("status")
        title = rec.get("title") or None

        b = int(rec.get("bytes") or 0)
        n_chunks = 0

        if rel_path:
            full = (run_dir / rel_path)
            if not b and full.exists():
                try:
                    b = full.stat().st_size
                except Exception:
                    b = 0

            ext = full.suffix.lower()
            try:
                if ext == ".pdf":
                    txt_path = full.with_suffix(full.suffix + ".txt")  # .pdf.txt
                    text = ""
                    if txt_path.exists():
                        try:
                            text = txt_path.read_text(encoding="utf-8", errors="ignore")
                        except Exception:
                            text = ""
                    # si hay texto, cuenta chunks
                    if text:
                        n_chunks = _count_chunks_simple(text, max_chars=1200)
                else:
                    html = full.read_text(encoding="utf-8", errors="ignore")
                    text = _html_to_text(html)
                    n_chunks = _count_chunks_simple(text, max_chars=1200)
            except Exception:
                pass

        pages.append(
            {"url": url, "title": title, "status": status, "raw": (str(run_dir / rel_path) if rel_path else None), "bytes": b, "num_chunks": n_chunks}
        )
        totals.pages += 1
        totals.chunks += n_chunks
        totals.bytes += int(b or 0)

    summary = {
        "kind": "web",
        "run_dir": str(run_dir),
        "totals": {"pages": totals.pages, "chunks": totals.chunks, "bytes": totals.bytes},
        "pages": pages,
    }
    return summary


# ---------- vistas ----------

@bp_ingesta_web.route("/", methods=["GET"])
def index():
    with db.get_session() as s:
        sources = (
            s.query(Source)
            .filter(Source.type == "web")
            .order_by(Source.id.desc())
            .all()
        )
        runs = (
            s.query(IngestionRun)
            .join(Source, IngestionRun.source_id == Source.id)
            .filter(Source.type == "web")
            .order_by(IngestionRun.id.desc())
            .limit(20)
            .all()
        )

    sources_by_id = {src.id: src for src in sources}

    # Completar meta para UI: run_dir, run_rel, summary_totals, display_time
    for r in runs:
        meta = r.meta or {}
        # run_dir
        if "run_dir" not in meta:
            meta["run_dir"] = str(RUNS_ROOT / "web" / f"run_{r.id}")
        # run_rel
        if "run_rel" not in meta and "run_dir" in meta:
            rr = _compute_run_rel(str(meta["run_dir"]))
            if rr:
                meta["run_rel"] = rr
        # summary
        summ_path = Path(str(meta["run_dir"])) / "summary.json"
        if "summary_totals" not in meta and summ_path.exists():
            try:
                js = json.loads(summ_path.read_text(encoding="utf-8"))
                meta["summary_totals"] = js.get("totals", {})
            except Exception:
                pass
        # display time
        ts = meta.get("ended_at") or meta.get("started_at")
        if ts and "display_time" not in meta:
            meta["display_time"] = _to_local_display(str(ts))
        r.meta = meta

    return render_template(
        "admin/ingesta_web.html",
        sources=sources,
        sources_by_id=sources_by_id,
        runs=runs,
        cfg_defaults=_default_config(),
    )


@bp_ingesta_web.route("/save", methods=["POST"])
def save():
    logger = _get_ingestion_logger()

    raw_id = (request.form.get("id") or "").strip()
    src_id = int(raw_id) if raw_id.isdigit() and int(raw_id) > 0 else None

    url = (request.form.get("seed") or request.form.get("url") or "").strip()
    name = (request.form.get("name") or "").strip()
    if not url:
        flash("La URL es obligatoria", "danger")
        return redirect(url_for("ingesta_web.index"))

    # Config desde formulario
    cfg = _default_config()
    cfg["strategy"] = request.form.get("strategy") or "sitemap"
    cfg["depth"] = int(request.form.get("depth") or 1)
    cfg["allowed_domains"] = [d.strip() for d in (request.form.get("allowed_domains") or "").replace("\n", ",").split(",") if d.strip()]
    cfg["include"] = [s.strip() for s in (request.form.get("include") or "").splitlines() if s.strip()]
    cfg["exclude"] = [s.strip() for s in (request.form.get("exclude") or "").splitlines() if s.strip()]
    cfg["robots_policy"] = request.form.get("robots_policy") or "strict"
    cfg["rate_per_host"] = float(request.form.get("rate_per_host") or 1.0)
    cfg["timeout"] = int(request.form.get("timeout") or 15)
    cfg["force_https"] = ("force_https" in request.form)
    cfg["include_pdfs"] = bool(request.form.get("include_pdfs"))
    cfg["user_agent"] = request.form.get("user_agent") or _default_config()["user_agent"]
    cfg["max_pages"] = int(request.form.get("max_pages") or 100)

    # Selenium
    cfg["driver"] = request.form.get("driver") or "chrome"
    cfg["window_size"] = request.form.get("window_size") or "1366,900"
    cfg["render_wait_ms"] = int(request.form.get("render_wait_ms") or 3000)
    cfg["wait_selector"] = request.form.get("wait_selector") or ""
    cfg["no_headless"] = bool(request.form.get("no_headless"))
    cfg["scroll"] = bool(request.form.get("scroll"))
    cfg["scroll_steps"] = int(request.form.get("scroll_steps") or 4)
    cfg["scroll_wait_ms"] = int(request.form.get("scroll_wait_ms") or 500)

    with db.get_session() as s:
        if src_id:
            # EDITAR
            src = s.get(Source, src_id)
            if not src or src.type != "web":
                flash("Source no encontrado.", "danger")
                return redirect(url_for("ingesta_web.index"))
            src.url = url
            if name:
                src.name = name
            src.type = "web"
            src.config = {**(src.config or {}), **cfg}
            action = "update"
        else:
            # CREAR
            src = Source(url=url, name=(name or None), type="web", config=cfg)
            s.add(src)
            action = "create"
        s.commit()

    logger.info("[INGEST_WEB] source_%s id=%s url=%s name=%s", action, src.id, src.url, src.name or "")
    flash("Fuente actualizada" if action == "update" else "Fuente guardada", "success")
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
        started_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
        run = IngestionRun(source_id=src.id, status="running", meta={"web_config": cfg, "started_at": started_at})
        s.add(run)
        s.commit()
        seed_url = src.url

    runs_web_root = RUNS_ROOT / "web"
    runs_web_root.mkdir(parents=True, exist_ok=True)
    run_dir_fallback = runs_web_root / f"run_{run.id}"
    run_dir_fallback.mkdir(parents=True, exist_ok=True)

    # localiza script
    candidates = [Path("scripts/ingest_web.py"), Path("ingest_web.py")]
    script_path = next((p for p in candidates if p.exists()), None)
    if not script_path:
        _update_run_meta(
            run_id=run.id,
            status="error",
            stdout="[NO_SCRIPT_FOUND]",
            extra={"run_dir": str(run_dir_fallback), "ended_at": datetime.now(timezone.utc).isoformat(timespec="seconds")},
        )
        flash("No encuentro scripts/ingest_web.py", "danger")
        return redirect(url_for("ingesta_web.index"))

    def _csv_arg(vals):
        if not vals:
            return None
        if isinstance(vals, (list, tuple)):
            return ",".join([str(v) for v in vals if str(v).strip()])
        return str(vals)

    py = sys.executable
    args = [
        py, str(script_path),
        "--seed", seed_url,
        "--strategy", cfg.get("strategy", "sitemap"),
        "--max-pages", str(cfg.get("max_pages", 100)),
        "--timeout", str(cfg.get("timeout", 15)),
        "--rate-per-host", str(cfg.get("rate_per_host", 1.0)),
        "--user-agent", cfg.get("user_agent", _default_config()["user_agent"]),
        "--source-id", str(src.id),
        "--run-id", str(run.id),
        "--robots-policy", (cfg.get("robots_policy") or "strict"),
    ]

    ad = _csv_arg(cfg.get("allowed_domains"))
    if ad:
        args += ["--allowed-domains", ad]
    inc = _csv_arg(cfg.get("include"))
    if inc:
        args += ["--include", inc]
    exc = _csv_arg(cfg.get("exclude"))
    if exc:
        args += ["--exclude", exc]
    if cfg.get("force_https") and cfg.get("strategy") == "sitemap":
        args.append("--force-https")
    # PDFs solo sitemap (flag explícito)
    if cfg.get("strategy") == "sitemap" and cfg.get("include_pdfs"):
        args.append("--include-pdfs")
    if cfg.get("strategy") in ("requests", "selenium"):
        args += ["--depth", str(cfg.get("depth", 1))]
    if cfg.get("strategy") == "selenium":
        args += ["--driver", cfg.get("driver", "chrome"),
                 "--render-wait-ms", str(cfg.get("render_wait_ms", 3000)),
                 "--window-size", cfg.get("window_size", "1366,900")]
        if cfg.get("wait_selector"):
            args += ["--wait-selector", cfg["wait_selector"]]
        if cfg.get("no_headless"):
            args.append("--no-headless")
        if cfg.get("scroll"):
            args += ["--scroll", str(cfg.get("scroll_steps", 4)), str(cfg.get("scroll_wait_ms", 500))]

    cmd_shown = " ".join(shlex.quote(a) for a in args)
    logger.info("[INGEST_WEB] start run source_id=%s run_id=%s url=%s strategy=%s max_pages=%s",
                source_id, run.id, seed_url, cfg.get("strategy"), cfg.get("max_pages"))
    logger.info("[INGEST_WEB] exec run_id=%s cmd=%s", run.id, cmd_shown)

    project_root = Path(current_app.root_path).parent
    proc = subprocess.run(
        args,
        capture_output=True,
        text=True,
        check=False,
        cwd=str(project_root),
        env={**os.environ, "RUN_DIR": str(run_dir_fallback)},
    )
    out = (proc.stdout or "") + ("\n" + proc.stderr if proc.stderr else "")

    # Resolver RUN_DIR
    run_dir = _extract_run_dir(out) or str(run_dir_fallback)
    Path(run_dir).mkdir(parents=True, exist_ok=True)
    (Path(run_dir) / "stdout.txt").write_text(out or "(sin salida)", encoding="utf-8")

    extra = {
        "returncode": proc.returncode,
        "cmd": cmd_shown,
        "run_dir": run_dir,
        "run_rel": _compute_run_rel(run_dir) or None,
        "ended_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }

    # summary + totales
    try:
        summary = _build_summary(Path(run_dir))
        if summary:
            (Path(run_dir) / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
            totals = summary.get("totals", {})
            extra["summary_totals"] = totals
            extra["pages"] = totals.get("pages", 0)
            extra["chunks"] = totals.get("chunks", 0)
            extra["bytes"] = totals.get("bytes", 0)
    except Exception as e:
        extra["summary_error"] = f"{type(e).__name__}: {e}"

    _update_run_meta(
        run_id=run.id,
        status=("done" if proc.returncode == 0 else "error"),
        stdout=(out[-20000:] or "(sin salida del proceso)"),
        extra=extra,
    )

    logger.info("[INGEST_WEB] finished run_id=%s returncode=%s run_dir=%s", run.id, proc.returncode, run_dir)
    flash("Ingesta finalizada con éxito." if proc.returncode == 0 else "Ingesta finalizada con error. Revisa la salida.",
          "success" if proc.returncode == 0 else "danger")
    return redirect(url_for("ingesta_web.index"))


@bp_ingesta_web.route("/delete/<int:source_id>", methods=["POST"])
def delete(source_id: int):
    from sqlalchemy import text
    logger = _get_ingestion_logger()

    with db.get_session() as s:
        src = s.get(Source, source_id)
        if not src or src.type != "web":
            flash("Fuente no encontrada.", "warning")
            return redirect(url_for("ingesta_web.index"))

        name, url = (src.name or ""), (src.url or "")

        s.execute(
            text("""
                DELETE FROM chunks
                 WHERE document_id IN (
                       SELECT id FROM documents WHERE source_id = :sid
                 )
            """),
            {"sid": source_id},
        )
        s.execute(text("DELETE FROM documents WHERE source_id = :sid"), {"sid": source_id})
        s.execute(text("DELETE FROM ingestion_runs WHERE source_id = :sid"), {"sid": source_id})
        s.execute(text("DELETE FROM sources WHERE id = :sid"), {"sid": source_id})
        s.commit()

    logger.info("[INGEST_WEB] delete source_id=%s name=%s url=%s", source_id, name, url)
    flash("Fuente eliminada", "success")
    return redirect(url_for("ingesta_web.index"))


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

    # Mostrar inline para txt/json/html; descargar el resto
    ext = file_path.suffix.lower()
    as_attachment = not (ext in {".txt", ".json", ".html", ".htm"})
    return send_file(file_path, as_attachment=as_attachment)


@bp_ingesta_web.route("/preview/<int:run_id>")
def preview(run_id: int):
    with db.get_session() as s:
        sources = (
            s.query(Source)
            .filter(Source.type == "web")
            .order_by(Source.id.desc())
            .all()
        )
        runs = (
            s.query(IngestionRun)
            .join(Source, IngestionRun.source_id == Source.id)
            .filter(Source.type == "web")
            .order_by(IngestionRun.id.desc())
            .limit(20)
            .all()
        )
        run_obj = s.get(IngestionRun, run_id)

    preview_text = ""
    if run_obj and run_obj.meta and "stdout" in run_obj.meta:
        preview_text = str(run_obj.meta.get("stdout") or "")
    elif run_obj and run_obj.meta and "run_dir" in run_obj.meta:
        stdout_path = Path(str(run_obj.meta["run_dir"])) / "stdout.txt"
        if stdout_path.exists():
            preview_text = stdout_path.read_text(encoding="utf-8", errors="ignore")

    sources_by_id = {src.id: src for src in sources}

    return render_template(
        "admin/ingesta_web.html",
        sources=sources,
        sources_by_id=sources_by_id,
        runs=runs,
        cfg_defaults=_default_config(),
        preview=preview_text,
    )
