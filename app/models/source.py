from sqlalchemy import Integer, String, JSON
from sqlalchemy.orm import Mapped, mapped_column, relationship
from app.extensions.db import Base

class Source(Base):
    __tablename__ = "sources"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    type: Mapped[str] = mapped_column(String(20), nullable=False)  # 'web' | 'docs' | ...
    url: Mapped[str] = mapped_column(String(1024), nullable=False)
    name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    config: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)

    # Relaciones (imprescindibles para cuadrar back_populates)
    runs = relationship("IngestionRun", back_populates="source", cascade="all, delete-orphan")
    documents = relationship("Document", back_populates="source", cascade="all, delete-orphan")
    chunks = relationship("Chunk", back_populates="source", cascade="all, delete-orphan")
