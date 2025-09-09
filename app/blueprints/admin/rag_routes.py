# app/blueprints/admin/rag_routes.py
from __future__ import annotations

import json
import math
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from flask import Blueprint, jsonify, render_template, request, current_app

# --- Opcional: proteger /admin con login_required ---
try:
    from flask_login import login_required  # type: ignore
except Exception:  # pragma: no cover
    def login_required(f):  # fallback no-op si no hay flask-login
        return f

# --- DB/ORM (opcional para enriquecer) ---
# Usa tu context manager oficial
try:
    from app.extensions.db import get_session  # type: ignore
except Exception:
    get_session = None  # type: ignore

try:
    from app.models import Chunk, Document  # ajusta si tu proyecto usa otros paths
except Exception:
    Chunk = Any  # type: ignore
    Document = Any  # type: ignore

# === Embeddings ===
_EMBEDDERS: Dict[str, Any] = {}
_DEFAULT_EMBED_MODEL = "sentence-transformers/all-MiniLM-L6-v2"


def _get_embedder(model_name: Optional[str]) -> Any:
    name = (model_name or _DEFAULT_EMBED_MODEL).strip()
    if name not in _EMBEDDERS:
        from sentence_transformers import SentenceTransformer
        _EMBEDDERS[name] = SentenceTransformer(name)
    return _EMBEDDERS[name]


def _prep_query_for_model(text: str, model_name: str) -> str:
    name = model_name.lower()
    if "multilingual-e5" in name or "/e5-" in name:
        return f"query: {text}"
    if "bge" in name:
        return f"Represent the Query for Retrieval: {text}"
    return text


def _prep_passage_for_model(text: str, model_name: str) -> str:
    name = model_name.lower()
    if "multilingual-e5" in name or "/e5-" in name:
        return f"passage: {text}"
    if "bge" in name:
        return f"Represent the Passage for Retrieval: {text}"
    return text


def embed_query(text: str, model_name: Optional[str], normalize: bool = True):
    import numpy as np
    name = (model_name or _DEFAULT_EMBED_MODEL)
    qtxt = _prep_query_for_model(text, name)
    model = _get_embedder(name)
    vec = model.encode([qtxt], normalize_embeddings=normalize)
    return np.asarray(vec, dtype="float32")  # (1, dim)


def embed_passages(texts: List[str], model_name: Optional[str], normalize: bool = True):
    import numpy as np
    if not texts:
        return np.zeros((0, 1), dtype="float32")
    name = (model_name or _DEFAULT_EMBED_MODEL)
    model = _get_embedder(name)
    prepped = [_prep_passage_for_model(t, name) for t in texts]
    vecs = model.encode(prepped, normalize_embeddings=normalize)
    return np.asarray(vecs, dtype="float32")  # (n, dim)


