"""
Minimal JSON API for document ingestion.

Endpoints (prefijados con /ingestion desde create_app):
- POST   /ingestion/sources                 -> crea Source (type=document)
- POST   /ingestion/run/<source_id>         -> ejecuta ingesta
- GET    /ingestion/runs/<source_id>/latest -> último run
"""
from __future__ import annotations

import logging
from flask import Blueprint, jsonify, request

from app.extensions.db import get_session
from app.models.source import Source
from app.models.ingestion_run import IngestionRun
from app.blueprints.ingestion.services import ingest_documents_by_source_id

bp = Blueprint("ingestion", __name__)
log = logging.getLogger(__name__)


@bp.post("/sources")
def create_source():
    data = request.get_json(silent=True) or {}
    required = {"id", "type", "name", "config"}
    missing = [k for k in required if k not in data]
    if missing:
        return jsonify({"error": f"Missing fields: {', '.join(missing)}"}), 400
    if data.get("type") != "document":
        return jsonify({"error": "Only type='document' is supported here"}), 400

    with get_session() as s:
        if s.get(Source, data["id"]) is not None:
            return jsonify({"error": "Source already exists"}), 409
        src = Source(
            id=data["id"],
            type=data["type"],
            name=data["name"],
            description=data.get("description"),
            uri=data.get("uri"),
            config=data.get("config") or {},
            schedule=data.get("schedule"),
            enabled=bool(data.get("enabled", True)),
        )
        s.add(src)
        log.info("Created source %s", src.id)
        return jsonify({"ok": True, "id": src.id})


@bp.post("/run/<source_id>")
def run_ingestion(source_id: str):
    try:
        run = ingest_documents_by_source_id(source_id)
    except ValueError as e:
        return jsonify({"error": str(e)}), 404
    return jsonify({
        "run_id": run.run_id,
        "source_id": run.source_id,
        "status": run.status,
        "stats": run.stats,
    })


@bp.get("/runs/<source_id>/latest")
def latest_run(source_id: str):
    with get_session() as s:
        run = (
            s.query(IngestionRun)
            .filter(IngestionRun.source_id == source_id)
            .order_by(IngestionRun.started_at.desc())
            .first()
        )
        if not run:
            return jsonify({"error": "No runs found"}), 404
        return jsonify({
            "run_id": run.run_id,
            "status": run.status,
            "started_at": str(run.started_at),
            "ended_at": str(run.ended_at) if run.ended_at else None,
            "stats": run.stats,
        })
