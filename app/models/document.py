from __future__ import annotations

from typing import Any, Optional
from sqlalchemy import DateTime, ForeignKey, Index, Integer, JSON, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column, relationship
from app.extensions.db import Base


class Document(Base):
    __tablename__ = "document"

    doc_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    source_id: Mapped[Optional[str]] = mapped_column(String(100), ForeignKey("source.id", ondelete="SET NULL"), nullable=True)
    source_type: Mapped[str] = mapped_column(String(16), nullable=False)

    uri: Mapped[str] = mapped_column(String(2048), nullable=False)

    title: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)
    lang: Mapped[Optional[str]] = mapped_column(String(10), nullable=True)
    mime: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    version: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)

    collected_at: Mapped[Optional[Any]] = mapped_column(DateTime, nullable=True)
    size_bytes: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)

    origin_hash: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    normalized_hash: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)

    license: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    confidentiality: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)

    created_at: Mapped[Any] = mapped_column(DateTime, nullable=False, server_default=func.now())

    source: Mapped[Optional["Source"]] = relationship(back_populates="documents")
    chunks: Mapped[list["Chunk"]] = relationship(back_populates="document", cascade="all, delete-orphan", passive_deletes=True)

    __table_args__ = (
        Index("ix_document_source", "source_type", "source_id"),
        Index("ix_document_uri", "uri"),
    )