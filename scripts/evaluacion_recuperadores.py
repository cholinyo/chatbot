# scripts/evaluacion_recuperadores.py
# -*- coding: utf-8 -*-
"""
Evaluación de recuperadores (FAISS/Chroma) con métricas extendidas y latencias.

CSV soportado (compatible con scripts/make_queries_template.py):
  query, expected_chunk_id, expected_chunk_ids,
  expected_document_id, expected_document_title_contains, expected_text_contains

Métricas:
- Chunk: Recall@k, MRR@k
- DocID: Recall@k, MRR@k (por consulta)
- DocTitle-contains: Recall@k, MRR@k (por consulta; matching robusto: sin tildes, token-based)
- Text-contains (en chunk): Rate@k, MRR@k (por consulta; matching robusto)
- Latencias: p50, p95, mean

Artefactos:
  models/<store>/<collection>/eval/<ts>/{metrics.json, results.json, stdout.jsonl}

Uso:
  python -m scripts.evaluacion_recuperadores \
    --store chroma \
    --collection chunks_default \
    --k 10 \
    --queries-csv data/validation/queries.csv \
    --db-path data/processed/tracking.sqlite
"""
import argparse, csv, json, sys, time, math, sqlite3, re, unicodedata
from pathlib import Path
from datetime import datetime, timezone
from typing import List, Dict, Any, Tuple, Optional

import numpy as np

# Dependencias opcionales según store
try:
    import faiss  # faiss-cpu
except Exception:
    faiss = None

try:
    import chromadb
except Exception:
    chromadb = None

try:
    from sentence_transformers import SentenceTransformer
except Exception as e:
    print(json.dumps({"level":"ERROR","event":"import.error","module":"sentence_transformers","msg":str(e)}))
    sys.exit(1)


# ============== Utils & Logging ==============
def log(line: Dict[str, Any], fp):
    fp.write(json.dumps(line, ensure_ascii=False) + "\n")
    fp.flush()

def now_ts() -> str:
    # timezone-aware para evitar DeprecationWarning
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")

def percentile(values: List[float], p: float) -> float:
    if not values:
        return 0.0
    arr = sorted(values)
    k = (len(arr)-1) * (p/100.0)
    f, c = math.floor(k), math.ceil(k)
    if f == c:
        return arr[int(k)]
    return arr[f] + (arr[c] - arr[f]) * (k - f)

def norm_ws(s: Optional[str]) -> str:
    return " ".join((s or "").split())

def to_lower(s: Optional[str]) -> str:
    return (s or "").casefold()

def re_split_multi(s: str) -> List[str]:
    return re.split(r"\s*[|;,]\s*", s or "")

def strip_accents(s: str) -> str:
    s = s or ""
    return "".join(ch for ch in unicodedata.normalize("NFD", s) if unicodedata.category(ch) != "Mn")

def contains_match(text: Optional[str], pattern: Optional[str]) -> bool:
    """
    Coincidencia flexible:
      - casefold + sin tildes en ambos
      - si el patrón tiene >=2 tokens (letras/números), exige que TODOS estén en el texto
      - si tiene 1 token, usa substring
    """
    if not pattern:
        return False
    t = strip_accents((text or "").casefold())
    p = strip_accents((pattern or "").casefold())
    toks = re.findall(r"\w+", p, flags=re.UNICODE)
    toks = [tok for tok in toks if len(tok) > 1]
    if len(toks) >= 2:
        return all(tok in t for tok in toks)
    return p in t


# ============== CSV Loader (tolerante a cabeceras/UTF-8 BOM) ==============
_HEADER_ALIASES: Dict[str, set] = {
    "query": {"q"},
    "expected_chunk_id": {"chunk_id","expectedchunkid"},
    "expected_chunk_ids": {"expected_chunks","expected_chunkid_list","chunk_ids","expected_chunk_ids_list"},
    "expected_document_id": {"expected_doc_id","doc_id"},
    "expected_document_title_contains": {"expected_doc_title_contains","doc_title_contains","title_contains","expected_document_title"},
    "expected_text_contains": {"text_contains","chunk_text_contains"},
}

