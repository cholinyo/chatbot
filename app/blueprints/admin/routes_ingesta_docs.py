from __future__ import annotations
import traceback, shlex, subprocess, sys, time, json
from pathlib import Path
from typing import Dict, List, Tuple
from flask import Blueprint, render_template, request, redirect, url_for, flash, Response, abort, current_app

import app.extensions.db as db
from app.models import Source, IngestionRun, Document, Chunk

bp = Blueprint("ingesta_docs", __name__, url_prefix="/admin/ingesta-docs")

UI_DEFAULTS = {
    "patterns": "*.pdf,*.docx,*.txt,*.md,*.csv",
    "recursive": True,
    "only_new": True,
}

FLAG_INPUTDIR  = "--input-dir"
FLAG_PATTERN   = "--pattern"
FLAG_RECURSIVE = "--recursive"
FLAG_ONLY_NEW  = "--only-new"
FLAG_RUNDIR    = "--run-dir"
FLAG_PROJROOT  = "--project-root"

def _build_stats(session, sources_docs: List[Source]) -> Tuple[Dict[int, dict], dict]:
    stats_by_source: Dict[int, dict] = {}
    total_docs = 0
    total_chunks = 0
    ok = 0
    ko = 0
    for src in sources_docs:
        doc_count = session.query(Document).filter(Document.source_id == src.id).count()
        chunk_count = session.query(Chunk).filter(Chunk.source_id == src.id).count()
        total_docs += doc_count
        total_chunks += chunk_count
        last_run = (
            session.query(IngestionRun)
            .filter(IngestionRun.source_id == src.id)
            .order_by(IngestionRun.id.desc())
            .first()
        )
        last_run_info = None
        if last_run:
            meta = last_run.meta or {}
            is_ok = (last_run.status == "done") and (meta.get("returncode", 0) == 0)
            if is_ok: ok += 1
            else: ko += 1
            last_run_info = {
                "id": last_run.id,
                "status": last_run.status,
                "returncode": meta.get("returncode"),
                "duration_sec": meta.get("duration_sec"),
                "run_dir": meta.get("run_dir"),
                "cmd": meta.get("cmd"),
                "cwd": meta.get("cwd"),
            }
        stats_by_source[src.id] = {
            "documents_total": doc_count,
            "chunks_total": chunk_count,
            "last_run": last_run_info,
        }
    globals_ = {
        "sources_docs": len(sources_docs),
        "documents_total": total_docs,
        "chunks_total": total_chunks,
        "last_run_ok_sources": ok,
        "last_run_ko_sources": ko,
    }
    return stats_by_source, globals_

def _runs_viewmodel(session, runs: List[IngestionRun]) -> List[dict]:
    src_ids = {r.source_id for r in runs}
    sources = session.query(Source).filter(Source.id.in_(src_ids)).all()
    name_by_id = {s.id: (s.name or s.url or f"Fuente {s.id}") for s in sources}
    rows = []
    for r in runs:
        meta = r.meta or {}
        rows.append({
            "id": r.id,
            "source_id": r.source_id,
            "source_name": name_by_id.get(r.source_id, str(r.source_id)),
            "status": r.status,
            "has_stdout": bool(meta.get("stdout_preview") or meta.get("run_dir")),
            "duration_sec": meta.get("duration_sec"),
        })
    return rows

@bp.route("/", methods=["GET"])
def index():
    select_id = request.args.get("select_id", type=int)
    with db.get_session() as s:
        sources = (s.query(Source).filter(Source.type == "docs").order_by(Source.id.desc()).all())
        runs = (s.query(IngestionRun).order_by(IngestionRun.id.desc()).limit(20).all())
        stats_by_source, stats_globales = _build_stats(s, sources)
        runs_vm = _runs_viewmodel(s, runs)
    return render_template(
        "admin/ingesta_docs.html",
        sources=sources,
        runs=runs_vm,
        defaults=UI_DEFAULTS,
        select_id=select_id,
        stats_by_source=stats_by_source,
        stats_globales=stats_globales,
    )

@bp.post("/quick-save")
def quick_save():
    name      = (request.form.get("name") or "").strip()
    input_dir = (request.form.get("input_dir") or "").strip()
    patterns  = (request.form.get("patterns") or "").strip()
    recursive = bool(request.form.get("recursive"))
    only_new  = bool(request.form.get("only_new"))
    if not input_dir:
        flash("Indica una carpeta para la fuente.", "warning")
        return redirect(url_for("ingesta_docs.index"))
    cfg = {
        "input_dir": input_dir,
        "patterns": patterns or UI_DEFAULTS["patterns"],
        "recursive": recursive,
        "only_new": only_new,
    }
    with db.get_session() as s:
        src = Source(type="docs", url=input_dir, name=name or None, config=cfg)
        s.add(src); s.commit()
        new_id = src.id
    flash("Fuente 'docs' creada.", "success")
    return redirect(url_for("ingesta_docs.index", select_id=new_id))

