# app/blueprints/admin/routes_data_sources.py
from __future__ import annotations
from flask import Blueprint, render_template, flash
from sqlalchemy import func
from pathlib import Path
import os

import app.extensions.db as db
from app.models import Source, Document, Chunk, IngestionRun  # usamos IngestionRun para stats

# Carga perezosa de networkx: solo si hay GraphML que contar
try:
    import networkx as nx  # type: ignore
except Exception:  # pragma: no cover
    nx = None  # si falta networkx, seguimos mostrando el resto de la página

bp_ds = Blueprint("data_sources", __name__, url_prefix="/admin/data-sources")


def _kg_path(namespace: str, emb_dim: int = 384) -> Path:
    """
    Ruta canónica del GraphML:
    models/kg/<namespace>/emb-<emb_dim>/graph_chunk_entity_relation.graphml
    """
    base = os.getenv("LIGHTRAG_WORKDIR", "models/kg")
    return Path(base).joinpath(namespace, f"emb-{emb_dim}", "graph_chunk_entity_relation.graphml").resolve()


def _kg_info(namespace: str, emb_dim: int = 384) -> dict:
    """
    Devuelve: {namespace, path, exists, nodes, edges}
    Cuenta nodos/aristas si networkx está disponible y el fichero existe.
    """
    p = _kg_path(namespace, emb_dim=emb_dim)
    exists = p.exists()
    nodes = edges = None
    if exists and nx is not None:
        try:
            g = nx.read_graphml(p)  # carga ligera suficiente para contar
            nodes = g.number_of_nodes()
            edges = g.number_of_edges()
        except Exception:
            # tolerante a errores de parseo o ficheros bloqueados
            nodes = edges = None
    return {
        "namespace": namespace,
        "path": str(p),
        "exists": exists,
        "nodes": nodes,
        "edges": edges,
        "emb_dim": emb_dim,
    }


@bp_ds.route("/", methods=["GET"])
def index():
    """Hub de fuentes: accesos rápidos + listado + estadísticas por tipo + Knowledge Graphs."""
    # 1) Listado de fuentes
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

    # 3) Knowledge Graphs (smartcity y sia). Si 'sia' aún no existe, aparecerá como 'No generado'
    kg_sources = [
        _kg_info("smartcity", emb_dim=384),
        _kg_info("sia", emb_dim=384),
    ]

    return render_template(
        "admin/data_sources.html",
        sources=sources,
        stats_by_type=stats_by_type,
        kg_sources=kg_sources,
    )


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
