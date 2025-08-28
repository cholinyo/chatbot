from __future__ import annotations

from typing import Any, Optional
from sqlalchemy import Boolean, CheckConstraint, DateTime, Index, JSON, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column, relationship
from app.extensions.db import Base


class Source(Base):
    __tablename__ = "source"

    id: Mapped[str] = mapped_column(String(100), primary_key=True)
    type: Mapped[str] = mapped_column(String(16), nullable=False)  # document|web|api|db

    name: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    uri: Mapped[Optional[str]] = mapped_column(String(1024), nullable=True)

    config: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    schedule: Mapped[Optional[dict[str, Any]]] = mapped_column(JSON, nullable=True)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    created_at: Mapped[Any] = mapped_column(DateTime, nullable=False, server_default=func.now())
    updated_at: Mapped[Any] = mapped_column(DateTime, nullable=False, server_default=func.now(), onupdate=func.now())

    documents: Mapped[list["Document"]] = relationship(back_populates="source", cascade="all, delete-orphan", passive_deletes=True)
    runs: Mapped[list["IngestionRun"]] = relationship(back_populates="source", cascade="all, delete-orphan", passive_deletes=True)

    __table_args__ = (
        CheckConstraint("type in ('document','web','api','db')", name="ck_source_type"),
        Index("ix_source_type_enabled", "type", "enabled"),
    )