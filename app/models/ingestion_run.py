from __future__ import annotations

from uuid import uuid4
from typing import Any, Optional
from sqlalchemy import DateTime, ForeignKey, Index, JSON, String, func
from sqlalchemy.orm import Mapped, mapped_column, relationship
from app.extensions.db import Base


class IngestionRun(Base):
    __tablename__ = "ingestion_run"

    run_id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    source_id: Mapped[str] = mapped_column(String(100), ForeignKey("source.id", ondelete="CASCADE"), nullable=False)
    source_type: Mapped[str] = mapped_column(String(16), nullable=False)
    source_scope: Mapped[Optional[str]] = mapped_column(String(512), nullable=True)
    params: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)

    started_at: Mapped[Any] = mapped_column(DateTime, nullable=False, server_default=func.now())
    ended_at: Mapped[Optional[Any]] = mapped_column(DateTime, nullable=True)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="running")
    stats: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    log_path: Mapped[Optional[str]] = mapped_column(String(1024), nullable=True)

    source: Mapped["Source"] = relationship(back_populates="runs")

    __table_args__ = (
        Index("ix_ingestion_run_source", "source_id", "started_at"),
        Index("ix_ingestion_run_status", "status"),
    )

    @property
    def duration_seconds(self) -> Optional[float]:
        if self.ended_at is None:
            return None
        try:
            return (self.ended_at - self.started_at).total_seconds()
        except Exception:
            return None