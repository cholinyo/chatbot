from __future__ import annotations
from sqlalchemy import Integer, JSON, ForeignKey, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship
from app.extensions.db import Base

class Chunk(Base):
    __tablename__ = "chunks"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    source_id: Mapped[int] = mapped_column(ForeignKey("sources.id"), index=True, nullable=False)
    document_id: Mapped[int | None] = mapped_column(ForeignKey("documents.id"), nullable=True)

    index: Mapped[int | None] = mapped_column(Integer, nullable=True)
    text: Mapped[str] = mapped_column(Text, nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)

    # Igual que en Document: atributo 'meta' para el JSON
    meta: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)

    # Relaciones
    source = relationship("Source", back_populates="chunks")
    document = relationship("Document", back_populates="chunks")
