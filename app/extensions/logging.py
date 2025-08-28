from __future__ import annotations

import logging
import os
from logging.handlers import RotatingFileHandler
from pathlib import Path

DEFAULT_DIR = "data/logs"


def _to_int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, default))
    except Exception:
        return default


def _level_from_env(var: str, default: str = "INFO") -> int:
    value = os.getenv(var, default).upper()
    return getattr(logging, value, logging.INFO)


def init_logging(app=None) -> logging.Logger:
    """Configura logging de app y logger específico de ingesta.
    Env vars soportadas:
      - LOG_DIR (por defecto data/logs)
      - LOG_LEVEL (DEBUG|INFO|WARNING|ERROR|CRITICAL)
      - LOG_FILE_MAX_BYTES (por defecto 5MB)
      - LOG_FILE_BACKUPS (por defecto 5)
      - LOG_FORMAT (formato; por defecto ISO timestamp + nivel + logger + msg)
    """
    log_dir = Path(os.getenv("LOG_DIR", DEFAULT_DIR))
    log_dir.mkdir(parents=True, exist_ok=True)

    level = _level_from_env("LOG_LEVEL", "INFO")
    max_bytes = _to_int("LOG_FILE_MAX_BYTES", 5 * 1024 * 1024)
    backups = _to_int("LOG_FILE_BACKUPS", 5)
    fmt = os.getenv("LOG_FORMAT", "%(asctime)s %(levelname)s %(name)s - %(message)s")
    datefmt = "%Y-%m-%dT%H:%M:%S%z"

    # Root logger
    root = logging.getLogger()
    root.setLevel(level)

    formatter = logging.Formatter(fmt=fmt, datefmt=datefmt)

    # Console
    ch = logging.StreamHandler()
    ch.setLevel(level)
    ch.setFormatter(formatter)
    root.addHandler(ch)

    # app.log (todo)
    app_fh = RotatingFileHandler(log_dir / "app.log", maxBytes=max_bytes, backupCount=backups, encoding="utf-8")
    app_fh.setLevel(level)
    app_fh.setFormatter(formatter)
    root.addHandler(app_fh)

    # ingestion.log (solo logger 'ingestion')
    ing_logger = logging.getLogger("ingestion")
    ing_logger.setLevel(level)
    ing_logger.propagate = True  # que suba también a root/console/app.log

    ing_fh = RotatingFileHandler(log_dir / "ingestion.log", maxBytes=max_bytes, backupCount=backups, encoding="utf-8")
    ing_fh.setLevel(level)
    ing_fh.setFormatter(formatter)
    ing_logger.addHandler(ing_fh)

    if app is not None:
        # Alinear el app.logger con la configuración
        app.logger.handlers = root.handlers
        app.logger.setLevel(level)
        app.logger.propagate = False

    return root