# === FAISS ===
def load_faiss_index(collection: str, models_dir: str = "models") -> Optional[Dict[str, Any]]:
    """
    Espera:
      models/faiss/<collection>/
        - index.faiss
        - ids.npy
        - index_meta.json (opcional, recomendable)
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

    meta: Dict[str, Any] = {}
    if meta_path.exists():
        try:
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
        except Exception:
            meta = {}

    meta.setdefault("collection", collection)
    meta.setdefault("model", meta.get("model") or _DEFAULT_EMBED_MODEL)
    try:
        meta.setdefault("dim", int(index.d))  # type: ignore[attr-defined]
    except Exception:
        meta.setdefault("dim", None)
    meta.setdefault("n_chunks", int(len(ids)))

    return {"index": index, "ids": ids, "meta": meta}


def search_faiss(store_data: Dict[str, Any], query: str, k: int, model_name: Optional[str]) -> List[Dict[str, Any]]:
    index = store_data["index"]
    ids = store_data["ids"]

    q = embed_query(query, model_name=model_name, normalize=True)
    scores, idxs = index.search(q, k)  # (1,k), (1,k)

    out: List[Dict[str, Any]] = []
    for rank, (score, local_idx) in enumerate(zip(scores[0], idxs[0]), start=1):
        if local_idx < 0:
            continue
        try:
            chunk_id = int(ids[local_idx])
        except Exception:
            chunk_id = ids[local_idx]
        score_raw = float(score)
        similarity = max(0.0, min(1.0, (score_raw + 1.0) / 2.0))
        out.append({
            "chunk_id": chunk_id,
            "score_raw": score_raw,
            "similarity": similarity,
            "rank": rank,
            # FAISS no trae meta: lo mapea enrich_results_from_db()
        })
    return out


# === ChromaDB ===
def load_chroma_collection(collection: str, models_dir: str = "models") -> Optional[Dict[str, Any]]:
    """
    Espera persistencia en:
      models/chroma/<collection>/
    """
    try:
        import chromadb
        from chromadb.config import Settings
    except Exception:
        return None

    base = Path(models_dir) / "chroma" / collection
    if not base.exists():
        return None

    client = chromadb.PersistentClient(path=str(base), settings=Settings(allow_reset=False))
    col = client.get_or_create_collection(collection)

    meta_path = base / "index_meta.json"
    meta_file = {}
    if meta_path.exists():
        try:
            meta_file = json.loads(meta_path.read_text(encoding="utf-8"))
        except Exception:
            meta_file = {}

    meta: Dict[str, Any] = {
        "collection": collection,
        "model": meta_file.get("model") or _DEFAULT_EMBED_MODEL,
        "dim": meta_file.get("dim"),
        "n_chunks": None,
    }
    try:
        meta["n_chunks"] = col.count()
    except Exception:
        pass

    return {"collection": col, "meta": meta}


def search_chroma(store_data: Dict[str, Any], query: str, k: int, model_name: Optional[str]) -> List[Dict[str, Any]]:
    """
    Consulta Chroma usando embeddings propios (query_embeddings).
    Normaliza campos comunes desde metadatos y usa documents (si existen) como snippet.
    """
    col = store_data["collection"]

    q = embed_query(query, model_name=model_name, normalize=True).tolist()
    res = col.query(
        query_embeddings=q,
        n_results=k,
        include=["metadatas", "distances", "documents"]  # ids siempre llegan
    )

    ids = (res.get("ids") or [[]])[0]
    distances = (res.get("distances") or [[]])[0]
    metadatas = (res.get("metadatas") or [[]])[0]
    documents = (res.get("documents") or [[]])[0]

    out: List[Dict[str, Any]] = []
    for rank in range(min(k, len(ids))):
        id_str = (ids[rank] if isinstance(ids, list) else None)
        meta = (metadatas[rank] if isinstance(metadatas, list) and rank < len(metadatas) else {}) or {}
        dist = float(distances[rank]) if isinstance(distances, list) and rank < len(distances) else None

        try:
            chunk_id = int(id_str)
        except Exception:
            chunk_id = id_str

        similarity = None
        if dist is not None:
            similarity = max(0.0, min(1.0, 1.0 - dist))

        item: Dict[str, Any] = {
            "chunk_id": chunk_id,
            "distance": dist,
            "similarity": similarity,
            "rank": rank + 1,
            "meta": meta,
        }

        # --- Normalización desde meta ---
        title = None
        for k_title in ("document_title", "title", "source_title"):
            if meta.get(k_title):
                title = meta[k_title]
                break
        if title:
            item["document_title"] = title

        path = None
        for k_path in ("document_path", "path", "uri", "source", "file"):
            if meta.get(k_path):
                path = meta[k_path]
                break
        if path:
            item["document_path"] = path

        ci = None
        for k_ci in ("chunk_index", "index", "chunk"):
            if meta.get(k_ci) is not None:
                ci = meta[k_ci]
                break
        if ci is not None:
            try:
                item["chunk_index"] = int(ci)
            except Exception:
                item["chunk_index"] = ci

        # Snippet desde documents o meta
        txt = ""
        if isinstance(documents, list) and rank < len(documents) and isinstance(documents[rank], str):
            txt = documents[rank]
        if not txt:
            for k_txt in ("text", "chunk_text", "content", "summary"):
                if isinstance(meta.get(k_txt), str) and meta[k_txt]:
                    txt = meta[k_txt]
                    break
        if txt:
            item["text"] = txt[:800] + ("..." if len(txt) > 800 else "")

        out.append(item)
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


# === Enriquecimiento desde BD (robusto a tu wiring) ===
def enrich_results_from_db(rows: List[Dict[str, Any]], max_chars: int = 800) -> List[Dict[str, Any]]:
    """
    Usa app.extensions.db.get_session() (SessionLocal) y SQLAlchemy 2.x (select + scalars()).
    Si no hay ORM o no hay get_session, devuelve rows tal cual.
    """
    if not rows:
        return []
    if get_session is None or Chunk is Any or Document is Any:
        return rows

    try:
        chunk_ids = [int(r["chunk_id"]) for r in rows if isinstance(r.get("chunk_id"), int)]
    except Exception:
        chunk_ids = []
    if not chunk_ids:
        return rows

    from sqlalchemy import select

    with get_session() as sess:  # type: ignore
        q_chunks = sess.execute(select(Chunk).where(Chunk.id.in_(chunk_ids))).scalars().all()

        chunks_by_id: Dict[int, Any] = {}
        doc_ids_set: set[int] = set()
        for ch in q_chunks:
            cid = int(getattr(ch, "id"))
            chunks_by_id[cid] = ch
            did = getattr(ch, "document_id", None)
            if did is not None:
                doc_ids_set.add(int(did))

        docs_by_id: Dict[int, Any] = {}
        if doc_ids_set:
            q_docs = sess.execute(select(Document).where(Document.id.in_(list(doc_ids_set)))).scalars().all()
            for d in q_docs:
                docs_by_id[int(getattr(d, "id"))] = d

        enriched: List[Dict[str, Any]] = []
        for r in rows:
            item = dict(r)
            cid = r.get("chunk_id")
            ch = chunks_by_id.get(cid) if isinstance(cid, int) else None
            doc = docs_by_id.get(int(getattr(ch, "document_id"))) if ch else None

            if ch:
                text = getattr(ch, "text", "") or ""
                item.update({
                    "chunk_index": getattr(ch, "index", None),
                    "text": text[:max_chars] + ("..." if len(text) > max_chars else "")
                })
            if doc:
                item.update({
                    "document_id": int(getattr(doc, "id")),
                    "document_title": getattr(doc, "title", None),
                    "document_path": getattr(doc, "path", None),
                })
            enriched.append(item)
        return enriched


# === MMR (Maximal Marginal Relevance) ===
def mmr_reorder(results: List[Dict[str, Any]], query: str, model_name: str, lam: float = 0.3, top_k: Optional[int] = None) -> Tuple[List[Dict[str, Any]], Optional[str]]:
    """
    Reordena con MMR usando embeddings del query y de los candidatos.
    Necesita texto de los candidatos (text o meta.text...). Si no hay, se omite.
    """
    import numpy as np
    if not results:
        return results, None

    # Extrae textos candidatos
    texts: List[str] = []
    for r in results:
        t = r.get("text") or r.get("meta", {}).get("text") or r.get("meta", {}).get("chunk_text") or ""
        texts.append(t if isinstance(t, str) else "")

    if not any(texts):
        return results, "mmr_skipped_no_text"

    qvec = embed_query(query, model_name, normalize=True)  # (1, d)
    dvecs = embed_passages(texts, model_name, normalize=True)  # (n, d)
    if dvecs.shape[0] == 0:
        return results, "mmr_skipped_no_vecs"

    # Similitudes
    def cos(a, b):  # a:(m,d), b:(n,d)
        return (a @ b.T)

    S_qd = cos(qvec, dvecs)[0]  # (n,)
    S_dd = cos(dvecs, dvecs)    # (n,n)

    n = len(results)
    selected: List[int] = []
    candidates = set(range(n))
    top_k = top_k or n

    # primer doc: mayor S_qd
    i = int(np.argmax(S_qd))
    selected.append(i)
    candidates.remove(i)

    while len(selected) < min(top_k, n) and candidates:
        best_j = None
        best_val = -1e9
        for j in candidates:
            max_div = max(S_dd[j, s] for s in selected) if selected else 0.0
            val = lam * S_qd[j] - (1 - lam) * max_div
            if val > best_val:
                best_val = val
                best_j = j
        selected.append(best_j)  # type: ignore
        candidates.remove(best_j)  # type: ignore

    reordered = [results[i] for i in selected] + [results[j] for j in sorted(candidates)]
    # renumera ranks
    for k, r in enumerate(reordered, start=1):
        r["rank"] = k
    return reordered, None


# === Reranker (CrossEncoder) ===
def rerank_cross_encoder(results: List[Dict[str, Any]], query: str, top_k: int = 20) -> Tuple[List[Dict[str, Any]], Optional[str]]:
    """
    Aplica un cross-encoder si está disponible; si no, devuelve tal cual con warning.
    Usa por defecto 'cross-encoder/ms-marco-MiniLM-L-6-v2' (rápido CPU).
    """
    if not results:
        return results, None
    try:
        from sentence_transformers import CrossEncoder
    except Exception:
        return results, "reranker_not_installed"

    model_name = current_app.config.get("RERANKER_MODEL", "cross-encoder/ms-marco-MiniLM-L-6-v2")
    try:
        ce = CrossEncoder(model_name)
    except Exception as e:
        return results, f"reranker_load_error: {e}"

    pairs = []
    texts = []
    for r in results[:top_k]:
        t = r.get("text") or r.get("meta", {}).get("text") or r.get("meta", {}).get("chunk_text") or ""
        t = str(t)[:800]
        texts.append(t)
        pairs.append((query, t if t else ""))
    if not any(texts):
        return results, "reranker_skipped_no_text"

    scores = ce.predict(pairs)  # mayor = más relevante
    for r, s in zip(results[:top_k], scores):
        r["rerank_score"] = float(s)

    head = sorted(results[:top_k], key=lambda x: x.get("rerank_score", -1e9), reverse=True)
    tail = results[top_k:]
    reordered = head + tail
    for k, r in enumerate(reordered, start=1):
        r["rank"] = k
    return reordered, None


# === Descubrimiento de colecciones ===
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

        model = meta.get("model") or _DEFAULT_EMBED_MODEL
        dim = meta.get("dim")

        ids_path = p / "ids.npy"
        n_chunks = None
        if ids_path.exists():
            try:
                import numpy as np
                n_chunks = int(len(np.load(str(ids_path))))
            except Exception:
                n_chunks = None

        out.append({
            "store": "faiss",
            "name": p.name,
            "chunks": n_chunks,
            "dim": dim,
            "model": model,
        })
    return out


def list_chroma_collections(models_dir: str = "models") -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    base = Path(models_dir) / "chroma"
    if not base.exists():
        return out
    for p in base.iterdir():
        if not p.is_dir():
            continue

        meta_path = p / "index_meta.json"
        meta_file = {}
        if meta_path.exists():
            try:
                meta_file = json.loads(meta_path.read_text(encoding="utf-8"))
            except Exception:
                meta_file = {}

        model = meta_file.get("model") or _DEFAULT_EMBED_MODEL

        n_chunks = None
        try:
            data = load_chroma_collection(p.name, models_dir=models_dir)
            n_chunks = data["meta"]["n_chunks"] if data else None
        except Exception:
            pass

        out.append({
            "store": "chroma",
            "name": p.name,
            "chunks": n_chunks,
            "dim": meta_file.get("dim"),
            "model": model,
        })
    return out


# === Blueprint ===
admin_rag_bp = Blueprint("admin_rag", __name__, url_prefix="/admin/rag")


@admin_rag_bp.route("/chat")
@login_required
def chat_interface():
    models_dir = current_app.config.get("MODELS_DIR", "models")
    cols = list_faiss_collections(models_dir) + list_chroma_collections(models_dir)
    return render_template("admin/chat.html", collections=cols)


@admin_rag_bp.route("/collections")
@login_required
def list_collections():
    models_dir = current_app.config.get("MODELS_DIR", "models")
    faiss_cols = list_faiss_collections(models_dir=models_dir)
    chroma_cols = list_chroma_collections(models_dir=models_dir)
    cols = faiss_cols + chroma_cols
    return jsonify({"models_dir": models_dir, "collections": cols})


@admin_rag_bp.route("/query", methods=["POST"])
@login_required
def rag_query():
    """
    Búsqueda RAG en la colección seleccionada.
    Flags extra: mmr=0/1, lambda (0..1), rerank=0/1, enrich=0/1, debug=0/1.
    """
    import traceback, time

    t0 = time.time()
    debug = (request.args.get("debug") or "0") == "1"

    try:
        models_dir = current_app.config.get("MODELS_DIR", "models")

        store = (request.args.get("store") or "chroma").strip().lower()
        collection = (request.args.get("collection") or "").strip()
        expected_model = (request.args.get("expected_model") or "").strip()
        enrich_flag = (request.args.get("enrich") or "1") != "0"

        mmr_on = (request.args.get("mmr") or "0") == "1"
        try:
            mmr_lambda = float(request.args.get("lambda") or 0.3)
        except Exception:
            mmr_lambda = 0.3
        rerank_on = (request.args.get("rerank") or "0") == "1"

        # ---- parseo body SEGURO ----
        payload = request.get_json(silent=True) or {}
        q_raw = payload.get("query", "")
        query = q_raw.strip() if isinstance(q_raw, str) else str(q_raw).strip()
        k = int(payload.get("k") or 5)

        if not collection:
            return jsonify({"ok": False, "error": "Falta 'collection'"}), 400
        if not query:
            return jsonify({"ok": False, "error": "El body debe incluir 'query'"}), 400
        if store not in ("faiss", "chroma"):
            return jsonify({"ok": False, "error": f"Store no soportado: {store}"}), 400

        # ---- carga de colección ----
        data = get_store(store, collection, models_dir=models_dir)
        if not data:
            return jsonify({"ok": False, "error": f"No existe la colección '{collection}' en {store}"}), 404

        # Modelo real
        model_name = (data.get("meta") or {}).get("model") or _DEFAULT_EMBED_MODEL

        # Validación con UI
        if expected_model and expected_model != model_name:
            return jsonify({
                "ok": False,
                "error": f"Modelo de la colección: '{model_name}'. No coincide con el esperado por la UI: '{expected_model}'.",
                "model_info": {**(data.get("meta") or {}), "store": store}
            }), 409

        # ---- búsqueda base ----
        if store == "faiss":
            base_results = search_faiss(data, query=query, k=k, model_name=model_name)
        else:
            base_results = search_chroma(data, query=query, k=k, model_name=model_name)

        warnings = []

        # ---- enriquecimiento ----
        if enrich_flag:
            try:
                enriched = enrich_results_from_db(base_results, max_chars=800)
            except Exception as e:
                current_app.logger.exception("[RAG] enrich_results_from_db falló")
                enriched = base_results
                warnings.append(f"enrichment_error: {type(e).__name__}: {e}")
        else:
            enriched = base_results

        # ---- MMR ----
        if mmr_on:
            enriched, w = mmr_reorder(enriched, query=query, model_name=model_name, lam=mmr_lambda, top_k=k)
            if w: warnings.append(w)

        # ---- Reranker ----
        if rerank_on:
            enriched, w = rerank_cross_encoder(enriched, query=query, top_k=max(10, k))
            if w: warnings.append(w)

        # Cobertura de campos (debug/TFM)
        coverage = {"title": 0, "path": 0, "chunk_index": 0, "text": 0}
        for r in enriched:
            if r.get("document_title"): coverage["title"] += 1
            if r.get("document_path"):  coverage["path"] += 1
            if r.get("chunk_index") is not None: coverage["chunk_index"] += 1
            if r.get("text"): coverage["text"] += 1

        meta = data.get("meta", {})
        resp = {
            "ok": True,
            "query": query,
            "k": k,
            "elapsed_ms": int((time.time() - t0) * 1000),
            "model_info": {
                "model": meta.get("model"),
                "dim": meta.get("dim"),
                "n_chunks": meta.get("n_chunks"),
                "collection": meta.get("collection"),
                "store": store,
            },
            "results": enriched,
            "total_results": len(enriched),
            "coverage": coverage,
        }
        if warnings: resp["warnings"] = warnings
        return jsonify(resp)

    except Exception as e:
        current_app.logger.exception("[RAG] query ERROR")
        err = {"ok": False, "error": f"{type(e).__name__}: {e}"}
        if debug:
            err["trace"] = traceback.format_exc()
        return jsonify(err), 500



@admin_rag_bp.route("/selftest")
@login_required
def rag_selftest():
    """
    Autodiagnóstico: carga índice, genera embedding y ejecuta búsqueda (sin UI).
    """
    import traceback
    models_dir = current_app.config.get("MODELS_DIR", "models")
    store = (request.args.get("store") or "chroma").strip().lower()
    collection = (request.args.get("collection") or "").strip()
    q = (request.args.get("q") or "test").strip()
    k = int(request.args.get("k") or 3)

    out: Dict[str, Any] = {"store": store, "collection": collection, "q": q, "k": k}
    try:
        data = get_store(store, collection, models_dir=models_dir)
        out["loaded"] = bool(data)
        out["meta"] = data.get("meta") if data else None
        if not data:
            return jsonify(out), 404

        model_name = (data.get("meta") or {}).get("model") or _DEFAULT_EMBED_MODEL
        try:
            emb = embed_query(q, model_name=model_name, normalize=True)
            out["embed_dim"] = int(emb.shape[1])
        except Exception as e:
            out["embed_error"] = f"{type(e).__name__}: {e}"

        try:
            res = search_faiss(data, q, k, model_name) if store == "faiss" else search_chroma(data, q, k, model_name)
            out["n"] = len(res)
            out["results"] = res
        except Exception as e:
            out["search_error"] = f"{type(e).__name__}: {e}"
            out["trace"] = traceback.format_exc()

        return jsonify(out)
    except Exception as e:
        out["fatal"] = f"{type(e).__name__}: {e}"
        out["trace"] = traceback.format_exc()
        return jsonify(out), 500


# === Evaluación mínima (TFM) ===
@admin_rag_bp.route("/eval")
@login_required
def rag_eval():
    """
    Ejecuta un set de evaluación simple con campos:
    [
      {"query":"...", "relevants":[123,456]},  # ids de chunk relevantes
      ...
    ]
    Archivo por defecto: models/eval/evalset.json
    Query params: store, collection, k (default 5), file
    """
    import math

    models_dir = current_app.config.get("MODELS_DIR", "models")
    store = (request.args.get("store") or "chroma").strip().lower()
    collection = (request.args.get("collection") or "").strip()
    k = int(request.args.get("k") or 5)
    file = (request.args.get("file") or "").strip() or str(Path(models_dir) / "eval" / "evalset.json")

    if not collection:
        return jsonify({"ok": False, "error": "Falta 'collection'"}), 400

    path = Path(file)
    if not path.exists():
        return jsonify({"ok": False, "error": f"No existe evalset: {file}"}), 404

    try:
        data = get_store(store, collection, models_dir=models_dir)
        if not data:
            return jsonify({"ok": False, "error": f"No existe la colección '{collection}' en {store}"}), 404
        model_name = (data.get("meta") or {}).get("model") or _DEFAULT_EMBED_MODEL

        items = json.loads(path.read_text(encoding="utf-8"))
        def run_one(q: str):
            if store == "faiss":
                res = search_faiss(data, q, k, model_name)
            else:
                res = search_chroma(data, q, k, model_name)
            return [r.get("chunk_id") for r in res]

        all_recall = []
        all_rr = []
        all_dcg = []
        all_idcg = []
        per: List[Dict[str, Any]] = []

        for it in items:
            q = (it.get("query") or "").strip()
            rel = it.get("relevants") or []
            preds = run_one(q)

            # Recall@k
            hits = len(set(preds) & set(rel))
            recall = hits / max(1, len(rel))

            # MRR
            rr = 0.0
            for rank, cid in enumerate(preds, start=1):
                if cid in rel:
                    rr = 1.0 / rank
                    break

            # nDCG@k
            def dcg(cands, rels):
                s = 0.0
                for i, cid in enumerate(cands, start=1):
                    gain = 1.0 if cid in rels else 0.0
                    s += gain / math.log2(i + 1)
                return s
            dcg_k = dcg(preds, rel)
            idcg_k = dcg(rel[:k], rel) if rel else 1.0
            ndcg = (dcg_k / idcg_k) if idcg_k > 0 else 0.0

            per.append({"query": q, "recall": recall, "mrr": rr, "ndcg": ndcg})
            all_recall.append(recall)
            all_rr.append(rr)
            all_dcg.append(dcg_k)
            all_idcg.append(idcg_k)

        out = {
            "ok": True,
            "store": store,
            "collection": collection,
            "k": k,
            "items": len(items),
            "metrics": {
                "Recall@k": sum(all_recall) / max(1, len(all_recall)),
                "MRR": sum(all_rr) / max(1, len(all_rr)),
                "nDCG@k": (sum(d / i for d, i in zip(all_dcg, all_idcg)) / max(1, len(all_dcg))) if all_idcg else 0.0,
            },
            "per_query": per
        }
        return jsonify(out)
    except Exception as e:
        return jsonify({"ok": False, "error": f"{type(e).__name__}: {e}"}), 500
