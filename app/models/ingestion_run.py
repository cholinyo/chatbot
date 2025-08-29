from sqlalchemy import Integer, String, JSON, ForeignKey, DateTime
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.sql import func
from app.extensions.db import Base

class IngestionRun(Base):
    __tablename__ = "ingestion_runs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    source_id: Mapped[int] = mapped_column(ForeignKey("sources.id"), index=True, nullable=False)

    status: Mapped[str] = mapped_column(String(20), nullable=False, default="running")
    # ¡OJO!: no usar 'metadata' como atributo de clase en modelos declarativos
    meta: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)

    created_at: Mapped[str] = mapped_column(DateTime(timezone=True), server_default=func.now())

    # Relaciones
    source = relationship("Source", back_populates="runs")
