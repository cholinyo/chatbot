# app/extensions/db.py
from __future__ import annotations

import os
from contextlib import contextmanager
from typing import Iterator, Optional

from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import declarative_base, sessionmaker, Session

# Base declarativa
Base = declarative_base()

# Objetos globales inicializados en create_app()
_engine: Optional[Engine] = None
SessionLocal: Optional[sessionmaker] = None


def _default_db_url() -> str:
    return "sqlite:///data/processed/tracking.sqlite"


def _ensure_sqlite_dir(db_url: str) -> None:
    if not db_url.startswith("sqlite"):
        return
    # Normaliza ruta de fichero sqlite y crea carpeta
    path = db_url.replace("sqlite:///", "", 1).replace("sqlite:////", "/", 1)
    folder = os.path.dirname(path)
    if folder and not os.path.exists(folder):
        os.makedirs(folder, exist_ok=True)


def init_engine(db_url: Optional[str] = None, *, echo: bool = False) -> Engine:
    """
    Crea el Engine global una sola vez y devuelve la instancia.
    """
    global _engine
    if _engine is not None:
        return _engine

    db_url = db_url or os.getenv("SQLALCHEMY_DATABASE_URI") or _default_db_url()
    _ensure_sqlite_dir(db_url)
    _engine = create_engine(db_url, future=True, echo=echo)

    # Smoke test (no obligatorio)
    try:
        with _engine.connect() as conn:
            conn.execute(text("SELECT 1"))
    except Exception:
        pass

    return _engine


def init_session(engine: Engine) -> sessionmaker:
    """
    Configura la factoría de sesiones global (SessionLocal) con el engine dado.
    """
    global SessionLocal
    if SessionLocal is None:
        SessionLocal = sessionmaker(
            bind=engine,
            autoflush=False,
            autocommit=False,
            expire_on_commit=False,
            future=True,
        )
    else:
        SessionLocal.configure(bind=engine)
    return SessionLocal


@contextmanager
def get_session() -> Iterator[Session]:
    """
    Context manager para abrir una sesión siempre segura, incluso si
    el módulo fue importado antes de inicializarse.
    """
    if SessionLocal is None:
        # Último recurso: inicializa con el engine por defecto
        engine = init_engine()
        init_session(engine)

    assert SessionLocal is not None, "SessionLocal no inicializado"
    session: Session = SessionLocal()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def create_all(engine: Optional[Engine] = None) -> None:
    engine = engine or _engine or init_engine()
    Base.metadata.create_all(bind=engine)


def drop_all(engine: Optional[Engine] = None) -> None:
    engine = engine or _engine or init_engine()
    Base.metadata.drop_all(bind=engine)


def get_engine() -> Engine:
    return _engine or init_engine()
