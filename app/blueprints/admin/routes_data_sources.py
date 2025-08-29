from __future__ import annotations
from flask import Blueprint, render_template, flash
from sqlalchemy import func

import app.extensions.db as db
from app.models import Source, Document, Chunk, IngestionRun  # usamos IngestionRun para stats

bp_ds = Blueprint("data_sources", __name__, url_prefix="/admin/data-sources")


@bp_ds.route("/", methods=["GET"])
def index():
    """Hub de fuentes: accesos rápidos + listado + estadísticas por tipo."""
    # 1) Listado
    try:
        with db.get_session() as s:
            sources = s.query(Source).order_by(Source.id.desc()).all()
    except Exception as e:
        sources = []
        flash(f"Aviso: no se pudo cargar el listado de fuentes ({e.__class__.__name__}).", "warning")

    # 2) Stats por tipo (tolerantes a fallo)
    stats_by_type = {}
    try:
        with db.get_session() as s:
            n_sources = dict(
                s.query(Source.type, func.count(Source.id)).group_by(Source.type).all()
            )
            docs = dict(
                s.query(Source.type, func.count(Document.id))
                 .select_from(Source)
                 .outerjoin(Document, Document.source_id == Source.id)
                 .group_by(Source.type)
                 .all()
            )
            chunks = dict(
                s.query(Source.type, func.count(Chunk.id))
                 .select_from(Source)
                 .outerjoin(Chunk, Chunk.source_id == Source.id)
                 .group_by(Source.type)
                 .all()
            )
            runs_total = dict(
                s.query(Source.type, func.count(IngestionRun.id))
                 .select_from(Source)
                 .outerjoin(IngestionRun, IngestionRun.source_id == Source.id)
                 .group_by(Source.type)
                 .all()
            )
            runs_done = dict(
                s.query(Source.type, func.count(IngestionRun.id))
                 .select_from(Source)
                 .outerjoin(IngestionRun, IngestionRun.source_id == Source.id)
                 .filter(IngestionRun.status == "done")
                 .group_by(Source.type)
                 .all()
            )
            runs_error = dict(
                s.query(Source.type, func.count(IngestionRun.id))
                 .select_from(Source)
                 .outerjoin(IngestionRun, IngestionRun.source_id == Source.id)
                 .filter(IngestionRun.status == "error")
                 .group_by(Source.type)
                 .all()
            )
            last_run = dict(
                s.query(Source.type, func.max(IngestionRun.created_at))
                 .select_from(Source)
                 .outerjoin(IngestionRun, IngestionRun.source_id == Source.id)
                 .group_by(Source.type)
                 .all()
            )

        all_types = set().union(n_sources, docs, chunks, runs_total, runs_done, runs_error, last_run)
        for t in sorted(all_types):
            stats_by_type[t] = {
                "sources": int(n_sources.get(t, 0) or 0),
                "documents": int(docs.get(t, 0) or 0),
                "chunks": int(chunks.get(t, 0) or 0),
                "runs_total": int(runs_total.get(t, 0) or 0),
                "runs_done": int(runs_done.get(t, 0) or 0),
                "runs_error": int(runs_error.get(t, 0) or 0),
                "last_run": last_run.get(t),
            }
    except Exception as e:
        stats_by_type = {}
        flash(f"Aviso: no se pudieron calcular las estadísticas ({e.__class__.__name__}).", "warning")

    return render_template("admin/data_sources.html",
                           sources=sources,
                           stats_by_type=stats_by_type)


# Ruta de diagnóstico rápida (útil para comprobar datos/ruta)
@bp_ds.route("/_debug", methods=["GET"])
def _debug():
    with db.get_session() as s:
        return {
            "sources": s.query(Source).count(),
            "documents": s.query(Document).count(),
            "chunks": s.query(Chunk).count(),
            "runs": s.query(IngestionRun).count(),
        }