def _norm_fieldname(s: Optional[str]) -> str:
    # quita BOM, espacios, minúsculas, sustituye espacios por "_"
    s = (s or "").replace("\ufeff", "").strip().lower().replace(" ", "_")
    return s

def _canonical_key(k: str) -> str:
    nk = _norm_fieldname(k)
    for canon, alts in _HEADER_ALIASES.items():
        if nk == canon or nk in alts:
            return canon
    return nk

def load_queries_csv(path: Path) -> List[Dict[str, Any]]:
    """
    Loader tolerante a:
      - BOM / cabeceras con espacios
      - alias de columnas (doc_title_contains, text_contains, etc.)
    """
    items: List[Dict[str, Any]] = []
    with path.open("r", encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        raw_fields = reader.fieldnames or []
        field_map = {orig: _canonical_key(orig) for orig in raw_fields}
        for row in reader:
            safe = {field_map.get(k, _canonical_key(k)): v for k, v in row.items()}
            q = norm_ws(safe.get("query") or "")
            if not q:
                continue
            # chunk golds
            gold_chunks: List[str] = []
            one = norm_ws(safe.get("expected_chunk_id") or "")
            many = norm_ws(safe.get("expected_chunk_ids") or "")
            if one:
                gold_chunks.append(one)
            if many:
                for tok in [t.strip() for t in re_split_multi(many) if t.strip()]:
                    if tok not in gold_chunks:
                        gold_chunks.append(tok)
            items.append({
                "query": q,
                "gold_chunks": gold_chunks,
                "gold_doc_id": norm_ws(safe.get("expected_document_id") or ""),
                "gold_doc_title_sub": norm_ws(safe.get("expected_document_title_contains") or ""),
                "gold_text_sub": norm_ws(safe.get("expected_text_contains") or ""),
            })
    return items


# ============== Contract Readers ==============
def load_chunk_ids_from_contract(base_dir: Path) -> List[str]:
    manifest = base_dir / "index_manifest.json"
    if manifest.exists():
        try:
            data = json.loads(manifest.read_text(encoding="utf-8"))
            chunk_ids = data.get("chunk_ids") or []
            if isinstance(chunk_ids, list) and chunk_ids:
                return chunk_ids
        except Exception:
            pass
    ids_path = base_dir / "chunk_ids.json"
    if ids_path.exists():
        return json.loads(ids_path.read_text(encoding="utf-8"))
    raise FileNotFoundError(
        f"No se pudo obtener 'chunk_ids' en {base_dir}. "
        f"Se esperaba index_manifest.json con 'chunk_ids' o chunk_ids.json."
    )


# ============== Searchers ==============
class FaissSearcher:
    def __init__(self, base_dir: Path, model_name: str):
        if faiss is None:
            raise RuntimeError("FAISS no disponible.")
        self.index_path = self._find_faiss_index(base_dir)
        if not self.index_path or not self.index_path.exists():
            raise FileNotFoundError(f"No se encontró índice FAISS en {base_dir}")
        self.index = faiss.read_index(str(self.index_path))
        self.chunk_ids: List[str] = load_chunk_ids_from_contract(base_dir)
        if self.index.ntotal != len(self.chunk_ids):
            raise ValueError(f"Inconsistencia: index.ntotal={self.index.ntotal} vs len(chunk_ids)={len(self.chunk_ids)}")
        self.model = SentenceTransformer(model_name)

    def _find_faiss_index(self, base_dir: Path) -> Optional[Path]:
        cand = base_dir / "index.faiss"
        if cand.exists():
            return cand
        g = list(base_dir.glob("*.faiss"))
        return g[0] if g else None

    def search(self, query: str, k: int) -> Tuple[List[str], List[float], float]:
        t0 = time.perf_counter()
        q_emb = self.model.encode([query], normalize_embeddings=True)
        q = np.array(q_emb, dtype="float32")
        D, I = self.index.search(q, k)
        elapsed = (time.perf_counter() - t0) * 1000.0
        ids = [self.chunk_ids[i] for i in I[0] if i >= 0]
        sims = D[0].tolist()  # IndexFlatIP + L2 normalize => coseno exacto
        return ids, sims, elapsed


class ChromaSearcher:
    def __init__(self, collection_dir: Path, collection_name: str, model_name: str):
        if chromadb is None:
            raise RuntimeError("ChromaDB no disponible.")
        self.model = SentenceTransformer(model_name)
        self.client = chromadb.PersistentClient(path=str(collection_dir))
        coll = None
        try:
            coll = self.client.get_collection(collection_name)
        except Exception:
            cols = self.client.list_collections()
            if len(cols) == 1:
                coll = cols[0]
        if coll is None:
            coll = self.client.get_or_create_collection(collection_name, metadata={"hnsw:space":"cosine"})
        self.collection = coll

    def search(self, query: str, k: int) -> Tuple[List[str], List[float], float]:
        t0 = time.perf_counter()
        q_emb = self.model.encode([query], normalize_embeddings=True).tolist()
        # En tu versión de Chroma, 'ids' puede no ser aceptado en include → usamos metadatas+distances
        res = self.collection.query(
            query_embeddings=q_emb,
            n_results=k,
            include=["metadatas", "distances"]
        )
        elapsed = (time.perf_counter() - t0) * 1000.0
        ids = (res.get("ids") or [[]])[0]  # algunos builds devuelven 'ids' aunque no se pida
        dists = (res.get("distances") or [[]])[0]
        sims = [1.0 - float(d) if d is not None else 0.0 for d in dists]  # cosine similarity

        # Fallback: si no hay 'ids', reconstruir desde metadatas[].chunk_id
        if not ids:
            metas = (res.get("metadatas") or [[]])[0]
            if metas:
                ids = [m.get("chunk_id") for m in metas if isinstance(m, dict) and m.get("chunk_id")]
        ids = ids or [""] * len(sims)
        return ids, sims, elapsed


# ============== SQLite Helpers ==============
def detect_tables_and_columns(conn: sqlite3.Connection) -> Dict[str, Any]:
    """
    Descubre tablas y columnas comunes:
      Tablas: Chunk/Document | chunks/documents | chunk/document
      Columnas: Document.title|name|titulo ; Chunk.text|content
    """
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    cur.execute("SELECT name FROM sqlite_master WHERE type='table'")
    tables = {row["name"].lower(): row["name"] for row in cur.fetchall()}

    def pick(cands: List[str]) -> Optional[str]:
        for k in cands:
            if k.lower() in tables:
                return tables[k.lower()]
        return None

    t_chunk = pick(["Chunk","chunks","chunk"])
    t_doc   = pick(["Document","documents","document"])

    title_col = None
    text_col = None
    if t_doc:
        cur.execute(f"PRAGMA table_info({t_doc})")
        cols_doc = {r["name"].lower(): r["name"] for r in cur.fetchall()}
        for c in ["title","name","titulo"]:
            if c in cols_doc:
                title_col = cols_doc[c]
                break
    if t_chunk:
        cur.execute(f"PRAGMA table_info({t_chunk})")
        cols_chunk = {r["name"].lower(): r["name"] for r in cur.fetchall()}
        for c in ["text","content"]:
            if c in cols_chunk:
                text_col = cols_chunk[c]
                break

    return {"t_chunk": t_chunk, "t_doc": t_doc, "title_col": title_col, "text_col": text_col}


def fetch_chunk_metadata(db_path: Path, chunk_ids: List[str], stdout_fp) -> Dict[str, Dict[str, Any]]:
    """
    Devuelve {chunk_id: {"document_id": <id>, "document_title": <title>, "text": <chunk_text>}}
    Si DB o columnas/tablas no están disponibles, retorna {} y se omiten métricas de doc/text.
    """
    log({"level":"INFO","event":"db.open","db_path":str(db_path),"n_chunk_ids":len(chunk_ids)}, stdout_fp)
    if not db_path or not db_path.exists():
        log({"level":"WARNING","event":"db.missing","db_path":str(db_path) if db_path else "(none)"}, stdout_fp)
        return {}

    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    try:
        meta = detect_tables_and_columns(conn)
        t_chunk, t_doc = meta["t_chunk"], meta["t_doc"]
        title_col, text_col = meta["title_col"], meta["text_col"]
        if not t_chunk or not t_doc:
            log({"level":"WARNING","event":"db.lookup.miss","reason":"missing tables Chunk/Document"}, stdout_fp)
            return {}

        fields = ["c.id AS chunk_id", "c.document_id AS document_id"]
        select_text = (text_col is not None)
        select_title = (title_col is not None)
        if select_text:
            fields.append(f"c.{text_col} AS chunk_text")
        if select_title:
            fields.append(f"d.{title_col} AS document_title")

        placeholders = ",".join("?" * len(chunk_ids)) if chunk_ids else "''"
        sql = (
            f"SELECT {', '.join(fields)} "
            f"FROM {t_chunk} c JOIN {t_doc} d ON d.id = c.document_id "
            f"WHERE c.id IN ({placeholders})"
        )

        cur = conn.execute(sql, chunk_ids)
        out: Dict[str, Dict[str, Any]] = {}
        for row in cur.fetchall():
            cid = str(row["chunk_id"])
            m: Dict[str, Any] = {
                "document_id": row["document_id"] if "document_id" in row.keys() else None,
            }
            if select_title and "document_title" in row.keys():
                m["document_title"] = row["document_title"]
            if select_text and "chunk_text" in row.keys():
                m["text"] = row["chunk_text"]
            out[cid] = m
        log({"level":"INFO","event":"db.lookup.done","n_rows":len(out)}, stdout_fp)
        return out
    except Exception as e:
        log({"level":"WARNING","event":"db.lookup.error","msg":str(e)}, stdout_fp)
        return {}
    finally:
        conn.close()


# ============== Métricas ==============
def recall_at_k(results: List[List[str]], targets: List[List[str]], k: int) -> float:
    hits = 0
    total = 0
    for res, gold in zip(results, targets):
        if not gold:
            continue
        total += 1
        topk = set(res[:k])
        if any(g in topk for g in gold):
            hits += 1
    return hits / total if total else 0.0

def mrr_at_k(results: List[List[str]], targets: List[List[str]], k: int) -> float:
    rr_sum = 0.0
    total = 0
    for res, gold in zip(results, targets):
        if not gold:
            continue
        total += 1
        rank = None
        for i, cid in enumerate(res[:k], start=1):
            if cid in gold:
                rank = i
                break
        rr_sum += (1.0 / rank) if rank else 0.0
    return rr_sum / total if total else 0.0


# ============== Core Eval ==============
class AnySearcher:
    def __init__(self, store: str, collection: str, model_name: str, models_dir: Path):
        self.store = store
        if store == "faiss":
            base = models_dir / "faiss" / collection
            self.impl = FaissSearcher(base, model_name)
        elif store == "chroma":
            base = models_dir / "chroma" / collection
            self.impl = ChromaSearcher(base, collection, model_name)
        else:
            raise ValueError(f"Store no soportado: {store}")

    def search(self, query: str, k: int) -> Tuple[List[str], List[float], float]:
        return self.impl.search(query, k)


def run_eval(store: str, collection: str, model_name: str, k: int,
             queries_csv: Path, out_base_dir: Path, db_path: Path, stdout_fp, ts: str) -> Dict[str, Any]:
    queries = load_queries_csv(queries_csv)
    if not queries:
        raise ValueError("El CSV no contiene queries válidas.")

    eval_dir = out_base_dir / store / collection / "eval" / ts
    eval_dir.mkdir(parents=True, exist_ok=True)

    searcher = AnySearcher(store, collection, model_name, out_base_dir)

    all_ids: List[List[str]] = []
    all_sims: List[List[float]] = []
    all_lat: List[float] = []
    per_query: List[Dict[str, Any]] = []

    for idx, q in enumerate(queries):
        qtext = q["query"]
        ids, sims, ms = searcher.search(qtext, k)
        all_ids.append(ids)
        all_sims.append(sims)
        all_lat.append(ms)
        per_query.append({
            "i": idx,
            "query": qtext,
            "gold": {
                "chunks": q["gold_chunks"],
                "document_id": q["gold_doc_id"],
                "document_title_contains": q["gold_doc_title_sub"],
                "text_contains": q["gold_text_sub"],
            },
            "results": [{"chunk_id": cid, "score": float(s)} for cid, s in zip(ids, sims)],
            "latency_ms": ms
        })
        log({"level":"INFO","event":"eval.query.done","store":store,"collection":collection,"i":idx,"latency_ms":ms,"n_results":len(ids)}, stdout_fp)

    # Enriquecer con metadatos desde SQLite
    flat_chunk_ids = sorted({r["chunk_id"] for rec in per_query for r in rec["results"] if r["chunk_id"]})
    meta_map: Dict[str, Dict[str, Any]] = {}
    if flat_chunk_ids:
        meta_map = fetch_chunk_metadata(db_path, flat_chunk_ids, stdout_fp)

    for rec in per_query:
        gold_doc_id = rec["gold"]["document_id"]
        gold_title = rec["gold"]["document_title_contains"]
        gold_text  = rec["gold"]["text_contains"]
        for r in rec["results"]:
            cid = r["chunk_id"]
            meta = meta_map.get(cid) or {}
            if "document_id" in meta and meta["document_id"] is not None:
                r["document_id"] = meta["document_id"]
            if "document_title" in meta and meta["document_title"]:
                r["document_title"] = meta["document_title"]
            if gold_doc_id:
                r["matched_doc_id"] = (str(meta.get("document_id","")) == gold_doc_id)
            if gold_title:
                r["matched_doc_title_contains"] = contains_match(meta.get("document_title"), gold_title)
            if gold_text:
                r["matched_text_contains"] = contains_match(meta.get("text"), gold_text)

    # ---- Métricas agregadas ----
    # chunk-level
    gold_chunks_list = [q["gold_chunks"] for q in queries]
    recall_chunk = recall_at_k(all_ids, gold_chunks_list, k)
    mrr_chunk = mrr_at_k(all_ids, gold_chunks_list, k)
    n_chunk_gold = sum(1 for g in gold_chunks_list if g)

    # doc-id por consulta
    docid_recalls, docid_rrs, n_docid_eval = [], [], 0
    if meta_map:
        for res_ids, q in zip(all_ids, queries):
            gold = q["gold_doc_id"]
            if not gold:
                continue
            n_docid_eval += 1
            found_rank = None
            hit = False
            for i, cid in enumerate(res_ids[:k], start=1):
                mm = meta_map.get(cid) or {}
                if str(mm.get("document_id","")) == gold:
                    hit = True
                    found_rank = i
                    break
            docid_recalls.append(1.0 if hit else 0.0)
            docid_rrs.append(1.0/found_rank if found_rank else 0.0)
    docid_recall = (sum(docid_recalls)/n_docid_eval) if n_docid_eval else 0.0
    docid_mrr    = (sum(docid_rrs)/n_docid_eval) if n_docid_eval else 0.0

    # doc title contains por consulta
    title_recalls, title_rrs, n_title_eval = [], [], 0
    if meta_map:
        for res_ids, q in zip(all_ids, queries):
            sub = q["gold_doc_title_sub"]
            if not sub:
                continue
            n_title_eval += 1
            found_rank = None
            hit = False
            for i, cid in enumerate(res_ids[:k], start=1):
                mm = meta_map.get(cid) or {}
                if sub and contains_match(mm.get("document_title"), sub):
                    hit = True
                    found_rank = i
                    break
            title_recalls.append(1.0 if hit else 0.0)
            title_rrs.append(1.0/found_rank if found_rank else 0.0)
    title_recall = (sum(title_recalls)/n_title_eval) if n_title_eval else 0.0
    title_mrr    = (sum(title_rrs)/n_title_eval) if n_title_eval else 0.0

    # text contains por consulta
    text_recalls, text_rrs, n_text_eval = [], [], 0
    if meta_map:
        for res_ids, q in zip(all_ids, queries):
            sub = q["gold_text_sub"]
            if not sub:
                continue
            n_text_eval += 1
            found_rank = None
            hit = False
            for i, cid in enumerate(res_ids[:k], start=1):
                mm = meta_map.get(cid) or {}
                if sub and contains_match(mm.get("text"), sub):
                    hit = True
                    found_rank = i
                    break
            text_recalls.append(1.0 if hit else 0.0)
            text_rrs.append(1.0/found_rank if found_rank else 0.0)
    text_recall = (sum(text_recalls)/n_text_eval) if n_text_eval else 0.0
    text_mrr    = (sum(text_rrs)/n_text_eval) if n_text_eval else 0.0

    metrics = {
        "store": store,
        "collection": collection,
        "model": model_name,
        "k": k,
        "n_queries": len(queries),
        "counts": {
            "with_chunk_gold": n_chunk_gold,
            "with_doc_id_gold": n_docid_eval,
            "with_doc_title_contains_gold": n_title_eval,
            "with_text_contains_gold": n_text_eval,
        },
        "chunk": {
            "recall_at_k": recall_chunk,
            "mrr_at_k": mrr_chunk,
        },
        "doc_id": {
            "recall_at_k": docid_recall,
            "mrr_at_k": docid_mrr,
        },
        "doc_title_contains": {
            "recall_at_k": title_recall,
            "mrr_at_k": title_mrr,
        },
        "text_contains": {
            "rate_at_k": text_recall,
            "mrr_at_k": text_mrr,
        },
        "latency_ms": {
            "p50": percentile(all_lat, 50),
            "p95": percentile(all_lat, 95),
            "mean": sum(all_lat)/len(all_lat) if all_lat else 0.0
        }
    }

    # Artefactos
    (eval_dir / "metrics.json").write_text(json.dumps(metrics, indent=2, ensure_ascii=False), encoding="utf-8")
    (eval_dir / "results.json").write_text(json.dumps(per_query, indent=2, ensure_ascii=False), encoding="utf-8")

    log({"level":"INFO","event":"eval.done","store":store,"collection":collection,"metrics_path":str(eval_dir / "metrics.json")}, stdout_fp)
    return {"eval_dir": str(eval_dir), "metrics": metrics}


# ============== CLI ==============
def main():
    ap = argparse.ArgumentParser(description="Evaluación FAISS/Chroma: Recall/MRR (chunk, doc) y latencias.")
    ap.add_argument("--store", choices=["faiss","chroma"], required=True)
    ap.add_argument("--collection", required=True)
    ap.add_argument("--model", default="sentence-transformers/all-MiniLM-L6-v2")
    ap.add_argument("--k", type=int, default=5)
    ap.add_argument("--queries-csv", required=True, help="CSV con columnas: query, expected_*")
    ap.add_argument("--models-dir", default="models", help="Directorio base de artefactos (models/...)")
    ap.add_argument("--db-path", default="data/processed/tracking.sqlite", help="Ruta a la BD SQLite (Document/Chunk)")
    args = ap.parse_args()

    ts = now_ts()
    models_dir = Path(args.models_dir).resolve()
    queries_csv = Path(args.queries_csv).resolve()
    db_path = Path(args.db_path).resolve()
    stdout_path = models_dir / args.store / args.collection / "eval" / ts / "stdout.jsonl"
    stdout_path.parent.mkdir(parents=True, exist_ok=True)

    with stdout_path.open("w", encoding="utf-8") as fp:
        log({"level":"INFO","event":"eval.start","store":args.store,"collection":args.collection,
             "k":args.k,"queries_csv":str(queries_csv),"db":str(db_path),"ts":ts}, fp)
        try:
            res = run_eval(args.store, args.collection, args.model, args.k, queries_csv, models_dir, db_path, fp, ts)
            print(json.dumps({"ok": True, "eval_dir": res["eval_dir"], "metrics": res["metrics"]}, ensure_ascii=False))
        except Exception as e:
            log({"level":"ERROR","event":"eval.error","msg":str(e)}, fp)
            print(json.dumps({"ok": False, "error": str(e)}))
            sys.exit(1)


if __name__ == "__main__":
    main()