@bp.post("/source/<int:source_id>/edit")
def source_edit(source_id: int):
    name      = (request.form.get("name") or "").strip()
    input_dir = (request.form.get("input_dir") or "").strip()
    patterns  = (request.form.get("patterns") or "").strip()
    recursive = bool(request.form.get("recursive"))
    only_new  = bool(request.form.get("only_new"))
    with db.get_session() as s:
        src = s.get(Source, source_id)
        if not src or src.type != "docs":
            flash("Fuente no encontrada o tipo inválido.", "danger")
            return redirect(url_for("ingesta_docs.index"))
        src.name = name or None
        if input_dir: src.url = input_dir
        cfg = src.config or {}
        cfg.update({
            "input_dir": input_dir or cfg.get("input_dir"),
            "patterns": patterns or cfg.get("patterns") or UI_DEFAULTS["patterns"],
            "recursive": recursive,
            "only_new": only_new,
        })
        src.config = cfg; s.commit()
    flash("Fuente actualizada.", "success")
    return redirect(url_for("ingesta_docs.index", select_id=source_id))

@bp.post("/source/<int:source_id>/delete")
def source_delete(source_id: int):
    confirm = (request.form.get("confirm") or "").lower()
    if confirm != "delete":
        flash("Escribe DELETE para confirmar la eliminación.", "warning")
        return redirect(url_for("ingesta_docs.index", select_id=source_id))
    with db.get_session() as s:
        src = s.get(Source, source_id)
        if not src or src.type != "docs":
            flash("Fuente no encontrada o tipo inválido.", "danger")
            return redirect(url_for("ingesta_docs.index"))
        s.delete(src); s.commit()
    flash("Fuente eliminada.", "success")
    return redirect(url_for("ingesta_docs.index"))

