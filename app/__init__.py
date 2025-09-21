# app/__init__.py
from __future__ import annotations

import os
import secrets
from pathlib import Path
from typing import Any, Dict, Optional

try:
    import tomllib  # Python 3.11+
except Exception:  # pragma: no cover
    tomllib = None  # type: ignore

try:
    from dotenv import load_dotenv  # opcional
except Exception:  # pragma: no cover
    load_dotenv = None  # type: ignore

from flask import Flask, jsonify

from app.extensions.logging import init_logging
from app.extensions.db import init_engine, init_session, create_all

# Admin/core blueprints (seguros de importar a nivel módulo)
from app.blueprints.admin.routes_main import bp as bp_admin_home
from app.blueprints.admin.routes_data_sources import bp_ds
from app.blueprints.admin.routes_ingesta_docs import bp as bp_ingesta_docs
from app.blueprints.admin.routes_ingesta_web import bp_ingesta_web
from app.blueprints.admin.routes_vector_store import bp as bp_vector_store
from app.blueprints.admin.rag_routes import admin_rag_bp
# IMPORTANTE: NO importamos aquí routes_knowledge_graph para evitar cargar LightRAG antes de tiempo.


def _load_settings(path: str = "config/settings.toml") -> Dict[str, Any]:
    p = Path(path)
    if not p.exists() or tomllib is None:
        return {}
    try:
        with p.open("rb") as f:
            return tomllib.load(f)  # type: ignore[attr-defined]
    except Exception:
        return {}


def create_app(config_override: Optional[Dict[str, Any]] = None) -> Flask:
    """Factory principal."""
    # 1) .env (si está disponible)
    if load_dotenv:
        load_dotenv()

    app = Flask(__name__)

    # 2) SECRET_KEY SIEMPRE (antes de registrar blueprints o usar flash)
    secret = os.getenv("FLASK_SECRET_KEY") or os.getenv("SECRET_KEY")
    if not secret:
        key_file = Path("data/secret_key.txt")
        try:
            if key_file.exists():
                secret = key_file.read_text(encoding="utf-8").strip()
            else:
                key_file.parent.mkdir(parents=True, exist_ok=True)
                secret = secrets.token_hex(32)
                key_file.write_text(secret, encoding="utf-8")
        except Exception:
            # último recurso no persistido
            secret = secrets.token_hex(32)
    app.config["SECRET_KEY"] = secret

    # 3) Defaults de app
    app.config.setdefault("APP_NAME", "Prototipo_chatbot")
    app.config.setdefault(
        "SQLALCHEMY_DATABASE_URI",
        os.getenv("SQLALCHEMY_DATABASE_URI", "sqlite:///data/processed/tracking.sqlite"),
    )
    app.config.setdefault("MODELS_DIR", "models")

    # 4) Mezclar settings.toml + overrides
    _ = _load_settings()
    if config_override:
        app.config.update(config_override)

    # 5) Logging temprano
    init_logging(app)

    # 6) Engine/sesión + create_all
    if str(app.config["SQLALCHEMY_DATABASE_URI"]).startswith("sqlite"):
        Path("data/processed").mkdir(parents=True, exist_ok=True)
        # (Opcional) preparar directorios de índices para evitar fallos de escritura
        Path("models/faiss").mkdir(parents=True, exist_ok=True)
        Path("models/chroma").mkdir(parents=True, exist_ok=True)
        Path("models/kg").mkdir(parents=True, exist_ok=True)

    engine = init_engine(app.config["SQLALCHEMY_DATABASE_URI"])
    init_session(engine)

    # Importa modelos ANTES de create_all
    from app.models import source, ingestion_run, document, chunk  # noqa: F401
    create_all(engine)

    # 7) Blueprints seguros
    app.register_blueprint(bp_admin_home)
    app.register_blueprint(bp_ds)
    app.register_blueprint(bp_ingesta_docs)
    app.register_blueprint(bp_ingesta_web)
    app.register_blueprint(bp_vector_store)
    app.register_blueprint(admin_rag_bp)

    # 7.1) Registrar blueprint del KG de forma perezosa
    # Evitamos importar LightRAG/LLM en el import de app para que los scripts CLI funcionen.
    try:
        from app.blueprints.admin.routes_knowledge_graph import bp as bp_kg  # import aquí
        app.register_blueprint(bp_kg)
    except Exception as e:
        app.logger.warning("Blueprint KG no registrado (deferred import falló): %s", e)

    # (Útil para depurar rutas una vez todo está registrado)
    print("== URL MAP ==")
    print(app.url_map)

    try:
        from app.blueprints.ingestion.routes import bp as ingestion_bp  # type: ignore
        app.register_blueprint(ingestion_bp, url_prefix="/ingestion")
    except Exception as e:
        app.logger.warning("Ingestion blueprint no registrado: %s", e)

    # 8) Healthcheck
    @app.get("/status/ping")
    def status_ping():  # type: ignore[override]
        return jsonify({"status": "ok", "app": app.config.get("APP_NAME")})

    # 9) Shell context
    @app.shell_context_processor
    def _ctx():
        ctx = {}
        try:
            from app.extensions.db import SessionLocal as db_session  # type: ignore
            ctx["db_session"] = db_session
        except Exception:
            pass
        try:
            from app.models import Source, Document, Chunk, IngestionRun  # type: ignore
            ctx.update(Source=Source, Document=Document, Chunk=Chunk, IngestionRun=IngestionRun)
        except Exception:
            pass
        return ctx

    # 10) Helpers Jinja
    from datetime import datetime
    from flask import url_for, current_app

    @app.context_processor
    def _inject_helpers():
        def url_for_safe(endpoint: str, **values) -> str:
            try:
                return url_for(endpoint, **values)
            except Exception:
                return "#"

        def has_endpoint(endpoint: str) -> bool:
            try:
                return any(rule.endpoint == endpoint for rule in current_app.url_map.iter_rules())
            except Exception:
                return False

        return dict(
            app_name=app.config.get("APP_NAME", "Prototipo_chatbot"),
            current_year=datetime.now().year,
            url_for_safe=url_for_safe,
            has_endpoint=has_endpoint,
        )

    app.logger.info("App creada: %s", app.config.get("APP_NAME"))
    return app
