# app/datasources/query_hub.py
from __future__ import annotations

import os
import json
import sqlite3
from pathlib import Path
from typing import List, Dict, Any, Optional, Tuple

import numpy as np
from flask import current_app

# Embeddings unificados (384D)
from app.core.embeddings_registry import get_embedding_from_env

# Chroma (vector store)
try:
    import chromadb  # type: ignore
except Exception:  # pragma: no cover
    chromadb = None  # type: ignore

# FAISS
try:
    import faiss  # type: ignore
except Exception:  # pragma: no cover
    faiss = None  # type: ignore


MODELS_DIR = Path(os.getenv("MODELS_DIR", "models")).resolve()
GRAPHML_NAME = "graph_chunk_entity_relation.graphml"


def _list_subdirs(base: Path) -> List[str]:
    if not base.exists():
        return []
    return sorted([p.name for p in base.iterdir() if p.is_dir()])


def list_vector_collections() -> Dict[str, List[str]]:
    """Descubre colecciones FAISS/Chroma por carpeta (heurístico simple)."""
    faiss_dirs = _list_subdirs(MODELS_DIR / "faiss")
    chroma_dirs = _list_subdirs(MODELS_DIR / "chroma")
    return {"faiss": faiss_dirs, "chroma": chroma_dirs}


def list_kg_namespaces(emb_dim: int = 384) -> List[str]:
    """Descubre namespaces KG que tengan el GraphML en emb-<dim>."""
    base = Path(os.getenv("LIGHTRAG_WORKDIR", MODELS_DIR / "kg")).resolve()
    out: List[str] = []
    if not base.exists():
        return out
    for ns_dir in base.iterdir():
        if not ns_dir.is_dir():
            continue
        gml = ns_dir / f"emb-{emb_dim}" / GRAPHML_NAME
        if gml.exists():
            out.append(ns_dir.name)
    return sorted(out)


# ===========================
# Utilidades comunes
# ===========================
def _encode(texts: List[str]) -> np.ndarray:
    """Embeddings 384D para una lista de textos."""
    emb = get_embedding_from_env()
    vecs = emb.encode(texts)  # type: ignore
    return np.array(vecs, dtype="float32")


def _cosine_sim(a: np.ndarray, b: np.ndarray) -> np.ndarray:
    """cosine(a,b) con broadcasting: a:[d], b:[n,d] -> [n]."""
    a = a / (np.linalg.norm(a) + 1e-12)
    b_norm = b / (np.linalg.norm(b, axis=1, keepdims=True) + 1e-12)
    return b_norm @ a


def _mmr_select(query_vec: np.ndarray, cand_vecs: np.ndarray, top_k: int, lambda_mult: float = 0.5) -> List[int]:
    """
    Maximal Marginal Relevance (MMR) greedy.
    query_vec: [d]; cand_vecs: [n,d]
    return: indices seleccionados (longitud <= top_k)
    """
    n = cand_vecs.shape[0]
    sims = _cosine_sim(query_vec, cand_vecs)  # [n]
    selected: List[int] = []
    candidates = list(range(n))

    while len(selected) < min(top_k, n) and candidates:
        if not selected:
            # primero: mayor similitud con query
            idx = int(np.argmax(sims[candidates]))
            selected.append(candidates.pop(idx))
            continue
        # Diversidad vs relevancia
        sel_vecs = cand_vecs[selected]
        # similitud a lo más cercano ya seleccionado
        div = np.max(cand_vecs[candidates] @ (sel_vecs.T / (np.linalg.norm(sel_vecs, axis=1, keepdims=True) + 1e-12)), axis=1)
        score = lambda_mult * sims[candidates] - (1 - lambda_mult) * div
        idx = int(np.argmax(score))
        selected.append(candidates.pop(idx))
    return selected


def _format_hits_for_prompt(hits: List[Dict[str, Any]]) -> str:
    ctx_lines = []
    for h in hits:
        meta = h.get("meta") or {}
        src = meta.get("source") or meta.get("path") or meta.get("doc_id") or "doc"
        snippet = h.get("text") or ""
        ctx_lines.append(f"[{h.get('rank', '?')}] ({src}) {snippet}")
    return "\n".join(ctx_lines)


