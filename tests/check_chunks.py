# tests/check_chunks.py
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # permite importar app

from app import create_app
from app.extensions.db import get_session
from app.models import Chunk, Document, Source

app = create_app()

with app.app_context():
    with get_session() as s:
        chunks = (
            s.query(Chunk, Document, Source)
            .join(Document, Chunk.document_id == Document.id)
            .join(Source, Document.source_id == Source.id)
            .order_by(Chunk.id.desc())
            .limit(110)
            .all()
)

    for chunk, doc, src in chunks:
        print(f"\n📄 Chunk ID: {chunk.id} | Tipo: {src.type} | Fuente ID: {src.id}")
        print(f"↪ Doc path: {doc.path}")
        print(f"↪ Preview: {chunk.content[:200]}...\n{'-'*50}")
