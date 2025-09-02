# app/models/chunk.py
# -*- coding: utf-8 -*-
from __future__ import annotations

from sqlalchemy import Integer, Text, JSON, ForeignKey, DateTime
from sqlalchemy.orm import Mapped, mapped_column, relationship
from datetime import datetime

# 🔧 IMPORTANTE: importa Base desde tu capa de BBDD
from app.extensions.db import Base  # Asegúrate de que aquí vive tu Declarative Base

class Chunk(Base):
    __tablename__ = "chunks"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    source_id: Mapped[int] = mapped_column(ForeignKey("sources.id"), index=True, nullable=False)
    document_id: Mapped[int | None] = mapped_column(ForeignKey("documents.id"), nullable=True)

    # Mapea la columna SQL "index" a un atributo Python seguro: "ordinal"
    # Esto evita choques con la palabra reservada y coincide con lo que usa el script de ingesta web.
    ordinal: Mapped[int | None] = mapped_column("index", Integer, nullable=True)

    # En tu esquema, content es NOT NULL y text puede ser NULL
    text: Mapped[str | None] = mapped_column(Text, nullable=True)
    content: Mapped[str] = mapped_column(Text, nullable=False)

    # JSON requerido en tu schema actual
    meta: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)

    created_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True, default=datetime.utcnow)

    # Relaciones
    source = relationship("Source", back_populates="chunks")
    document = relationship("Document", back_populates="chunks")