# ===========================
# CHROMA
# ===========================
def _chroma_query(folder_name: str, query: str, k: int = 4,
                  mmr: bool = False, rerank: bool = False) -> Dict[str, Any]:
    """
    PersistentClient en models/chroma/<folder_name>, consulta primera colección
    o la que coincida en nombre. Aplica MMR/Reranker si se piden.
    """
    if chromadb is None:
        raise RuntimeError("chromadb no instalado. pip install chromadb")

    persist_path = MODELS_DIR / "chroma" / folder_name
    client = chromadb.PersistentClient(path=str(persist_path))

    colls = client.list_collections()
    if not colls:
        return {"hits": [], "as_text": "", "note": f"Sin colecciones en {persist_path}"}

    coll = None
    names = [c.name for c in colls]
    if folder_name in names:
        coll = client.get_collection(folder_name)
    else:
        coll = client.get_collection(colls[0].name)

    # embed query
    qv = _encode([query])[0]

    res = coll.query(query_embeddings=[qv], n_results=max(k, 8 if (mmr or rerank) else k),
                     include=["documents", "metadatas", "distances"])
    docs = (res.get("documents") or [[]])[0]
    metas = (res.get("metadatas") or [[]])[0]
    dists = (res.get("distances") or [[]])[0]

    hits = [{
        "rank": i + 1, "text": docs[i] if i < len(docs) else "",
        "meta": metas[i] if i < len(metas) else {}, "distance": dists[i] if i < len(dists) else None
    } for i in range(min(len(docs), max(k, 8 if (mmr or rerank) else k)))]

    # Re-embed candidates for rerank/MMR if requested
    if hits and (mmr or rerank):
        cand_texts = [h["text"] for h in hits]
        cand_vecs = _encode(cand_texts)  # [n,d]

        order = list(range(len(hits)))
        if mmr:
            order = _mmr_select(qv, cand_vecs, top_k=k, lambda_mult=0.5)
        elif rerank:
            sims = _cosine_sim(qv, cand_vecs)
            order = list(np.argsort(-sims)[:k])

        hits = [hits[i] for i in order]
        # renumerar rank
        for i, h in enumerate(hits, 1):
            h["rank"] = i
    else:
        # trim
        hits = hits[:k]

    as_text = _format_hits_for_prompt(hits)
    return {"hits": hits, "as_text": as_text, "collection": coll.name, "persist_path": str(persist_path)}


# ===========================
# FAISS
# ===========================
def _maybe_read_jsonl(path: Path) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    if not path.exists():
        return out
    with path.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                out.append(json.loads(line))
            except Exception:
                continue
    return out


def _load_faiss_docstore(base_dir: Path) -> Tuple[List[str], List[Dict[str, Any]]]:
    """
    Carga el docstore para FAISS desde varios formatos comunes:
    - documents.jsonl (+ opcional metadatas.jsonl): cada línea: {"text": "...", "meta": {...}}
      (si metadatas.jsonl existe, empareja por línea)
    - docstore.jsonl: cada línea: {"id": ..., "text": "...", "meta": {...}}
    - store.sqlite: tabla docs(id TEXT, text TEXT, meta TEXT)
    Devuelve (texts, metas) con igual longitud.
    """
    # 1) documents.jsonl (+ metadatas.jsonl)
    docs_jsonl = base_dir / "documents.jsonl"
    metas_jsonl = base_dir / "metadatas.jsonl"
    if docs_jsonl.exists():
        docs = _maybe_read_jsonl(docs_jsonl)
        metas = _maybe_read_jsonl(metas_jsonl) if metas_jsonl.exists() else []
        texts: List[str] = []
        metas_out: List[Dict[str, Any]] = []
        for i, row in enumerate(docs):
            text = row.get("text") or row.get("document") or ""
            meta = row.get("meta") or {}
            if not meta and i < len(metas):
                m = metas[i]
                meta = m if isinstance(m, dict) else {}
            texts.append(text)
            metas_out.append(meta)
        return texts, metas_out

    # 2) docstore.jsonl
    docstore = base_dir / "docstore.jsonl"
    if docstore.exists():
        rows = _maybe_read_jsonl(docstore)
        texts = [r.get("text") or "" for r in rows]
        metas = [r.get("meta") or {} for r in rows]
        return texts, metas

    # 3) store.sqlite
    sqlite_p = base_dir / "store.sqlite"
    if sqlite_p.exists():
        try:
            con = sqlite3.connect(str(sqlite_p))
            cur = con.cursor()
            cur.execute("SELECT text, meta FROM docs")
            rows = cur.fetchall()
            con.close()
            texts = [(r[0] or "") for r in rows]
            metas = []
            for r in rows:
                try:
                    metas.append(json.loads(r[1]) if r[1] else {})
                except Exception:
                    metas.append({})
            return texts, metas
        except Exception as e:
            current_app.logger.warning("[FAISS] Error leyendo store.sqlite: %s", e)

    # Fallback vacío
    return [], []


