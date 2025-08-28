"""
Application factory for the TFM RAG project (Flask + SQLAlchemy 2.x).

Purpose
- Centraliza la carga de configuración (.env + settings.toml) y la init de extensiones.
- Inicializa la base de control y crea las tablas ORM (tracking.sqlite) en el arranque.
- Configura el sistema de logging (consola + ficheros rotados) antes de registrar blueprints.

Validación rápida
    # Desde la raíz del proyecto
    python -c "from app import create_app; create_app(); print('OK')"

    # Logs (por defecto en data/logs)
    Get-Content .\\data\\logs\\app.log -Wait

Notas
- No dependemos de Flask-SQLAlchemy. Usamos SQLAlchemy ORM vía app.extensions.db
- El módulo de logging del proyecto es app.extensions.logging (no choca con 'logging' stdlib)
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Dict, Optional

try:
    # Python 3.11+: parser TOML estándar
    import tomllib  # type: ignore[attr-defined]
except Exception:  # pragma: no cover
    tomllib = None  # type: ignore

try:
    # Opcional: cargar .env en desarrollo si python-dotenv está instalado
    from dotenv import load_dotenv  # type: ignore
except Exception:  # pragma: no cover
    load_dotenv = None  # type: ignore

from flask import Flask, jsonify

# Extensiones propias
from app.extensions.logging import init_logging
from app.extensions.db import init_engine, init_session, create_all


# ------------------------------
# Helpers de configuración
# ------------------------------

def _load_settings(path: str = "config/settings.toml") -> Dict[str, Any]:
    """Carga settings TOML si existe. Devuelve dict (vacío si no hay fichero)."""
    p = Path(path)
    if not p.exists() or tomllib is None:
        return {}
    try:
        with p.open("rb") as f:
            return tomllib.load(f)  # type: ignore[attr-defined]
    except Exception:
        return {}


# ------------------------------
# Application factory
# ------------------------------

def create_app(config_override: Optional[Dict[str, Any]] = None) -> Flask:
    """Crea y configura la instancia Flask.

    - Carga .env (si está disponible) para desarrollo local.
    - Carga config/settings.toml (si existe) con settings no secretos.
    - Inicializa el sistema de logging (consola + ficheros rotados).
    - Inicializa SQLAlchemy (engine/sesión) y crea tablas ORM.
    - Registra blueprints (si están disponibles).
    """
    # 1) Cargar variables de entorno desde .env (opcional)
    if load_dotenv:
        load_dotenv()  # no-op si no existe .env

    app = Flask(__name__)

    # 2) Defaults seguros para primer arranque
    app.config.setdefault("APP_NAME", "Prototipo_chatbot")
    app.config.setdefault(
        "SQLALCHEMY_DATABASE_URI",
        os.getenv("SQLALCHEMY_DATABASE_URI", "sqlite:///data/processed/tracking.sqlite"),
    )

    # 3) Mezclar settings.toml (si lo tienes) y overrides explícitos
    _ = _load_settings()  # reservado para mapear claves adicionales si lo necesitas
    if config_override:
        app.config.update(config_override)

    # 4) Logging lo antes posible (para capturar mensajes de init)
    init_logging(app)

    # 5) Asegurar carpeta del SQLite y levantar engine/sesión
    if str(app.config["SQLALCHEMY_DATABASE_URI"]).startswith("sqlite"):
        Path("data/processed").mkdir(parents=True, exist_ok=True)

    engine = init_engine(app.config["SQLALCHEMY_DATABASE_URI"])
    init_session(engine)

    # 6) Importar modelos ANTES de create_all
    from app.models import source, ingestion_run, document, chunk  # noqa: F401
    create_all(engine)

    # 7) Registrar blueprints (tolerante si aún no existen)
    try:
        from app.blueprints.ingestion.routes import bp as ingestion_bp  # type: ignore
        app.register_blueprint(ingestion_bp, url_prefix="/ingestion")
    except Exception as e:  # pragma: no cover - útil en fases tempranas
        app.logger.warning("Ingestion blueprint no registrado: %s", e)

    # 8) Endpoint mínimo de salud
    @app.get("/status/ping")
    def status_ping():  # type: ignore[override]
        return jsonify({"status": "ok", "app": app.config.get("APP_NAME")})

    app.logger.info("App creada: %s", app.config.get("APP_NAME"))
    return app
