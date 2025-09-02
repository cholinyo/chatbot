# app/blueprints/admin/routes_ingesta_docs.py
# -*- coding: utf-8 -*-
from __future__ import annotations

import json
import os
import re
import shlex
import subprocess
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

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

# Blueprint (se mantiene el nombre público "ingesta_docs")
bp_ingesta_docs = Blueprint("ingesta_docs", __name__, url_prefix="/admin/ingesta-docs")

# Directorio base para artefactos
RUNS_ROOT = Path("data/processed/runs")
RUNS_ROOT.mkdir(parents=True, exist_ok=True)


def _utcnow():
    return datetime.now(timezone.utc)


def _update_run_meta(
    run_id: int,
    *,
    status: str | None = None,
    stdout: str | None = None,
    extra: dict | None = None
):
    with db.get_session() as s:
        run = s.get(IngestionRun, run_id)
        if not run:
            return
        meta = dict(run.meta or {})
        if stdout is not None:
            meta["stdout"] = stdout
        if extra:
            meta.update(extra)
        run.meta = meta
        if status is not None:
            run.status = status
        # timestamps si existen en el modelo
        if status == "running" and hasattr(run, "started_at") and not getattr(run, "started_at", None):
            run.started_at = _utcnow()
        if status in {"done", "error"} and hasattr(run, "finished_at"):
            run.finished_at = _utcnow()
        s.commit()


def _extract_last_json_block(s: str) -> Optional[dict]:
    """
    Busca el último bloque JSON en la salida del proceso.
    Soporta salida en línea o pretty-print.
    """
    # Intento 1: última línea que parezca JSON plano
    lines = [ln.strip() for ln in s.splitlines() if ln.strip()]
    for ln in reversed(lines):
        if ln.startswith("{") and ln.endswith("}"):
            try:
                return json.loads(ln)
            except Exception:
                pass
    # Intento 2: último bloque {...} por regex (greedy)
    matches = list(re.finditer(r"\{[\s\S]*\}", s))
    if matches:
        last = matches[-1].group(0)
        try:
            return json.loads(last)
        except Exception:
            return None
    return None


@bp_ingesta_docs.route("/", methods=["GET"])
def index():
    with db.get_session() as s:
        sources = (
            s.query(Source)
            .filter(Source.type == "docs")
            .order_by(Source.id.desc())
            .all()
        )
        runs = (
            s.query(IngestionRun)
            .join(Source, IngestionRun.source_id == Source.id)
            .filter(Source.type == "docs")
            .order_by(IngestionRun.id.desc())
            .limit(25)
            .all()
        )
    return render_template(
        "admin/ingesta_docs.html",
        sources=sources,
        runs=runs,
    )


@bp_ingesta_docs.route("/save", methods=["POST"])
def save():
    input_dir = (request.form.get("input_dir") or "").strip()
    name = (request.form.get("name") or "").strip()
    pattern = (request.form.get("pattern") or "*.pdf,*.docx,*.txt").strip()
    chunk_size = int(request.form.get("chunk_size") or 512)
    chunk_overlap = int(request.form.get("chunk_overlap") or 64)

    if not input_dir:
        flash("Debes indicar una carpeta de entrada.", "danger")
        return redirect(url_for("ingesta_docs.index"))

    cfg = {
        "input_dir": input_dir,
        "pattern": pattern,
        "chunk_size": chunk_size,
        "chunk_overlap": chunk_overlap,
        "recursive": True,
        "policy": "hash",
    }

    with db.get_session() as s:
        src = Source(
            type="docs",
            url=input_dir,
            name=name or Path(input_dir).name,
            config=cfg,
        )
        s.add(src)
        s.commit()

    flash("Fuente DOCS guardada.", "success")
    return redirect(url_for("ingesta_docs.index"))