@bp.route("/run", methods=["POST"])
def run():
    source_id  = request.form.get("source_id")
    input_dir  = (request.form.get("input_dir") or "").strip()
    patterns_s = (request.form.get("patterns") or "").strip()
    recursive  = bool(request.form.get("recursive"))
    only_new   = bool(request.form.get("only_new"))
    extra_args = (request.form.get("extra_args") or "").strip()

    if not source_id:
        flash("Selecciona una fuente 'docs' o crea una con 'Guardar como fuente'.", "danger")
        return redirect(url_for("ingesta_docs.index"))

    with db.get_session() as s:
        src = s.get(Source, int(source_id))
        if not src or src.type != "docs":
            flash("Fuente inválida", "danger")
            return redirect(url_for("ingesta_docs.index"))
        cfg_f = src.config or {}
        input_dir  = input_dir  or cfg_f.get("input_dir")
        patterns_s = patterns_s or cfg_f.get("patterns")  or UI_DEFAULTS["patterns"]
        if "recursive" not in request.form:
            recursive = bool(cfg_f.get("recursive", UI_DEFAULTS["recursive"]))
        if "only_new" not in request.form:
            only_new = bool(cfg_f.get("only_new", UI_DEFAULTS["only_new"]))
        run = IngestionRun(
            source_id=src.id,
            status="running",
            meta={"docs_config": {
                "input_dir": input_dir,
                "patterns": patterns_s,
                "recursive": recursive,
                "only_new": only_new,
                "extra_args": extra_args,
            }},
        )
        s.add(run); s.commit()
        run_id = run.id

    REPO_ROOT = Path(current_app.root_path).parent.resolve()

    # PRE-FLIGHT
    runs_root = REPO_ROOT / "data/processed/runs/docs"
    runs_root.mkdir(parents=True, exist_ok=True)
    run_dir = (runs_root / f"run_{run_id}"); run_dir.mkdir(parents=True, exist_ok=True)

    script_path = REPO_ROOT / "scripts" / "ingest_documents.py"
    errors: list[str] = []
    if not input_dir:
        errors.append("No se ha especificado 'input_dir'.")
    else:
        p = Path(input_dir)
        if not p.exists() or not p.is_dir():
            errors.append(f"La carpeta indicada no existe o no es un directorio: {input_dir}")
    if not script_path.exists():
        errors.append(f"No se encuentra el script: {script_path}")

    if errors:
        msg = " | ".join(errors)
        with db.get_session() as s:
            run_db = s.get(IngestionRun, run_id)
            if run_db:
                meta = run_db.meta or {}
                meta.update({
                    "run_dir": str(run_dir),
                    "cwd": str(REPO_ROOT),
                    "stdout_preview": msg,
                    "returncode": -1,
                    "exception": msg,
                })
                run_db.meta = meta
                run_db.status = "error"
                s.commit()
        flash(f"Fallo al ejecutar la ingesta: {msg}", "danger")
        return redirect(url_for("ingesta_docs.index"))

    # Lanzamiento
    py = sys.executable
    args = [
        py,
        script_path.as_posix(),
        FLAG_INPUTDIR, input_dir,
        FLAG_RUNDIR, str(run_dir),
        FLAG_PROJROOT, str(REPO_ROOT),
    ]
    for p in [x.strip() for x in str(patterns_s).split(",") if x.strip()]:
        args += [FLAG_PATTERN, p]
    if recursive: args.append(FLAG_RECURSIVE)
    if only_new:  args.append(FLAG_ONLY_NEW)
    if extra_args: args += shlex.split(extra_args)

    with db.get_session() as s:
        run_db = s.get(IngestionRun, run_id)
        if run_db:
            meta = run_db.meta or {}
            meta["cmd"] = " ".join(shlex.quote(a) for a in args)
            meta["run_dir"] = str(run_dir)
            meta["cwd"] = str(REPO_ROOT)
            run_db.meta = meta
            s.commit()

    t0 = time.perf_counter()
    try:
        proc = subprocess.run(args, capture_output=True, text=True, check=False, cwd=str(REPO_ROOT))
        duration = time.perf_counter() - t0
        out = (proc.stdout or "") + ("\n" + proc.stderr if proc.stderr else "")
        (run_dir / "stdout.txt").write_text(out or "(sin salida)", encoding="utf-8", errors="replace")

        with db.get_session() as s:
            run_db = s.get(IngestionRun, run_id)
            if run_db:
                meta = run_db.meta or {}
                meta["stdout_preview"] = (out or "")[-20000:]
                meta["returncode"] = proc.returncode
                meta["duration_sec"] = round(duration, 3)
                run_db.meta = meta
                run_db.status = "done" if proc.returncode == 0 else "error"
                s.commit()

        flash(
            f"Ingesta finalizada {'correctamente' if proc.returncode == 0 else 'con errores'}. "
            f"Duración: {round(duration, 2)}s",
            "success" if proc.returncode == 0 else "danger",
        )
    except Exception as e:
        tb = traceback.format_exc()
        (run_dir / "stdout.txt").write_text(tb, encoding="utf-8", errors="replace")
        with db.get_session() as s:
            run_db = s.get(IngestionRun, run_id)
            if run_db:
                meta = run_db.meta or {}
                meta["stdout_preview"] = tb[-20000:]
                meta["returncode"] = -1
                meta["exception"] = str(e)
                run_db.meta = meta
                run_db.status = "error"
                s.commit()
        flash(f"Fallo al ejecutar la ingesta: {e}", "danger")

    return redirect(url_for("ingesta_docs.index"))

@bp.get("/run/<int:run_id>/stdout")
def run_stdout(run_id: int):
    """Salida textual del último run (robusto a encodings)."""
    with db.get_session() as s:
        run = s.get(IngestionRun, run_id)
        if not run: abort(404)
        meta = run.meta or {}
        run_dir = meta.get("run_dir")
        if run_dir:
            path = Path(run_dir) / "stdout.txt"
            if path.exists():
                try:
                    txt = path.read_text(encoding="utf-8", errors="replace")
                except Exception as e:
                    txt = f"(error leyendo stdout.txt: {e})"
                return Response(txt, mimetype="text/plain; charset=utf-8")
        preview = meta.get("stdout_preview") or "(sin salida)"
        return Response(preview, mimetype="text/plain; charset=utf-8")

@bp.get("/run/<int:run_id>/summary.json")
def run_summary(run_id: int):
    """Resumen JSON del run (robusto a encodings; mensaje claro si no existe)."""
    with db.get_session() as s:
        run = s.get(IngestionRun, run_id)
        if not run: abort(404)
        meta = run.meta or {}
        run_dir = meta.get("run_dir")
        if not run_dir:
            return Response(json.dumps({"error": "run_dir no disponible en meta"}), mimetype="application/json", status=404)
        path = Path(run_dir) / "summary.json"
        if not path.exists():
            return Response(json.dumps({"error": "summary.json no encontrado"}), mimetype="application/json", status=404)
        try:
            data = path.read_text(encoding="utf-8", errors="replace")
        except Exception as e:
            return Response(json.dumps({"error": f"no se pudo leer summary.json: {e}"}), mimetype="application/json", status=500)
        return Response(data, mimetype="application/json; charset=utf-8")
