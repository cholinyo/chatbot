# app/blueprints/admin/rag_routes.py
from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from flask import Blueprint, jsonify, render_template, request, current_app

# === Opcional: proteger /admin con login_required si usas flask-login ===
try:
    from flask_login import login_required  # type: ignore
except Exception:  # pragma: no cover
    def login_required(f):  # fallback no-op si no hay flask-login
        return f

# === DB / ORM ===
# Ajusta estas importaciones a tu proyecto. Si usas 'db.session', deja db;
# si usas un SessionLocal() manual, adapta get_db() abajo.
try:
    from app.extensions import db  # db = SQLAlchemy()
except Exception:
    db = None  # si no existe, usa SessionLocal()

# Modelos esperados (ajusta nombres de módulos/clases si cambian en tu repo)
try:
    from app.models import Chunk, Document  # type: ignore
except Exception:
    # Fallback de tipos para evitar errores de import en tiempo de edición.
    Chunk = Any  # type: ignore
    Document = Any  # type: ignore

# === Embeddings (sentence-transformers) ===
# Se usa el mismo modelo que en el indexado para coherencia con FAISS/Chroma.
_EMBED_MODEL_NAME = "sentence-transformers/all-MiniLM-L6-v2"
_embedding_model = None  # cache del modelo

def _get_embedder():
    global _embedding_model
    if _embedding_model is None:
        from sentence_transformers import SentenceTransformer
        _embedding_model = SentenceTransformer(_EMBED_MODEL_NAME)
    return _embedding_model

def embed_query(text: str, normalize: bool = True):
    import numpy as np
    model = _get_embedder()
    vec = model.encode([text], normalize_embeddings=normalize)
    return np.asarray(vec, dtype="float32")  # shape (1, dim)

# === FAISS ===
def load_faiss_index(collection: str, models_dir: str = "models") -> Optional[Dict[str, Any]]:
    """
    Estructura esperada en disco:
      models/faiss/<collection>/
        - index.faiss
        - ids.npy
        - index_meta.json   (opcional pero recomendable)
    """
    import faiss
    import numpy as np

    base = Path(models_dir) / "faiss" / collection
    index_path = base / "index.faiss"
    ids_path = base / "ids.npy"
    meta_path = base / "index_meta.json"

    if not index_path.exists() or not ids_path.exists():
        return None

    index = faiss.read_index(str(index_path))
    ids = np.load(str(ids_path))
    meta = {}
    if meta_path.exists():
        try:
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
        except Exception:
            meta = {}

    # Relleno mínimo de meta si falta info
    meta.setdefault("model", _EMBED_MODEL_NAME)
    meta.setdefault("dim", int(index.d))  # type: ignore[attr-defined]
    meta.setdefault("n_chunks", int(len(ids)))
    meta.setdefault("collection", collection)
    return {"index": index, "ids": ids, "meta": meta}

def search_faiss(store_data: Dict[str, Any], query: str, k: int) -> List[Dict[str, Any]]:
    import numpy as np
    index = store_data["index"]
    ids = store_data["ids"]

    # Embedding normalizado para compatibilidad con IP≈cosine
    q = embed_query(query, normalize=True)  # shape (1, dim)
    scores, idxs = index.search(q, k)  # scores: (1,k), idxs: (1,k)

    out: List[Dict[str, Any]] = []
    for rank, (score, local_idx) in enumerate(zip(scores[0], idxs[0]), start=1):
        if local_idx < 0:
            continue
        chunk_id = int(ids[local_idx])
        score_raw = float(score)  # si IP con vectores normalizados: [-1..1]
        similarity = max(0.0, min(1.0, (score_raw + 1.0) / 2.0))
        out.append({
            "chunk_id": chunk_id,
            "score_raw": score_raw,
            "similarity": similarity,
            "rank": rank,
        })
    return out

# === ChromaDB ===
def load_chroma_collection(collection: str, models_dir: str = "models") -> Optional[Dict[str, Any]]:
    """
    Estructura esperada en disco:
      models/chroma/<collection>/  (persist_directory)
    """
    try:
        import chromadb
        from chromadb.config import Settings
    except Exception:
        return None

    persist_directory = str(Path(models_dir) / "chroma" / collection)
    if not Path(persist_directory).exists():
        return None

    client = chromadb.PersistentClient(path=persist_directory, settings=Settings(allow_reset=False))
    col = client.get_or_create_collection(collection)
    # No siempre hay meta homogénea en Chroma; rellenamos lo básico:
    meta = {
        "model": _EMBED_MODEL_NAME,   # suposición razonable si usaste ese en indexado
        "dim": None,                  # desconocido (no lo expone)
        "n_chunks": col.count(),      # rápido y útil
        "collection": collection
    }
    return {"collection": col, "meta": meta}