@bp_ingesta_docs.route("/run/<int:source_id>", methods=["POST"])
def run(source_id: int):
    with db.get_session() as s:
        src = s.get(Source, source_id)
        if not src or src.type != "docs":
            flash("Fuente DOCS no encontrada.", "danger")
            return redirect(url_for("ingesta_docs.index"))
        cfg = dict(src.config or {})

        run = IngestionRun(source_id=src.id, status="running", meta={"docs_config": cfg})
        if hasattr(run, "started_at"):
            run.started_at = _utcnow()
        s.add(run)
        s.commit()

    # Localiza el script
    project_root = Path(current_app.root_path).parent
    candidates = [project_root / "scripts" / "ingest_documents.py", project_root / "ingest_documents.py"]
    script_path = next((p for p in candidates if p.exists()), None)
    if not script_path:
        _update_run_meta(run.id, status="error", stdout="[NO_SCRIPT_FOUND] ingest_documents.py")
        flash("No encuentro scripts/ingest_documents.py", "danger")
        return redirect(url_for("ingesta_docs.index"))

    # Construye comando
    args = [
        sys.executable,
        str(script_path),
        "--input-dir", cfg.get("input_dir", ""),
        "--pattern", cfg.get("pattern", "*.pdf,*.docx,*.txt"),
        "--chunk-size", str(cfg.get("chunk_size", 512)),
        "--chunk-overlap", str(cfg.get("chunk_overlap", 64)),
        "--verbose-json",
        "--project-root", str(project_root),
    ]
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
        out_tail = out[-20000:] if out else "(sin salida)"

        summary = _extract_last_json_block(out) or {}
        run_dir = summary.get("run_dir")
        elapsed = summary.get("elapsed_sec")
        stats = summary.get("stats") or {}
        totals = {
            "docs": (stats.get("new_docs", 0) + stats.get("updated_docs", 0)),
            "chunks": stats.get("total_chunks", 0),
        }

        extra = {
            "cmd": cmd_shown,
            "returncode": proc.returncode,
            "run_dir": run_dir,
            "elapsed_sec": elapsed,
            "summary_stats": stats,
            "summary_totals": totals,
        }
        _update_run_meta(
            run.id,
            status=("done" if proc.returncode == 0 else "error"),
            stdout=out_tail,
            extra=extra,
        )

        if run_dir:
            try:
                rd = Path(run_dir)
                rd.mkdir(parents=True, exist_ok=True)
                (rd / "stdout.txt").write_text(out or "(sin salida)", encoding="utf-8")
                (rd / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
            except Exception:
                pass

        flash(
            "Ingesta DOCS finalizada con éxito." if proc.returncode == 0 else "Ingesta DOCS con errores. Revisa salida.",
            "success" if proc.returncode == 0 else "danger",
        )
    except Exception as e:
        _update_run_meta(run.id, status="error", stdout=f"[exception] {e}", extra={"cmd": cmd_shown})
        flash(f"Excepción al ejecutar la ingesta: {e}", "danger")

    return redirect(url_for("ingesta_docs.index"))


@bp_ingesta_docs.route("/artifact/<path:relpath>")
def artifact(relpath: str):
    base = RUNS_ROOT.resolve()
    rel = relpath.replace("\\", "/")
    p = Path(rel)
    if p.is_absolute():
        cand = p.resolve()
    else:
        if rel.startswith(str(base).replace("\\", "/")):
            rel = rel[len(str(base)):].lstrip("/")
        cand = (base / rel).resolve()
    if not str(cand).startswith(str(base)):
        abort(403)

    if not cand.exists() or not cand.is_file():
        flash("Archivo no encontrado.", "warning")
        return redirect(url_for("ingesta_docs.index"))

    return send_file(cand, as_attachment=True)


@bp_ingesta_docs.route("/preview/<int:run_id>")
def preview(run_id: int):
    with db.get_session() as s:
        sources = (
            s.query(Source).filter(Source.type == "docs").order_by(Source.id.desc()).all()
        )
        runs = (
            s.query(IngestionRun)
            .join(Source, IngestionRun.source_id == Source.id)
            .filter(Source.type == "docs")
            .order_by(IngestionRun.id.desc())
            .limit(25)
            .all()
        )
        run_obj = s.get(IngestionRun, run_id)

    preview_text = ""
    if run_obj and run_obj.meta and "stdout" in run_obj.meta:
        preview_text = str(run_obj.meta.get("stdout") or "")
    if not preview_text and run_obj and run_obj.meta and "run_dir" in run_obj.meta:
        stdout_path = Path(str(run_obj.meta["run_dir"])) / "stdout.txt"
        if stdout_path.exists():
            preview_text = stdout_path.read_text(encoding="utf-8", errors="ignore")

    return render_template(
        "admin/ingesta_docs.html",
        sources=sources,
        runs=runs,
        preview=preview_text,
    )


# === Alias para que app/__init__.py pueda importar `bp` ===
bp = bp_ingesta_docs
