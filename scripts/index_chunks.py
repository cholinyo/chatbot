#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Indexación de Chunks a Vector Store (FAISS/Chroma)
--------------------------------------------------
- Selecciona Chunk desde SQLite (vía SQLAlchemy) usando filtros (--run-id/--source-id/--limit).
- Genera embeddings (Sentence-Transformers) por lotes.
- Persiste en:
    * FAISS:  models/faiss/<collection>/{index.faiss, ids.npy, index_meta.json, index_manifest.json}
    * Chroma: models/chroma/<collection>/{chroma.sqlite3, ... , index_meta.json, index_manifest.json}
- NO toca el esquema de BD. El control de re-indexación se hace con manifest JSON en disco.

Decisiones técnicas:
- FAISS: IndexFlatIP + normalización L2 de embeddings para aproximar coseno (IP).
- Chroma: colección HNSW con métrica 'cosine'; enviamos los embeddings desde ST.
- collection_name por defecto:
    - si --run-id:     "run_<RUN>"
    - elif --source-id: "source_<SRC>"
    - else:             "chunks_default"
- Filtrado por run_id se hace en Python leyendo Document.meta['run_id'] por compatibilidad SQLite.

Requisitos:
    pip install sentence-transformers faiss-cpu numpy
Opcional:
    pip install chromadb
