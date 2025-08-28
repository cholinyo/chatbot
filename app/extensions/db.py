from __future__ import annotations

import os
from contextlib import contextmanager
from typing import Iterator, Optional

from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import declarative_base, sessionmaker, Session

Base = declarative_base()
_engine: Optional[Engine] = None
_SessionLocal: Optional[sessionmaker] = None


def _default_db_url() -> str:
    return "sqlite:///data/processed/tracking.sqlite"


def _ensure_sqlite_dir(db_url: str) -> None:
    if not db_url.startswith("sqlite"):
        return
    path = db_url.replace("sqlite:///", "", 1).replace("sqlite:////", "/", 1)
    folder = os.path.dirname(path)
    if folder and not os.path.exists(folder):
        os.makedirs(folder, exist_ok=True)


def init_engine(db_url: Optional[str] = None, *, echo: bool = False) -> Engine:
    global _engine
    if _engine is not None:
        return _engine
    db_url = db_url or os.getenv("SQLALCHEMY_DATABASE_URI") or _default_db_url()
    _ensure_sqlite_dir(db_url)
    _engine = create_engine(db_url, future=True, echo=echo)
    try:
        with _engine.connect() as conn:
            conn.execute(text("SELECT 1"))
    except Exception:
        pass
    return _engine


def init_session(engine: Optional[Engine] = None) -> sessionmaker:
    global _SessionLocal
    if _SessionLocal is not None:
        return _SessionLocal
    engine = engine or _engine or init_engine()
    _SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False, future=True, class_=Session)
    return _SessionLocal


@contextmanager
def get_session() -> Iterator[Session]:
    if _SessionLocal is None:
        init_session()
    assert _SessionLocal is not None
    session: Session = _SessionLocal()
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


def get_session_factory() -> sessionmaker:
    return _SessionLocal or init_session()