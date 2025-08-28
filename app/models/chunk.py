from __future__ import annotations

from typing import Any, Optional
from sqlalchemy import DateTime, ForeignKey, Index, Integer, JSON, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column, relationship
from app.extensions.db import Base


class Chunk(Base):
    __tablename__ = "chunk"

    chunk_id: Mapped[str] = mapped_column(String(160), primary_key=True)
    doc_id: Mapped[str] = mapped_column(String(128), ForeignKey("document.doc_id", ondelete="CASCADE"), nullable=False)
    position: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    content: Mapped[str] = mapped_column(Text, nullable=False)
    tokens: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)

    lang: Mapped[Optional[str]] = mapped_column(String(10), nullable=True)
    title: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)

    retrieval_tags: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    provenance: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)

    created_at: Mapped[Any] = mapped_column(DateTime, nullable=False, server_default=func.now())

    document: Mapped["Document"] = relationship(back_populates="chunks")

    __table_args__ = (Index("ix_chunk_doc_pos", "doc_id", "position"),)