def search_chroma(store_data: Dict[str, Any], query: str, k: int) -> List[Dict[str, Any]]:
    """
    Usamos query_texts (Chroma calcula embeddings con su función asociada a la colección).
    Si tu colección se creó sin función de embeddings, puedes cambiar a query_embeddings=embed_query(...).tolist().
    """
    col = store_data["collection"]

    # Preferimos query_texts para usar la embedding_function registrada en la colección
    try:
        res = col.query(query_texts=[query], n_results=k, include=["metadatas", "distances", "ids"])
    except TypeError:
        # Fallback: usar embeddings externos
        q = embed_query(query, normalize=True).tolist()
        res = col.query(query_embeddings=q, n_results=k, include=["metadatas", "distances", "ids"])

    ids = (res.get("ids") or [[]])[0]
    distances = (res.get("distances") or [[]])[0]
    metadatas = (res.get("metadatas") or [[]])[0]

    out: List[Dict[str, Any]] = []
    for rank, (id_str, dist, meta) in enumerate(zip(ids, distances, metadatas), start=1):
        try:
            chunk_id = int(id_str)
        except Exception:
            # Si tus IDs no son enteros, adapta aquí cómo mapear a Chunk.
            continue
        distance = float(dist)
        # Asumiendo métrica cosine → similarity = 1 - distance
        similarity = max(0.0, min(1.0, 1.0 - distance))
        out.append({
            "chunk_id": chunk_id,
            "distance": distance,
            "similarity": similarity,
            "rank": rank,
            # Conservamos algunos campos útiles de metadata si existieran
            "meta": meta or {},
        })
    return out

# === Cache de índices/colecciones ===
_INDEX_CACHE: Dict[Tuple[str, str], Dict[str, Any]] = {}

def get_store(store: str, collection: str, models_dir: str = "models") -> Optional[Dict[str, Any]]:
    key = (store, collection)
    if key in _INDEX_CACHE:
        return _INDEX_CACHE[key]
    if store == "faiss":
        data = load_faiss_index(collection, models_dir=models_dir)
    elif store == "chroma":
        data = load_chroma_collection(collection, models_dir=models_dir)
    else:
        data = None
    if data is not None:
        _INDEX_CACHE[key] = data
    return data

# === Enriquecimiento desde BD (Chunk + Document) ===
def enrich_results_from_db(rows: List[Dict[str, Any]], max_chars: int = 800) -> List[Dict[str, Any]]:
    """
    Añade título, ruta y texto (recortado) del chunk/documento a cada resultado.
    Ajusta campos según tu ORM real si difieren.
    """
    if not rows:
        return []

    # Obtén ids únicos
    chunk_ids = [int(r["chunk_id"]) for r in rows if "chunk_id" in r]
    unique_ids = list(sorted(set(chunk_ids)))

    # Obtén sesión DB
    session = None
    if db is not None:
        session = db.session
    else:
        # Si no usas 'db.session', adapta esto a tu SessionLocal()
        from app.database import SessionLocal  # <-- ajusta si fuese tu caso
        session = SessionLocal()

    # Consulta en bloque
    # Se asume que Chunk tiene .id, .index, .text, .document_id
    # y Document tiene .id, .title, .path
    chunks_by_id: Dict[int, Any] = {}
    docs_by_id: Dict[int, Any] = {}

    try:
        q_chunks = session.query(Chunk).filter(Chunk.id.in_(unique_ids)).all()
        for ch in q_chunks:
            chunks_by_id[int(ch.id)] = ch

        doc_ids = list({int(ch.document_id) for ch in q_chunks if getattr(ch, "document_id", None) is not None})
        if doc_ids:
            q_docs = session.query(Document).filter(Document.id.in_(doc_ids)).all()
            for d in q_docs:
                docs_by_id[int(d.id)] = d

        enriched: List[Dict[str, Any]] = []
        for r in rows:
            cid = int(r["chunk_id"])
            ch = chunks_by_id.get(cid)
            doc = docs_by_id.get(int(ch.document_id)) if ch else None

            item = dict(r)
            if ch:
                text = getattr(ch, "text", None) or ""
                item.update({
                    "chunk_index": getattr(ch, "index", None),
                    "text": text[:max_chars] + ("..." if len(text) > max_chars else "")
                })
            if doc:
                item.update({
                    "document_id": int(doc.id),
                    "document_title": getattr(doc, "title", None),
                    "document_path": getattr(doc, "path", None),
                })
            enriched.append(item)
        return enriched
    finally:
        # Si abriste una sesión propia, ciérrala (no cierres db.session)
        if db is None and session is not None:
            try:
                session.close()
            except Exception:
                pass

