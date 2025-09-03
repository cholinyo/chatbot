# app/blueprints/admin/routes_vector_store.py
from __future__ import annotations

import json
import shlex
import subprocess
import time
from pathlib import Path
from glob import glob  # === NUEVO HISTÓRICO ===

from flask import Blueprint, flash, redirect, render_template, request, url_for, current_app, send_from_directory  # + send_from_directory
from werkzeug.utils import secure_filename  # noqa: F401  # reservado para futuras subidas CSV

# Modelos (import seguros a nivel de módulo)
from app.models.source import Source
from app.models.ingestion_run import IngestionRun

bp = Blueprint("vector_store", __name__, url_prefix="/admin/vector_store")

OUT_BASE = Path("models")


# ---------- Sesión perezosa para evitar NoneType() ----------
def _open_session():
    try:
        from app.extensions import db as _dbmod
        SessionLocal = getattr(_dbmod, "SessionLocal", None)
        if SessionLocal:
            return SessionLocal()  # type: ignore[misc]
    except Exception:
        pass
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    uri = (current_app.config.get("SQLALCHEMY_DATABASE_URI") or "sqlite:///data/processed/tracking.sqlite")
    engine = create_engine(uri, future=True)
    return sessionmaker(bind=engine, future=True)()


def _run_cmd(cmd: str):
    proc = subprocess.Popen(shlex.split(cmd), stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    lines = []
    assert proc.stdout is not None
    for line in proc.stdout:
        line = line.strip()
        try:
            lines.append(json.loads(line))
        except Exception:
            lines.append({"level": "INFO", "event": "stdout", "line": line})
    code = proc.wait()
    return code, lines


def _safe_meta(meta_val):
    if isinstance(meta_val, dict):
        return meta_val
    if isinstance(meta_val, str):
        try:
            return json.loads(meta_val)
        except Exception:
            return {}
    return {} if meta_val is None else dict(meta_val)


def _load_runs_and_sources(limit_runs: int = 80):
    runs, sources = [], []
    with _open_session() as session:
        for s in session.query(Source).order_by(Source.id.asc()).all():
            t = (getattr(s, "type", "") or "").lower()
            name = (getattr(s, "name", "") or "").strip()
            label = f"{s.id} · {t or 'unknown'}" + (f" · {name}" if name else "")
            sources.append({"id": s.id, "type": getattr(s, "type", None), "name": getattr(s, "name", None), "label": label[:120]})

        order_col = getattr(IngestionRun, "started_at", None) or getattr(IngestionRun, "created_at", None) or IngestionRun.id
        q = session.query(IngestionRun).order_by(order_col.desc()).limit(limit_runs)
        for r in q.all():
            status = (getattr(r, "status", "") or "").lower()
            src = getattr(r, "source_id", None)
            meta = _safe_meta(getattr(r, "meta", {}))
            counters = meta.get("summary_counters") or meta.get("counters") or {}
            pages = counters.get("pages_extracted") or counters.get("pages_total")
            dt_val = getattr(r, "started_at", None) or getattr(r, "created_at", None)
            dt_txt = str(dt_val)[:19] if dt_val else "?"
            extra = f" · {pages} páginas" if pages else ""
            label = f"{r.id} · src {src} · {status}{extra} · {dt_txt}"
            runs.append({"id": r.id, "source_id": src, "status": getattr(r, "status", None), "label": label[:140]})
    return runs, sources


# === NUEVO HISTÓRICO ===
def _list_rebuild_history(store: str, max_items: int = 30):
    """
    Lee models/<store>/rebuild_*.json y devuelve entradas ordenadas desc por timestamp.
    Cada entrada: {
      fname, ts, store, mode, model, batch_size, limit, k, dry_run,
      total_jobs, duration_sec, ok
    }
    """
    from datetime import datetime
    base = OUT_BASE / store
    items = []
    if not base.exists():
        return items

    for path in sorted(glob(str(base / "rebuild_*.json")), reverse=True)[:max_items]:
        try:
            data = json.loads(Path(path).read_text(encoding="utf-8"))
            # OK si todos los jobs retornan 0 (salvo DRY)
            ok = all(
                (r.get("return_code", 0) == 0)
                for r in data.get("results", [])
                if not data.get("dry_run", False)
            )
            started = data.get("started_at")
            finished = data.get("finished_at")
            duration_sec = None
            try:
                if started and finished:
                    t0 = datetime.fromisoformat(str(started))
                    t1 = datetime.fromisoformat(str(finished))
                    duration_sec = round((t1 - t0).total_seconds(), 3)
            except Exception:
                duration_sec = None

            items.append({
                "fname": Path(path).name,
                "ts": finished or started,
                "store": data.get("store") or store,
                "mode": data.get("mode"),
                "model": data.get("model"),
                "batch_size": data.get("batch_size"),
                "limit": data.get("limit"),
                "k": data.get("k"),
                "dry_run": data.get("dry_run", False),
                "total_jobs": data.get("total_jobs", 0),
                "duration_sec": duration_sec,
                "ok": ok,
            })
        except Exception:
            # Si el JSON está corrupto, aún así listamos el nombre
            items.append({
                "fname": Path(path).name,
                "ts": "?",
                "store": store,
                "mode": "?",
                "model": "?",
                "batch_size": "?",
                "limit": "?",
                "k": "?",
                "dry_run": False,
                "total_jobs": "?",
                "duration_sec": None,
                "ok": False
            })
    return items


@bp.route("/", methods=["GET"])
@bp.route("/", methods=["GET"])
def index():
    store = request.args.get("store", "faiss")
    collection = request.args.get("collection", "chunks_default")
    meta_path = OUT_BASE / store / collection / "index_meta.json"
    meta = json.loads(meta_path.read_text("utf-8")) if meta_path.exists() else None

    # Tamaño índice: recorrer recursivamente (Chroma guarda en subdirectorios)
    size_bytes = 0
    idx_dir = OUT_BASE / store / collection
    if idx_dir.exists():
        for p in idx_dir.rglob("*"):
            if p.is_file():
                try:
                    size_bytes += p.stat().st_size
                except Exception:
                    pass

    runs, sources = _load_runs_and_sources(limit_runs=80)

    rebuild_history = _list_rebuild_history(store, max_items=30)

    return render_template(
        "admin/vector_store.html",
        meta=meta,
        store=store,
        collection=collection,
        size_bytes=size_bytes,
        runs=runs,
        sources=sources,
        rebuild_history=rebuild_history,
    )



@bp.route("/build", methods=["POST"])
def build():
    store = request.form.get("store", "faiss")
    model = request.form.get("model", "sentence-transformers/all-MiniLM-L6-v2")
    batch = int(request.form.get("batch_size", "256"))
    run_id = request.form.get("run_id") or None
    source_id = request.form.get("source_id") or None
    limit = request.form.get("limit") or None
    collection = request.form.get("collection") or None
    rebuild = "rebuild" in request.form

    cmd = f"python -m scripts.index_chunks --store {store} --model {model} --batch-size {batch}"
    if run_id: cmd += f" --run-id {int(run_id)}"
    if source_id: cmd += f" --source-id {int(source_id)}"
    if limit: cmd += f" --limit {int(limit)}"
    if collection: cmd += f" --collection {collection}"
    if rebuild: cmd += " --rebuild"

    code, lines = _run_cmd(cmd)
    summary = next((l for l in reversed(lines) if l.get("event") in ("index.persist", "index.end")), None)
    flash(f"Build {'OK' if code == 0 else 'ERROR'} — {summary}", "success" if code == 0 else "danger")
    return redirect(url_for("vector_store.index", store=store, collection=collection or ""))


@bp.route("/eval", methods=["POST"])
def eval_query():
    store = request.form.get("store", "faiss")
    collection = request.form.get("collection") or "chunks_default"
    model = request.form.get("model", "sentence-transformers/all-MiniLM-L6-v2")
    k = int(request.form.get("k", "5"))
    smoke = request.form.get("smoke_query") or ""
    cmd = f"python -m scripts.index_chunks --store {store} --model {model} --collection {collection} --smoke-query {shlex.quote(smoke)} --k {k}"
    code, lines = _run_cmd(cmd)
    ts = time.strftime("%Y%m%d-%H%M%S")
    eval_dir = OUT_BASE / store / collection / "eval" / ts
    eval_dir.mkdir(parents=True, exist_ok=True)
    (eval_dir / "stdout.jsonl").write_text("\n".join(json.dumps(x, ensure_ascii=False) for x in lines), "utf-8")
    results = next((l for l in lines if l.get("event") == "smoke.results"), {"results": []})
    (eval_dir / "results.json").write_text(json.dumps(results, ensure_ascii=False, indent=2), "utf-8")
    flash(f"Eval {'OK' if code == 0 else 'ERROR'} — {len(results.get('results', []))} items", "success" if code == 0 else "danger")
    return redirect(url_for("vector_store.index", store=store, collection=collection))


# --- Helpers para reconstrucción masiva ---
def _iter_existing_collections(store: str):
    base = OUT_BASE / store
    if not base.exists():
        return
    for d in base.iterdir():
        if d.is_dir() and (d / "index_meta.json").exists():
            yield d.name

def _iter_run_ids(limit_runs: int = 200):
    runs = []
    with _open_session() as session:
        order_col = getattr(IngestionRun, "started_at", None) or getattr(IngestionRun, "created_at", None) or IngestionRun.id
        q = session.query(IngestionRun).order_by(order_col.desc()).limit(limit_runs)
        for r in q.all():
            rid = getattr(r, "id", None)
            if rid is not None:
                runs.append(int(rid))
    return runs

@bp.route("/rebuild_all", methods=["POST"])
def rebuild_all():
    store = request.form.get("store", "faiss")
    mode = request.form.get("mode", "collections")  # collections | runs
    model = request.form.get("model", "sentence-transformers/all-MiniLM-L6-v2")
    batch = int(request.form.get("batch_size", "256"))
    limit = request.form.get("limit") or None
    k = int(request.form.get("k", "5"))
    collection_prefix = request.form.get("collection_prefix", "run_")
    dry_run = "dry_run" in request.form

    results = []
    started_ts = time.strftime("%Y-%m-%dT%H:%M:%S")

    jobs = []
    if mode == "collections":
        for col in _iter_existing_collections(store):
            jobs.append({"type": "collection", "collection": col})
    else:
        for run_id in _iter_run_ids():
            col = f"{collection_prefix}{run_id}"
            jobs.append({"type": "run", "run_id": run_id, "collection": col})

    for job in jobs:
        if dry_run:
            results.append({"job": job, "status": "DRY_RUN"})
            continue

        if job["type"] == "collection":
            cmd = f"python -m scripts.index_chunks --store {store} --model {model} --batch-size {batch} --collection {job['collection']} --rebuild"
            if limit: cmd += f" --limit {int(limit)}"
        else:
            cmd = f"python -m scripts.index_chunks --store {store} --model {model} --batch-size {batch} --run-id {int(job['run_id'])} --collection {job['collection']} --rebuild"
            if limit: cmd += f" --limit {int(limit)}"

        code, lines = _run_cmd(cmd)
        summary = next((l for l in reversed(lines) if isinstance(l, dict) and l.get("event") in ("index.persist","index.end")), None)

        smoke_cmd = f"python -m scripts.index_chunks --store {store} --model {model} --collection {job['collection']} --smoke-query sanity --k {k}"
        smoke_code, smoke_lines = _run_cmd(smoke_cmd)
        smoke_res = next((l for l in smoke_lines if isinstance(l, dict) and l.get("event")=="smoke.results"), {"results":[]})

        results.append({
            "job": job,
            "cmd": cmd,
            "return_code": code,
            "summary": summary,
            "smoke_return_code": smoke_code,
            "smoke_n": len(smoke_res.get("results", []))
        })

    ts = time.strftime("%Y%m%d-%H%M%S")
    out = {
        "store": store, "mode": mode, "model": model, "batch_size": batch,
        "limit": int(limit) if limit else None, "k": k, "dry_run": dry_run,
        "started_at": started_ts, "finished_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "total_jobs": len(jobs), "results": results,
    }
    (OUT_BASE / store).mkdir(parents=True, exist_ok=True)
    (OUT_BASE / store / f"rebuild_{ts}.json").write_text(json.dumps(out, ensure_ascii=False, indent=2), "utf-8")

    ok = all(r.get("return_code", 0) == 0 for r in results if not dry_run)
    msg = f"Reconstrucción {'DRY_RUN ' if dry_run else ''}{'OK' if ok else 'con ERRORES'} — {len(jobs)} jobs"
    flash(msg, "success" if ok else "danger")
    return redirect(url_for("vector_store.index", store=store))


# === NUEVO HISTÓRICO: servir JSON de reconstrucción de forma segura ===
@bp.route("/rebuild_file/<store>/<path:fname>", methods=["GET"])
def rebuild_file(store: str, fname: str):
    """
    Devuelve un rebuild_*.json del directorio models/<store>.
    """
    base = OUT_BASE / store
    # Validación simple: sólo permitimos archivos con patrón rebuild_*.json
    if not fname.startswith("rebuild_") or not fname.endswith(".json"):
        flash("Nombre de archivo inválido.", "danger")
        return redirect(url_for("vector_store.index", store=store))
    return send_from_directory(base, fname, mimetype="application/json", as_attachment=False)
