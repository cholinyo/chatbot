# scripts/validate_metadata.py
from app.extensions.db import SessionLocal
from app.models import Chunk
import random, sys
k = int(sys.argv[1]) if len(sys.argv)>1 else 5
session = SessionLocal()
ids = [r[0] for r in session.query(Chunk.id).all()]
sample = random.sample(ids, min(500, len(ids)))
ok = 0
for cid, meta in session.query(Chunk.id, Chunk.meta).filter(Chunk.id.in_(sample)).all():
    fields = ["document_title","document_path","chunk_index","text"]
    ok += sum(1 for f in fields if (meta or {}).get(f) not in (None,""))
coverage = ok / (len(sample)*4)
print({"sample": len(sample), "fields": 4, "coverage": coverage})