# === Descubrimiento de colecciones en disco ===
def list_faiss_collections(models_dir: str = "models") -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    base = Path(models_dir) / "faiss"
    if not base.exists():
        return out
    for p in base.iterdir():
        if not p.is_dir():
            continue
        meta_path = p / "index_meta.json"
        meta = {}
        if meta_path.exists():
            try:
                meta = json.loads(meta_path.read_text(encoding="utf-8"))
            except Exception:
                meta = {}
        # fallback si no hay meta
        meta.setdefault("collection", p.name)
        meta.setdefault("model", _EMBED_MODEL_NAME)
        # intenta leer ids.npy para contar chunks
        ids_path = p / "ids.npy"
        if ids_path.exists():
            try:
                import numpy as np
                n_chunks = int(len(np.load(str(ids_path))))
            except Exception:
                n_chunks = None
        else:
            n_chunks = None
        if "n_chunks" not in meta and n_chunks is not None:
            meta["n_chunks"] = n_chunks
        out.append({
            "store": "faiss",
            "name": p.name,
            "chunks": meta.get("n_chunks"),
            "dim": meta.get("dim"),
            "model": meta.get("model"),
        })
    return out

def list_chroma_collections(models_dir: str = "models") -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    base = Path(models_dir) / "chroma"
    if not base.exists():
        return out
    # En modo persistente, cada subcarpeta suele corresponder a una colección
    for p in base.iterdir():
        if not p.is_dir():
            continue
        # Intento de contar datos con el cliente
        try:
            data = load_chroma_collection(p.name, models_dir=models_dir)
            n_chunks = data["meta"]["n_chunks"] if data else None
        except Exception:
            n_chunks = None
        out.append({
            "store": "chroma",
            "name": p.name,
            "chunks": n_chunks,
            "dim": None,
            "model": _EMBED_MODEL_NAME,
        })
    return out

# === Blueprint /admin/rag ===
admin_rag_bp = Blueprint("admin_rag", __name__, url_prefix="/admin/rag")

@admin_rag_bp.route("/chat")
@login_required
def chat_interface():
    """
    Página HTML del laboratorio RAG (usa templates/admin/chat.html).
    """
    return render_template("admin/chat.html")

@admin_rag_bp.route("/collections")
@login_required
def list_collections():
    """
    Devuelve la lista de colecciones detectadas en FAISS/Chroma.
    """
    models_dir = current_app.config.get("MODELS_DIR", "models")
    faiss_cols = list_faiss_collections(models_dir=models_dir)
    chroma_cols = list_chroma_collections(models_dir=models_dir)
    cols = faiss_cols + chroma_cols
    # en rag_routes.py
    current_app.logger.info(f"[RAG] MODELS_DIR: {current_app.config.get('MODELS_DIR', 'models')}")
    return jsonify({"models_dir": current_app.config.get("MODELS_DIR", "models"), "collections": cols})

@admin_rag_bp.route("/query", methods=["POST"])
@login_required
def rag_query():
    """
    Realiza una búsqueda en el vector store seleccionado y enriquece resultados
    con datos de Chunk/Document desde la base de datos.
    Querystring:
      - store: "faiss" | "chroma"  (por defecto: "chroma")
      - collection: nombre de la colección (obligatorio)
    Body JSON:
      - { "query": "...", "k": 5 }
    """
    t0 = time.time()
    models_dir = current_app.config.get("MODELS_DIR", "models")

    store = (request.args.get("store") or "chroma").strip().lower()
    collection = (request.args.get("collection") or "").strip()
    payload = request.get_json(silent=True) or {}
    query = (payload.get("query") or "").strip()
    k = int(payload.get("k") or 5)

    if not collection:
        return jsonify({"error": "Falta 'collection' en querystring"}), 400
    if not query:
        return jsonify({"error": "El body debe incluir 'query'"}), 400
    if store not in ("faiss", "chroma"):
        return jsonify({"error": f"Store no soportado: {store}"}), 400

    # Carga (cacheada) del índice/colección
    data = get_store(store, collection, models_dir=models_dir)
    if not data:
        return jsonify({"error": f"No se ha encontrado la colección '{collection}' en {store}"}), 404

    # Búsqueda
    if store == "faiss":
        base_results = search_faiss(data, query=query, k=k)
    else:
        base_results = search_chroma(data, query=query, k=k)

    # Enriquecimiento (Chunk/Document) desde BD
    enriched = enrich_results_from_db(base_results, max_chars=800)
    elapsed_ms = int((time.time() - t0) * 1000)

    # Meta del modelo/colección
    meta = data.get("meta", {})
    model_info = {
        "model": meta.get("model"),
        "dim": meta.get("dim"),
        "n_chunks": meta.get("n_chunks"),
        "collection": meta.get("collection"),
        "store": store,
    }

    return jsonify({
        "ok": True,
        "query": query,
        "k": k,
        "total_results": len(enriched),
        "elapsed_ms": elapsed_ms,
        "model_info": model_info,
        "results": enriched,
    })