def _faiss_query(folder_name: str, query: str, k: int = 4,
                 mmr: bool = False, rerank: bool = False) -> Dict[str, Any]:
    """
    Busca un índice FAISS en models/faiss/<folder_name>:
      - index.faiss (o *.faiss/*.index)
      - docstore: documents.jsonl (+metadatas.jsonl) | docstore.jsonl | store.sqlite
    Aplica MMR/Reranker si se solicitan.
    """
    if faiss is None:
        return {"hits": [], "as_text": "", "note": "faiss no instalado. pip install faiss-cpu"}

    base = MODELS_DIR / "faiss" / folder_name
    if not base.exists():
        return {"hits": [], "as_text": "", "note": f"No existe carpeta FAISS: {base}"}

    # Localizar índice
    idx_path = None
    for cand in ["index.faiss", "main.faiss", "index.index"]:
        p = base / cand
        if p.exists():
            idx_path = p
            break
    if not idx_path:
        # primer .faiss o .index que encontremos
        for p in base.glob("*.faiss"):
            idx_path = p; break
        if not idx_path:
            for p in base.glob("*.index"):
                idx_path = p; break
    if not idx_path:
        return {"hits": [], "as_text": "", "note": f"No se encontró archivo de índice FAISS en {base}"}

    try:
        index = faiss.read_index(str(idx_path))
    except Exception as e:
        return {"hits": [], "as_text": "", "note": f"Error leyendo índice FAISS: {type(e).__name__}: {e}"}

    # Docstore (textos y metadatos)
    texts, metas = _load_faiss_docstore(base)
    if not texts:
        return {"hits": [], "as_text": "", "note": "Docstore vacío o no encontrado (documents.jsonl/docstore.jsonl/store.sqlite)"}

    # Embed query y búsqueda
    qv = _encode([query]).astype("float32")  # [1,d]
    # Normalizamos si el índice es de inner product para simular coseno
    try:
        # si es IndexFlatIP, conviene normalizar
        if isinstance(index, faiss.IndexFlatIP):
            faiss.normalize_L2(qv)
    except Exception:
        pass

    D, I = index.search(qv, max(k, 8 if (mmr or rerank) else k))  # D: distancias/sims, I: ids
    ids = I[0].tolist() if len(I) else []
    dists = D[0].tolist() if len(D) else []

    # Construir hits iniciales
    hits: List[Dict[str, Any]] = []
    for rank, (idx, dist) in enumerate(zip(ids, dists), 1):
        if idx < 0 or idx >= len(texts):
            continue
        hits.append({
            "rank": rank,
            "text": texts[idx],
            "meta": metas[idx] if idx < len(metas) else {},
            "distance": float(dist),
            "faiss_id": int(idx),
        })

    # Re-embed candidates para MMR/rerank si procede
    if hits and (mmr or rerank):
        cand_texts = [h["text"] for h in hits]
        cand_vecs = _encode(cand_texts)  # [n,d]
        # normalizar si buscamos por coseno
        cand_vecs = cand_vecs.astype("float32")
        try:
            faiss.normalize_L2(cand_vecs)
        except Exception:
            pass
        q_vec = qv[0]

        order = list(range(len(hits)))
        if mmr:
            order = _mmr_select(q_vec, cand_vecs, top_k=k, lambda_mult=0.5)
        elif rerank:
            sims = _cosine_sim(q_vec, cand_vecs)
            order = list(np.argsort(-sims)[:k])

        hits = [hits[i] for i in order]
        for i, h in enumerate(hits, 1):
            h["rank"] = i
    else:
        hits = hits[:k]

    as_text = _format_hits_for_prompt(hits)
    return {"hits": hits, "as_text": as_text, "index_path": str(idx_path), "base_dir": str(base)}


def retrieve_vector(query: str, collection: Optional[Dict[str, str]],
                    k: int = 4, mmr: bool = False, rerank: bool = False) -> Dict[str, Any]:
    """
    collection: {"kind": "faiss|chroma", "name": "<folder>"}
    Implementa CHROMA y FAISS con MMR/rerank opcionales.
    """
    collection = collection or {}
    kind = (collection.get("kind") or "").lower()
    name = (collection.get("name") or "").strip()

    if kind == "chroma":
        if not name:
            return {"hits": [], "as_text": "", "note": "Colección Chroma no especificada"}
        return _chroma_query(name, query, k=k, mmr=mmr, rerank=rerank)

    if kind == "faiss":
        if not name:
            return {"hits": [], "as_text": "", "note": "Colección FAISS no especificada"}
        return _faiss_query(name, query, k=k, mmr=mmr, rerank=rerank)

    return {"hits": [], "as_text": "", "note": f"Tipo de colección no soportado: {kind}"}


# ===========================
# KG retrieval (LightRAG)
# ===========================
def query_kg(namespace: str, q: str) -> Dict[str, Any]:
    """
    Usa tu query híbrida de LightRAG. Import perezoso para evitar event loops en otras rutas.
    """
    from app.datasources.graphs.graph_registry import query_hybrid  # noqa: WPS433
    ans = query_hybrid(namespace, q)
    return {"answer": ans, "as_text": f"[KG:{namespace}] {ans}"}
