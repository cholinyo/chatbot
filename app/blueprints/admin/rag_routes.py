# app/blueprints/admin/rag_routes.py
from __future__ import annotations

import json
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
# Usamos el context manager oficial de tu proyecto: app/extensions/db.py → get_session()
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
# Cache por nombre de modelo (para soportar colecciones con modelos distintos)
_EMBEDDERS: Dict[str, Any] = {}
_DEFAULT_EMBED_MODEL = "sentence-transformers/all-MiniLM-L6-v2"


def _get_embedder(model_name: Optional[str]) -> Any:
    name = (model_name or _DEFAULT_EMBED_MODEL).strip()
    if name not in _EMBEDDERS:
        from sentence_transformers import SentenceTransformer
        _EMBEDDERS[name] = SentenceTransformer(name)
    return _EMBEDDERS[name]


def embed_query(text: str, model_name: Optional[str], normalize: bool = True):
    import numpy as np
    model = _get_embedder(model_name)
    vec = model.encode([text], normalize_embeddings=normalize)
    return np.asarray(vec, dtype="float32")  # (1, dim)


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

    # mínimos razonables
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

    # Embedding normalizado para compatibilidad IP≈cosine
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
        score_raw = float(score)  # [-1..1] si vectores normalizados con IP
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

    # Puedes desactivar telemetría en dev ajustando Settings si te molesta el log
    client = chromadb.PersistentClient(path=str(base), settings=Settings(allow_reset=False))
    col = client.get_or_create_collection(collection)

    # meta de archivo si existe
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
    col = store_data["collection"]

    q = embed_query(query, model_name=model_name, normalize=True).tolist()
    res = col.query(
        query_embeddings=q,
        n_results=k,
        include=["metadatas", "distances","documents"]  # ids siempre vienen; añade "documents" si guardaste texto
    )

    ids = (res.get("ids") or [[]])[0]
    distances = (res.get("distances") or [[]])[0]
    metadatas = (res.get("metadatas") or [[]])[0]
    documents = (res.get("documents") or [[]])[0] if "documents" in res else []

    out: List[Dict[str, Any]] = []
    for rank, (id_str, dist, meta) in enumerate(zip(ids, distances, metadatas), start=1):
        try:
            chunk_id = int(id_str)
        except Exception:
            chunk_id = id_str

        distance = float(dist)
        similarity = max(0.0, min(1.0, 1.0 - distance))

        item: Dict[str, Any] = {
            "chunk_id": chunk_id,
            "distance": distance,
            "similarity": similarity,
            "rank": rank,
            "meta": meta or {},
        }

        # --- Normaliza campos comunes desde meta ---
        # título
        title = None
        for k_title in ("document_title", "title", "source_title"):
            if meta and meta.get(k_title):
                title = meta.get(k_title)
                break
        if title:
            item["document_title"] = title

        # ruta / path / uri / file
        path = None
        for k_path in ("document_path", "path", "uri", "source", "file"):
            if meta and meta.get(k_path):
                path = meta.get(k_path)
                break
        if path:
            item["document_path"] = path

        # índice de chunk
        ci = None
        for k_ci in ("chunk_index", "index", "chunk"):
            if meta and meta.get(k_ci) is not None:
                ci = meta.get(k_ci)
                break
        if ci is not None:
            try:
                item["chunk_index"] = int(ci)
            except Exception:
                item["chunk_index"] = ci  # deja string si no es numérico

        # snippet fallback desde documents o meta
        txt = ""
        try:
            # si pediste "documents", úsalo como snippet
            i = rank - 1
            if documents and i < len(documents) and isinstance(documents[i], str):
                txt = documents[i]
        except Exception:
            pass
        if not txt and meta:
            # otras opciones comunes en meta
            for k_txt in ("text", "chunk_text", "content", "summary"):
                if isinstance(meta.get(k_txt), str) and meta.get(k_txt):
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