"""

from __future__ import annotations

import argparse
import json
import math
import os
import sys
import time
import hashlib
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Tuple

# --- Dependencias runtime (fallo temprano con mensaje claro)
try:
    import numpy as np
except Exception as e:
    print(json.dumps({"level": "ERROR", "event": "import.numpy.fail", "error": str(e)}))
    sys.exit(2)

try:
    import faiss  # type: ignore
    _FAISS_AVAILABLE = True
except Exception:
    _FAISS_AVAILABLE = False

try:
    from sentence_transformers import SentenceTransformer  # type: ignore
except Exception as e:
    print(json.dumps({"level": "ERROR", "event": "import.st.fail", "hint": "pip install sentence-transformers", "error": str(e)}))
    sys.exit(2)

# Chroma opcional
try:
    import chromadb  # type: ignore
    from chromadb import PersistentClient  # type: ignore
    _CHROMA_AVAILABLE = True
except Exception:
    _CHROMA_AVAILABLE = False

# --- SQLAlchemy y modelos de la app (no requiere app context)
try:
    from sqlalchemy import create_engine, select
    from sqlalchemy.orm import sessionmaker, Session
    # Modelos propios
    from app.models.chunk import Chunk
    from app.models.document import Document
except Exception as e:
    print(json.dumps({
        "level": "ERROR",
        "event": "import.models.fail",
        "error": str(e),
        "hint": "Ejecuta desde la raíz del repo (python -m scripts.index_chunks) y comprueba PYTHONPATH."
    }))
    sys.exit(2)

# ---------------------------------------------------------------------
# Utilidades de logging/FS
# ---------------------------------------------------------------------

def log(event: str, **kw) -> None:
    rec = {"level": "INFO", "event": event}
    rec.update(kw)
    print(json.dumps(rec, ensure_ascii=False))

def log_err(event: str, **kw) -> None:
    rec = {"level": "ERROR", "event": event}
    rec.update(kw)
    print(json.dumps(rec, ensure_ascii=False), file=sys.stderr)

def ensure_dir(p: Path) -> None:
    p.mkdir(parents=True, exist_ok=True)

def rm_tree(p: Path) -> None:
    import shutil
    if p.exists():
        shutil.rmtree(p)

def load_json(path: Path, default):
    if path.exists():
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except Exception as e:
            log_err("json.load.fail", path=str(path), error=str(e))
            return default
    return default

def save_json(path: Path, data) -> None:
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

def sha256_text(text: str) -> str:
    # Normalización ligera previa al hash (espacios)
    norm = " ".join((text or "").split())
    return hashlib.sha256(norm.encode("utf-8")).hexdigest()

def time_iso_now() -> str:
    # Hora local con offset (sin dependencias extra)
    from datetime import datetime, timezone, timedelta
    import time as _time
    offset_sec = -_time.timezone + (3600 if _time.daylight and _time.localtime().tm_isdst > 0 else 0)
    tz = timezone(offset=timedelta(seconds=offset_sec))
    return datetime.now(tz).isoformat(timespec="seconds")

# ---------------------------------------------------------------------
# Selección de chunks
# ---------------------------------------------------------------------

def get_engine() -> "Engine":
    db_url = os.getenv("DATABASE_URL", "sqlite:///data/processed/tracking.sqlite")
    return create_engine(db_url, future=True)

def iter_candidate_chunks(
    session: Session,
    run_id: Optional[int],
    source_id: Optional[int],
    limit: Optional[int],
) -> Iterable[Tuple[int, int, str, Optional[dict]]]:
    """
    Devuelve tuplas (chunk_id, chunk_source_id, chunk_text, doc_meta)
    Filtra por:
      - source_id directamente en chunk
      - run_id evaluado sobre Document.meta['run_id'] (filtrado en Python para máxima compatibilidad)
    """
    q = session.query(Chunk, Document).join(Document, Document.id == Chunk.document_id)

    if source_id is not None:
        q = q.filter(Chunk.source_id == source_id)

    # No filtramos run_id en SQL para evitar dependencia de JSON1; filtramos en Python.
    if limit is not None and limit > 0:
        # Pedimos más de lo necesario si vamos a filtrar por run_id, para amortiguar descartes
        fetch_limit = limit * 3 if run_id is not None else limit
        q = q.limit(fetch_limit)

    for ch, doc in q.yield_per(1000):
        doc_meta = doc.meta if isinstance(doc.meta, dict) else safe_json(doc.meta)
        if run_id is not None:
            if not (isinstance(doc_meta, dict) and doc_meta.get("run_id") == run_id):
                continue
        yield (ch.id, ch.source_id, ch.text or "", doc_meta)

def safe_json(raw) -> Optional[dict]:
    if raw is None:
        return None
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str):
        try:
            return json.loads(raw)
        except Exception:
            return None
    return None

# ---------------------------------------------------------------------
# Embeddings
# ---------------------------------------------------------------------

class Embedder:
    def __init__(self, model_name: str, batch_size: int = 256):
        self.model_name = model_name
        self.batch_size = batch_size
        self._model = SentenceTransformer(model_name)

    @property
    def dim(self) -> int:
        # Inferimos dimensión al vuelo embebiendo un token de prueba una sola vez
        vec = self._model.encode(["test"], convert_to_numpy=True, normalize_embeddings=False)
        return int(vec.shape[1])

    def encode_iter(self, texts: List[str]) -> np.ndarray:
        """Embebe en lotes y concatena a un único array (N, D). No normaliza."""
        out: List[np.ndarray] = []
        n = len(texts)
        for i in range(0, n, self.batch_size):
            batch = texts[i:i + self.batch_size]
            emb = self._model.encode(batch, convert_to_numpy=True, normalize_embeddings=False, show_progress_bar=False)
            out.append(emb)
            log("emb.batch", batch_from=i, batch_to=min(i + self.batch_size, n), batch_size=len(batch))
        return np.vstack(out) if out else np.empty((0, self.dim), dtype="float32")

def l2_normalize(x: np.ndarray) -> np.ndarray:
    # Evita división por cero
    norms = np.linalg.norm(x, axis=1, keepdims=True)
    norms[norms == 0.0] = 1.0
    return (x / norms).astype("float32")

# ---------------------------------------------------------------------
# FAISS store
# ---------------------------------------------------------------------

class FaissStore:
    """
    Persistencia mínima con:
      - index.faiss   : índice FAISS (IndexFlatIP) sobre vectores normalizados
      - ids.npy       : array paralelo de chunk_ids (orden de inserción)
      - index_meta.json
      - index_manifest.json
    """
    def __init__(self, base_dir: Path):
        self.base_dir = base_dir
        self.index_path = base_dir / "index.faiss"
        self.ids_path = base_dir / "ids.npy"
        self.meta_path = base_dir / "index_meta.json"
        self.manifest_path = base_dir / "index_manifest.json"
        ensure_dir(base_dir)

        if not _FAISS_AVAILABLE:
            raise RuntimeError("FAISS no disponible. Instala faiss-cpu.")

        self.index = None  # type: Optional[faiss.Index]
        self.ids = None    # type: Optional[np.ndarray]

    def load_or_init(self, dim: int, rebuild: bool) -> None:
        if rebuild:
            self.index = faiss.IndexFlatIP(dim)
            self.ids = np.empty((0,), dtype="int64")
            return

        if self.index_path.exists() and self.ids_path.exists():
            self.index = faiss.read_index(str(self.index_path))
            self.ids = np.load(self.ids_path)
        else:
            self.index = faiss.IndexFlatIP(dim)
            self.ids = np.empty((0,), dtype="int64")

    def add(self, vectors: np.ndarray, chunk_ids: np.ndarray) -> None:
        assert self.index is not None and self.ids is not None
        if vectors.dtype != np.float32:
            vectors = vectors.astype("float32")
        self.index.add(vectors)
        self.ids = np.concatenate([self.ids, chunk_ids.astype("int64")], axis=0)

    def save(self) -> None:
        assert self.index is not None and self.ids is not None
        faiss.write_index(self.index, str(self.index_path))
        np.save(self.ids_path, self.ids)

    def search(self, query_vec: np.ndarray, k: int) -> Tuple[np.ndarray, np.ndarray]:
        assert self.index is not None
        if query_vec.ndim == 1:
            query_vec = query_vec.reshape(1, -1)
        D, I = self.index.search(query_vec.astype("float32"), k)
        return D, I

# ---------------------------------------------------------------------
# Chroma store (mismo “montaje” que FAISS a nivel de meta/manifest/logs)
# ---------------------------------------------------------------------

class ChromaStore:
    """
    Persistencia con Chroma, 1 carpeta por colección:
      - models/chroma/<collection>/
          - chroma.sqlite3, index/*, etc. (controlado por Chroma)
          - index_meta.json
          - index_manifest.json
    """
    def __init__(self, base_dir: Path, collection_name: str, rebuild: bool = False, metric: str = "cosine"):
        if not _CHROMA_AVAILABLE:
            raise RuntimeError("Chroma no disponible. Instala chromadb>=0.5")

        self.base_dir = base_dir
        self.collection_name = collection_name
        self.meta_path = base_dir / "index_meta.json"
        self.manifest_path = base_dir / "index_manifest.json"

        if rebuild:
            rm_tree(base_dir)
        ensure_dir(base_dir)

        # Un cliente por carpeta (1 DB por colección) para trazabilidad 1:1 con FAISS
        self.client = PersistentClient(path=str(base_dir))
        self.collection = self.client.get_or_create_collection(
            name=collection_name,
            metadata={"hnsw:space": metric, "vectorizer": "none"},
            embedding_function=None,  # Enviamos embeddings ya calculados
        )

    def add(self, vectors: np.ndarray, chunk_ids: List[int], documents: List[str], batch_size: int = 4096) -> None:
        ids_str = [str(i) for i in chunk_ids]
        try:
            self.collection.delete(ids=ids_str)
        except Exception:
            pass

        if isinstance(vectors, np.ndarray):
            vectors = vectors.astype("float32")

        n = len(ids_str)
        for i in range(0, n, batch_size):
            sl = slice(i, min(i + batch_size, n))
            metas = [{"chunk_id": sid} for sid in ids_str[sl]]
            self.collection.add(
                ids=ids_str[sl],
                embeddings=vectors[sl].tolist(),
                documents=[d for d in documents[sl]],
                metadatas=metas,
            )


    def count(self) -> int:
        return int(self.collection.count())

# ---------------------------------------------------------------------
# Manifest y Meta
# ---------------------------------------------------------------------

def load_manifest(path: Path) -> Dict:
    return load_json(path, {"chunk_ids": [], "hash_by_chunk_id": {}})

def update_manifest(manifest: Dict, pairs: List[Tuple[int, str]]) -> Tuple[int, int, int]:
    """
    Actualiza el manifest con (chunk_id, content_hash).
    Retorna (n_new, n_updated, n_skipped)
    """
    chunk_ids: List[int] = manifest.get("chunk_ids", [])
    hash_map: Dict[str, str] = manifest.get("hash_by_chunk_id", {})

    new = upd = skip = 0
    have = set(str(x) for x in chunk_ids)
    for cid, h in pairs:
        s_cid = str(cid)
        if s_cid not in hash_map:
            hash_map[s_cid] = h
            if s_cid not in have:
                chunk_ids.append(cid)
            new += 1
        else:
            if hash_map[s_cid] != h:
                hash_map[s_cid] = h
                upd += 1
            else:
                skip += 1

    manifest["chunk_ids"] = chunk_ids
    manifest["hash_by_chunk_id"] = hash_map
    return new, upd, skip

def compute_checksum_from_manifest(manifest: Dict) -> str:
    # Hash estable sobre (chunk_id, hash) ordenado por chunk_id
    items = sorted(((int(k), v) for k, v in manifest.get("hash_by_chunk_id", {}).items()), key=lambda x: x[0])
    h = hashlib.sha256()
    for cid, chash in items:
        h.update(f"{cid}:{chash}\n".encode("utf-8"))
    return f"sha256:{h.hexdigest()}"

# ---------------------------------------------------------------------
# Top-k smoke test
# ---------------------------------------------------------------------

def smoke_test_faiss(
    store: FaissStore,
    embedder: Embedder,
    session: Session,
    k: int,
    query_text: str
) -> None:
    assert store.index is not None and store.ids is not None
    qv = embedder._model.encode([query_text], convert_to_numpy=True, normalize_embeddings=False)
    qv = l2_normalize(qv)
    D, I = store.search(qv, k)
    idxs = I[0]
    scores = D[0]
    top_chunk_ids = [int(store.ids[i]) for i in idxs if 0 <= i < len(store.ids)]
    # Recuperamos snippets para log (breves)
    if not top_chunk_ids:
        log("smoke.results", k=k, query=query_text, results=[])
        return

    rows = session.query(Chunk, Document).join(Document, Document.id == Chunk.document_id)\
        .filter(Chunk.id.in_(top_chunk_ids)).all()
    by_id = {ch.id: (ch, doc) for ch, doc in rows}
    results = []
    for rank, (cid, score) in enumerate(zip(top_chunk_ids, scores), start=1):
        ch, doc = by_id.get(cid, (None, None))
        if ch is None:
            continue
        snippet = " ".join((ch.text or "").split())[:160]
        results.append({
            "rank": rank,
            "chunk_id": cid,
            "score": float(score),
            "title": getattr(doc, "title", None),
            "document_id": getattr(ch, "document_id", None),
            "source_id": getattr(ch, "source_id", None),
            "snippet": snippet
        })
    log("smoke.results", k=k, query=query_text, results=results)

def smoke_test_chroma(
    store: ChromaStore,
    embedder: Embedder,
    session: Session,
    k: int,
    query_text: str
) -> None:
    # Embedding de la query (sin normalizar; métrica cosine en Chroma)
    qv = embedder._model.encode([query_text], convert_to_numpy=True, normalize_embeddings=False)[0]
    res = store.collection.query(
        query_embeddings=[qv.astype("float32").tolist()],
        n_results=max(1, int(k)),
        include=["distances", "metadatas"],  # NO 'ids' (Chroma 0.5 no lo permite en include)
    )
    # En Chroma 0.5, 'ids' viene SIEMPRE, pero no se puede pedir en include.
    ids = (res.get("ids") or [[]])[0]
    dists = (res.get("distances") or [[]])[0]
    # Fallback: si por cualquier motivo no hay ids, intenta con metadatas.chunk_id
    if not ids:
        metas = (res.get("metadatas") or [[]])[0]
        ids = [m.get("chunk_id") for m in metas if isinstance(m, dict) and m.get("chunk_id")]

    # Mapeo de ids -> int
    try:
        top_chunk_ids = [int(x) for x in ids]
    except Exception:
        # Si por cualquier motivo no son enteros, devolvemos vacíos
        log("smoke.results", k=k, query=query_text, results=[])
        return

    # Recuperamos desde BD para mantener mismo formato que FAISS
    rows = session.query(Chunk, Document).join(Document, Document.id == Chunk.document_id)\
        .filter(Chunk.id.in_(top_chunk_ids)).all()
    by_id = {ch.id: (ch, doc) for ch, doc in rows}

    results = []
    for rank, (cid, dist) in enumerate(zip(top_chunk_ids, dists), start=1):
        ch, doc = by_id.get(cid, (None, None))
        if ch is None:
            continue
        snippet = " ".join((ch.text or "").split())[:160]
        # Convertimos distancia cosine a "score" de similitud ~ (1 - distancia)
        score = 1.0 - float(dist) if dist is not None else None
        results.append({
            "rank": rank,
            "chunk_id": cid,
            "score": score,
            "title": getattr(doc, "title", None),
            "document_id": getattr(ch, "document_id", None),
            "source_id": getattr(ch, "source_id", None),
            "snippet": snippet
        })
    log("smoke.results", k=k, query=query_text, results=results)

# ---------------------------------------------------------------------
# CLI principal
# ---------------------------------------------------------------------

def parse_args(argv: Optional[List[str]] = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Indexación de Chunks a FAISS/Chroma")
    p.add_argument("--store", choices=["faiss", "chroma"], default="faiss")
    p.add_argument("--model", default="sentence-transformers/all-MiniLM-L6-v2")
    p.add_argument("--batch-size", type=int, default=256)
    p.add_argument("--limit", type=int, default=None)
    p.add_argument("--rebuild", action="store_true", help="Recrear índice desde cero")
    p.add_argument("--run-id", type=int, default=None, help="Filtrar por run_id (Document.meta.run_id)")
    p.add_argument("--source-id", type=int, default=None, help="Filtrar por Chunk.source_id")
    p.add_argument("--collection", default=None, help="Nombre explícito de la colección")
    p.add_argument("--smoke-query", default=None, help="Consulta de humo top-k")
    p.add_argument("--k", type=int, default=5, help="k para la prueba de humo")
    return p.parse_args(argv)

def resolve_collection(args: argparse.Namespace) -> str:
    if args.collection:
        return args.collection
    if args.run_id is not None:
        return f"run_{args.run_id}"
    if args.source_id is not None:
        return f"source_{args.source_id}"
    return "chunks_default"

def main(argv: Optional[List[str]] = None) -> int:
    args = parse_args(argv)

    if args.store == "faiss" and not _FAISS_AVAILABLE:
        log_err("faiss.unavailable", hint="pip install faiss-cpu")
        return 2
    if args.store == "chroma" and not _CHROMA_AVAILABLE:
        log_err("chroma.unavailable", hint="pip install chromadb>=0.5")
        return 2
    if args.batch_size <= 0:
        log_err("args.invalid", field="batch-size", value=args.batch_size)
        return 2

    collection = resolve_collection(args)
    out_dir = Path("models") / args.store / collection
    ensure_dir(out_dir)

    t0 = time.time()
    log("index.start", store=args.store, model=args.model, run_id=args.run_id, source_id=args.source_id,
        batch_size=args.batch_size, limit=args.limit, collection=collection, out_dir=str(out_dir))

    # --- DB
    engine = get_engine()
    SessionLocal = sessionmaker(bind=engine, future=True)
    session: Session
    with SessionLocal() as session:
        # --- Selección de candidatos
        candidates: List[Tuple[int, int, str, Optional[dict]]] = list(
            iter_candidate_chunks(session, args.run_id, args.source_id, args.limit)
        )
        if args.limit is not None and len(candidates) > args.limit:
            candidates = candidates[:args.limit]

        n_input = len(candidates)
        log("select.done", n_input_chunks=n_input)

        # Si no hay trabajo, salir limpio (crear meta/manifest vacíos para UI)
        if n_input == 0:
            meta_path = out_dir / "index_meta.json"
            manifest_path = out_dir / "index_manifest.json"
            if not meta_path.exists():
                save_json(meta_path, {
                    "collection": collection, "store": args.store, "model": args.model,
                    "dim": 0, "n_chunks": 0, "built_at": time_iso_now(),
                    "duration_sec": 0.0, "run_ids": [], "source_ids": [],
                    "checksum": "", "notes": "empty build"
                })
            if not manifest_path.exists():
                save_json(manifest_path, {"chunk_ids": [], "hash_by_chunk_id": {}})
            log("index.end", duration_ms=int((time.time() - t0) * 1000), n_chunks=0, dim=0, out_dir=str(out_dir))
            return 0

        # --- Manifest previo
        manifest = load_manifest(out_dir / "index_manifest.json")
        prev_hash = manifest.get("hash_by_chunk_id", {})

        # --- Preparar textos y hashes
        chunk_ids: List[int] = []
        texts: List[str] = []
        new_pairs: List[Tuple[int, str]] = []   # para update_manifest

        for cid, _src, text, _meta in candidates:
            h = sha256_text(text)
            prev = prev_hash.get(str(cid))
            if args.rebuild or prev is None or prev != h:
                chunk_ids.append(cid)
                texts.append(text)
                new_pairs.append((cid, h))
            else:
                pass  # no reindex

        n_todo = len(texts)
        n_skip = n_input - n_todo
        log("plan", n_reindex=n_todo, n_skipped=n_skip, rebuild=args.rebuild)

        # --- Embeddings
        embedder = Embedder(args.model, batch_size=args.batch_size)
        dim = embedder.dim

        if args.store == "faiss":
            store = FaissStore(out_dir)
            store.load_or_init(dim=dim, rebuild=args.rebuild)

            if n_todo > 0:
                vecs = embedder.encode_iter(texts)  # (N, D)
                vecs = l2_normalize(vecs)
                store.add(vecs, np.array(chunk_ids, dtype="int64"))
                store.save()

                # Manifest
                n_new, n_upd, n_sk = update_manifest(manifest, new_pairs)
                save_json(store.manifest_path, manifest)

            # Meta
            meta = load_json(store.meta_path, {})
            n_chunks_total = int(store.ids.shape[0]) if store.ids is not None else 0
            meta.update({
                "collection": collection,
                "store": "faiss",
                "model": args.model,
                "dim": dim,
                "n_chunks": n_chunks_total,
                "built_at": time_iso_now(),
                "duration_sec": round(time.time() - t0, 3),
                "run_ids": [args.run_id] if args.run_id is not None else [],
                "source_ids": [args.source_id] if args.source_id is not None else [],
                "checksum": compute_checksum_from_manifest(manifest),
                "notes": f"batched={args.batch_size}, normalized"
            })
            save_json(store.meta_path, meta)

            log("index.persist",
                n_input=n_input,
                n_reindex=n_todo,
                n_skipped=n_skip,
                out_dir=str(out_dir))

            # Smoke test opcional
            if args.smoke_query:
                smoke_test_faiss(store, embedder, session, args.k, args.smoke_query)

        elif args.store == "chroma":
            # Inicializa store Chroma (1 carpeta == 1 DB/colección para trazabilidad)
            store = ChromaStore(out_dir, collection_name=collection, rebuild=bool(args.rebuild), metric="cosine")

            if n_todo > 0:
                vecs = embedder.encode_iter(texts)  # (N, D), sin normalizar (cosine lo maneja)
                store.add(vecs, chunk_ids=chunk_ids, documents=texts)

                # Manifest
                n_new, n_upd, n_sk = update_manifest(manifest, new_pairs)
                save_json(store.manifest_path, manifest)

            # Meta: contrato idéntico al de FAISS (para tu template)
            meta_path = out_dir / "index_meta.json"
            meta = load_json(meta_path, {})
            n_chunks_total = store.count()
            meta.update({
                "collection": collection,
                "store": "chroma",
                "model": args.model,
                "dim": dim,
                "n_chunks": n_chunks_total,
                "built_at": time_iso_now(),
                "duration_sec": round(time.time() - t0, 3),
                "run_ids": [args.run_id] if args.run_id is not None else [],
                "source_ids": [args.source_id] if args.source_id is not None else [],
                "checksum": compute_checksum_from_manifest(manifest),
                "notes": f"batched={args.batch_size}, metric=cosine"
            })
            save_json(meta_path, meta)

            log("index.persist",
                n_input=n_input,
                n_reindex=n_todo,
                n_skipped=n_skip,
                out_dir=str(out_dir))

            # Smoke test opcional (mismo formato de salida)
            if args.smoke_query:
                smoke_test_chroma(store, embedder, session, args.k, args.smoke_query)

    log("index.end", duration_ms=int((time.time() - t0) * 1000), n_chunks=n_input, dim=dim, out_dir=str(out_dir))
    return 0

if __name__ == "__main__":
    try:
        sys.exit(main())
    except NotImplementedError as nie:
        log_err("not_implemented", error=str(nie))
        sys.exit(3)
    except Exception as e:
        log_err("fatal", error=str(e))
        sys.exit(1)