# === Enriquecimiento desde BD (opcional, robusto a tu wiring) ===
def enrich_results_from_db(rows: List[Dict[str, Any]], max_chars: int = 800) -> List[Dict[str, Any]]:
    """
    Usa app.extensions.db.get_session() para ser compatible con tu SessionLocal.
    SQLAlchemy 2.x style (select + scalars()).
    Si no hay ORM o no hay get_session, devuelve rows tal cual.
    """
    if not rows:
        return []
    if get_session is None or Chunk is Any or Document is Any:
        return rows

    # solo ids enteros se pueden mapear a BD
    try:
        chunk_ids = [int(r["chunk_id"]) for r in rows if isinstance(r.get("chunk_id"), int)]
    except Exception:
        chunk_ids = []
    if not chunk_ids:
        return rows

    from sqlalchemy import select  # import local para no requerirlo si no hay enriquecimiento

    with get_session() as sess:  # type: ignore[misc]
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


# === Descubrimiento de colecciones ===
def list_faiss_collections(models_dir: str = "models") -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    base = Path(models_dir) / "faiss"
    if not base.exists():
        return out
    for p in base.iterdir():
        if not p.is_dir():
            continue
        # lee meta si existe
        meta_path = p / "index_meta.json"
        meta = {}
        if meta_path.exists():
            try:
                meta = json.loads(meta_path.read_text(encoding="utf-8"))
            except Exception:
                meta = {}

        model = meta.get("model") or _DEFAULT_EMBED_MODEL
        dim = meta.get("dim")

        # intenta contar chunks con ids.npy
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

        # meta de archivo si existe
        meta_path = p / "index_meta.json"
        meta_file = {}
        if meta_path.exists():
            try:
                meta_file = json.loads(meta_path.read_text(encoding="utf-8"))
            except Exception:
                meta_file = {}

        model = meta_file.get("model") or _DEFAULT_EMBED_MODEL

        # cuenta con cliente
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
    """
    Render del laboratorio RAG.
    Además, pasamos colecciones para que el selector pueda pintarse server-side
    si el JS no corre (defensa ante fallos).
    """
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
    Valida (opcional) el modelo esperado por la UI para asegurar pruebas correctas.
    """
    import traceback

    t0 = time.time()
    models_dir = current_app.config.get("MODELS_DIR", "models")

    store = (request.args.get("store") or "chroma").strip().lower()
    collection = (request.args.get("collection") or "").strip()
    expected_model = (request.args.get("expected_model") or "").strip()  # validación UI
    enrich_flag = (request.args.get("enrich") or "1") != "0"
    debug = (request.args.get("debug") or "0") == "1"

    payload = request.get_json(silent=True) or {}
    query = (payload.get("query") or "").strip()
    k = int(payload.get("k") or 5)

    if not collection:
        return jsonify({"ok": False, "error": "Falta 'collection'"}), 400
    if not query:
        return jsonify({"ok": False, "error": "El body debe incluir 'query'"}), 400
    if store not in ("faiss", "chroma"):
        return jsonify({"ok": False, "error": f"Store no soportado: {store}"}), 400

    try:
        data = get_store(store, collection, models_dir=models_dir)
        if not data:
            return jsonify({"ok": False, "error": f"No existe la colección '{collection}' en {store}"}), 404

        # Modelo real de la colección (fuente de verdad)
        model_name = (data.get("meta") or {}).get("model") or _DEFAULT_EMBED_MODEL

        # Validación de coherencia con la UI (si la UI envía expected_model)
        if expected_model and expected_model != model_name:
            return jsonify({
                "ok": False,
                "error": f"Modelo de la colección: '{model_name}'. No coincide con el esperado por la UI: '{expected_model}'.",
                "model_info": {**(data.get("meta") or {}), "store": store}
            }), 409

        # Búsqueda
        if store == "faiss":
            base_results = search_faiss(data, query=query, k=k, model_name=model_name)
        else:
            base_results = search_chroma(data, query=query, k=k, model_name=model_name)

        # Enriquecimiento tolerante + warning en respuesta si falla
        warning = None
        if enrich_flag:
            try:
                enriched = enrich_results_from_db(base_results, max_chars=800)
            except Exception as e:
                current_app.logger.exception("[RAG] enrich_results_from_db falló")
                enriched = base_results
                warning = f"enrichment_error: {type(e).__name__}: {e}"
        else:
            enriched = base_results

        meta = data.get("meta", {})
        resp: Dict[str, Any] = {
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
        }
        if warning:
            resp["warning"] = warning
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
    Autodiagnóstico: carga índice, genera embedding y ejecuta búsqueda.
    Útil para ver errores exactos sin pasar por la UI.
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
