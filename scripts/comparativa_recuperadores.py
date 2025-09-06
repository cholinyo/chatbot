# scripts/comparativa_recuperadores.py
# -*- coding: utf-8 -*-
"""
Comparativa de recuperadores (FAISS vs Chroma) sobre una colección y un CSV de validación.

- Para cada store ∈ {faiss, chroma} y cada k solicitado:
  * Recupera top-k por query con el embedding del índice (desde index_meta.json).
  * Enriquce top-k con SQLite (chunks → documents) para calcular:
      - chunk@k y MRR (expected_chunk_id / expected_chunk_ids)
      - doc@k y docMRR     (expected_document_id)
      - title@k            (expected_document_title_contains)
      - text@k (rate)      (expected_text_contains)
  * Mide latencias por query (ms) y agrega p50/p95/mean.
  * Persiste resultados por store en: models/<store>/<collection>/eval/<ts>/{results.json, stdout.jsonl}

- Además escribe la matriz agregada en:
    models/compare/<collection>/eval/<ts>/{matrix.json, matrix.md, stdout.jsonl}

Uso (PowerShell):
  python -m scripts.comparativa_recuperadores `
    --stores chroma,faiss `
    --ks 20,25,30 `
    --collection onda_docs `
    --queries-csv data\validation\queries.csv `
    --db-path data\processed\tracking.sqlite

Notas:
- No introduce frameworks nuevos.
- Chroma: usar include=["metadatas","distances"] (compat); los ids pueden variar según versión.
- FAISS: IndexFlatIP + normalización L2 (coseno).
"""

import argparse, csv, io, json, os, re, sqlite3, statistics, time
from pathlib import Path
from typing import Dict, List, Optional, Tuple

# ---------------- Utilidades comunes

def utc_ts() -> str:
    # UTC compacto, estable
    return time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())

def log_jsonl(fp: Path, event: str, **fields):
    rec = {"ts": utc_ts(), "event": event}
    rec.update(fields or {})
    fp.parent.mkdir(parents=True, exist_ok=True)
    with fp.open("a", encoding="utf-8") as w:
        w.write(json.dumps(rec, ensure_ascii=False) + "\n")

def read_index_meta(models_dir: Path, store: str, collection: str) -> Dict:
    p = models_dir / store / collection / "index_meta.json"
    if not p.exists():
        raise FileNotFoundError(f"index_meta.json no encontrado: {p}")
    return json.loads(p.read_text(encoding="utf-8"))

def parse_list_field(val: str) -> List[str]:
    """
    Acepta separadores comunes: ',', ';', '|', espacios. Devuelve lista de strings limpias.
    """
    if not val:
        return []
    # Reemplaza separadores por coma y divide
    s = re.sub(r"[;\|\s]+", ",", str(val).strip())
    out = [x for x in s.split(",") if x]
    return out

def p50(values: List[float]) -> float:
    if not values:
        return 0.0
    return statistics.median(values)

def p95(values: List[float]) -> float:
    if not values:
        return 0.0
    xs = sorted(values)
    idx = int(round(0.95 * (len(xs) - 1)))
    return xs[idx]

# ---------------- Carga CSV de validación

def load_validation_rows(csv_path: Path) -> List[Dict]:
    text = csv_path.read_text(encoding="utf-8-sig")
    r = csv.DictReader(io.StringIO(text))
    # normaliza cabeceras
    r.fieldnames = [h.lower() for h in (r.fieldnames or [])]
    rows = []
    for row in r:
        rows.append({k.lower(): (v or "").strip() for k, v in row.items()})
    return rows

# ---------------- Enriquecimiento desde SQLite (chunks → documents)

def enrich_chunks(db_path: Path, chunk_ids: List[str]) -> Dict[str, Dict]:
    """
    Devuelve: {chunk_id: {"document_id": str, "document_title": str, "text": str}}
    """
    out: Dict[str, Dict] = {}
    if not chunk_ids:
        return out
    con = sqlite3.connect(str(db_path)); con.row_factory = sqlite3.Row
    cur = con.cursor()
    ph = ",".join("?" * len(chunk_ids))
    sql = f"""
      SELECT c.id AS chunk_id,
             c.document_id AS document_id,
             d.title AS document_title,
             c.text AS text
      FROM chunks c
      JOIN documents d ON d.id = c.document_id
      WHERE CAST(c.id AS TEXT) IN ({ph})
    """
    for r in cur.execute(sql, [str(x) for x in chunk_ids]):
        out[str(r["chunk_id"])] = {
            "document_id": str(r["document_id"]),
            "document_title": (r["document_title"] or ""),
            "text": (r["text"] or "")
        }
    con.close()
    return out

# ---------------- Retrievers

class FaissRetriever:
    def __init__(self, models_dir: Path, collection: str, model_name: str):
        import faiss, json as _json
        from sentence_transformers import SentenceTransformer
        base = models_dir / "faiss" / collection
        idx_path = base / "index.faiss"
        man_path = base / "index_manifest.json"
        if not idx_path.exists() or not man_path.exists():
            raise FileNotFoundError(f"Faltan artefactos FAISS en {base}")
        self.index = faiss.read_index(str(idx_path))
        man = _json.loads(man_path.read_text(encoding="utf-8"))
        self.chunk_ids = [str(x) for x in man["chunk_ids"]]
        self.model = SentenceTransformer(model_name)

    def search(self, query: str, k: int) -> List[Dict]:
        import numpy as np
        t0 = time.perf_counter()
        q = self.model.encode([query], normalize_embeddings=True)
        q = np.asarray(q, dtype="float32")
        D, I = self.index.search(q, k)
        lat_ms = (time.perf_counter() - t0) * 1000.0
        out = []
        for pos, (idx, score) in enumerate(zip(I[0], D[0]), start=1):
            if idx < 0:
                continue
            out.append({"rank": pos, "chunk_id": self.chunk_ids[idx], "score": float(score)})
        return out, lat_ms

class ChromaRetriever:
    def __init__(self, models_dir: Path, collection: str, model_name: str):
        import chromadb
        from sentence_transformers import SentenceTransformer
        base = models_dir / "chroma" / collection
        client = chromadb.PersistentClient(path=str(base))
        # Compat: get_collection SIN 'metadata='
        self.coll = client.get_collection(collection)
        self.model = SentenceTransformer(model_name)

    def search(self, query: str, k: int) -> List[Dict]:
        t0 = time.perf_counter()
        emb = self.model.encode([query], normalize_embeddings=False).tolist()
        # include SIN "ids" (compat con versiones que no lo admiten)
        res = self.coll.query(query_embeddings=emb, n_results=k, include=["metadatas","distances"])
        metas = (res.get("metadatas") or [[]])[0]
        dists = (res.get("distances") or [[]])[0]
        # Extrae chunk_id desde metadatas (contrato: {"chunk_id": "<id>"})
        out = []
        for i, dist in enumerate(dists):
            m = metas[i] if i < len(metas) else {}
            cid = (m or {}).get("chunk_id")
            if cid is None:
                continue
            sim = 1.0 - float(dist)  # cosine similarity
            out.append({"rank": i+1, "chunk_id": str(cid), "score": sim})
        lat_ms = (time.perf_counter() - t0) * 1000.0
        return out, lat_ms

# ---------------- Métricas

def first_rank_in_topk(topk: List[Dict], predicate) -> Optional[int]:
    for r in topk:
        if predicate(r):
            return r["rank"]
    return None

def mrr_from_rank(rank: Optional[int]) -> float:
    return 0.0 if (rank is None or rank <= 0) else (1.0 / float(rank))

def compute_metrics_for_query(topk: List[Dict],
                              gold: Dict,
                              info_by_chunk: Dict[str, Dict]) -> Dict:
    """
    Calcula métricas para una query dada:
      - chunk@k y chunkMRR
      - doc@k y docMRR
      - title@k
      - text_rate (text@k)
    """
    k = len(topk)
    chunk_ids = [r["chunk_id"] for r in topk]

    # Oro
    gold_chunk_ids = set(parse_list_field(gold.get("expected_chunk_ids") or ""))
    if gold.get("expected_chunk_id"):
        gold_chunk_ids.add(gold["expected_chunk_id"])

    gold_docid = (gold.get("expected_document_id") or "").strip()
    gold_title_contains = (gold.get("expected_document_title_contains") or "").strip().lower()
    gold_text_contains  = (gold.get("expected_text_contains") or "").strip().lower()

    # CHUNK: rank del 1er chunk oro en top-k
    rank_chunk = None
    if gold_chunk_ids:
        rank_chunk = first_rank_in_topk(topk, lambda r: r["chunk_id"] in gold_chunk_ids)

    # DOC: rank del 1er chunk cuyo document_id == esperado
    rank_doc = None
    if gold_docid:
        rank_doc = first_rank_in_topk(
            topk, lambda r: (info_by_chunk.get(r["chunk_id"]) or {}).get("document_id") == gold_docid
        )

    # TITLE: ¿algún top-k tiene document_title que contenga el patrón?
    title_hit = False
    if gold_title_contains:
        for r in topk:
            title = (info_by_chunk.get(r["chunk_id"]) or {}).get("document_title") or ""
            if gold_title_contains in title.lower():
                title_hit = True
                break

    # TEXT: ¿algún top-k tiene text que contenga el patrón?
    text_hit = False
    if gold_text_contains:
        for r in topk:
            txt = (info_by_chunk.get(r["chunk_id"]) or {}).get("text") or ""
            if gold_text_contains in txt.lower():
                text_hit = True
                break

    return {
        "chunk": {"hit": rank_chunk is not None, "mrr": mrr_from_rank(rank_chunk)},
        "doc":   {"hit": rank_doc   is not None, "mrr": mrr_from_rank(rank_doc)},
        "title": {"hit": bool(title_hit)},
        "text":  {"hit": bool(text_hit)},
    }

# ---------------- Evaluación por store y k

def evaluate_store_k(
    store: str,
    collection: str,
    k: int,
    rows: List[Dict],
    db_path: Path,
    models_dir: Path,
    override_model: Optional[str] = None,
    run_ts: Optional[str] = None,
) -> Dict:
    """
    Ejecuta la evaluación para un (store, k) y devuelve el dict de resultados agregados.
    También persiste artefactos por store en: models/<store>/<collection>/eval/<ts>/...
    """
    meta = read_index_meta(models_dir, store, collection)
    model_name = override_model or meta.get("model")
    out_dir = models_dir / store / collection / "eval" / (run_ts or utc_ts())
    out_dir.mkdir(parents=True, exist_ok=True)
    log_fp = out_dir / "stdout.jsonl"

    # Carga retriever
    if store == "faiss":
        retr = FaissRetriever(models_dir, collection, model_name)
    elif store == "chroma":
        retr = ChromaRetriever(models_dir, collection, model_name)
    else:
        raise ValueError(f"Store no soportado: {store}")

    # Contadores
    lat_ms: List[float] = []
    n = 0
    counts = {
        "with_chunk_gold": 0,
        "with_doc_id_gold": 0,
        "with_doc_title_contains_gold": 0,
        "with_text_contains_gold": 0,
    }
    # Acumuladores
    chunk_hits = 0
    chunk_mrrs: List[float] = []

    doc_hits = 0
    doc_mrrs: List[float] = []

    title_hits = 0
    text_hits = 0

    t0_all = time.perf_counter()

    log_jsonl(log_fp, "eval.start", store=store, collection=collection, k=k, model=model_name)

    for i, row in enumerate(rows, start=1):
        q = row.get("query") or ""
        if not q.strip():
            continue
        n += 1

        # Recuperación
        topk, ms = retr.search(q, k)
        lat_ms.append(ms)

        # Enriquecer con SQLite
        ids = [r["chunk_id"] for r in topk]
        info = enrich_chunks(db_path, ids)

        # Métricas por query
        m = compute_metrics_for_query(topk, row, info)

        # Oro presente por tipo
        if (row.get("expected_chunk_id") or row.get("expected_chunk_ids")):
            counts["with_chunk_gold"] += 1
        if row.get("expected_document_id"):
            counts["with_doc_id_gold"] += 1
        if row.get("expected_document_title_contains"):
            counts["with_doc_title_contains_gold"] += 1
        if row.get("expected_text_contains"):
            counts["with_text_contains_gold"] += 1

        # Acumular
        if m["chunk"]["hit"]:
            chunk_hits += 1
        chunk_mrrs.append(m["chunk"]["mrr"])

        if m["doc"]["hit"]:
            doc_hits += 1
        doc_mrrs.append(m["doc"]["mrr"])

        if m["title"]["hit"]:
            title_hits += 1
        if m["text"]["hit"]:
            text_hits += 1

        log_jsonl(log_fp, "eval.query.done",
                  idx=i, query=q, k=k, store=store,
                  chunk_hit=m["chunk"]["hit"], chunk_mrr=m["chunk"]["mrr"],
                  doc_hit=m["doc"]["hit"], doc_mrr=m["doc"]["mrr"],
                  title_hit=m["title"]["hit"], text_hit=m["text"]["hit"],
                  latency_ms=ms)

    dur_ms = (time.perf_counter() - t0_all) * 1000.0

    # Agregados (proteger divisiones)
    def mean(xs: List[float]) -> float:
        return sum(xs)/len(xs) if xs else 0.0

    res = {
        "store": store,
        "collection": collection,
        "model": model_name,
        "k": k,
        "n_queries": n,
        "counts": counts,
        "chunk_recall": (chunk_hits / counts["with_chunk_gold"]) if counts["with_chunk_gold"] else 0.0,
        "chunk_mrr": mean([x for x in chunk_mrrs if x is not None]) if counts["with_chunk_gold"] else 0.0,
        "docid_recall": (doc_hits / counts["with_doc_id_gold"]) if counts["with_doc_id_gold"] else 0.0,
        "docid_mrr": mean([x for x in doc_mrrs if x is not None]) if counts["with_doc_id_gold"] else 0.0,
        "title_recall": (title_hits / counts["with_doc_title_contains_gold"]) if counts["with_doc_title_contains_gold"] else 0.0,
        "text_rate": (text_hits / counts["with_text_contains_gold"]) if counts["with_text_contains_gold"] else 0.0,
        "p50_ms": p50(lat_ms),
        "p95_ms": p95(lat_ms),
        "mean_ms": mean(lat_ms),
        "eval_dir": str(out_dir),
        "compare_runtime_ms": dur_ms,
    }

    # Persistir resultados por store
    (out_dir / "results.json").write_text(json.dumps(res, ensure_ascii=False, indent=2), encoding="utf-8")
    log_jsonl(log_fp, "eval.done", summary=res)

    return res

# ---------------- Render de matriz

def render_matrix_md(rows: List[Dict]) -> str:
    lines = []
    lines.append(f"# Comparativa de recuperadores — colección `{rows[0]['collection']}`" if rows else "# Comparativa de recuperadores")
    lines.append("")
    lines.append("| Store | k | n | chunk@k | MRR | doc@k | docMRR | title@k | text@k | p50 ms | p95 ms | mean ms | eval_dir |")
    lines.append("|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---|")
    for r in rows:
        lines.append("| {store} | {k} | {n_queries} | {chunk_recall:.1%} | {chunk_mrr:.3f} | {docid_recall:.1%} | {docid_mrr:.3f} | {title_recall:.1%} | {text_rate:.1%} | {p50:.1f} | {p95:.1f} | {mean:.1f} | {edir} |".format(
            store=r["store"],
            k=r["k"],
            n_queries=r["n_queries"],
            chunk_recall=r["chunk_recall"],
            chunk_mrr=r["chunk_mrr"],
            docid_recall=r["docid_recall"],
            docid_mrr=r["docid_mrr"],
            title_recall=r["title_recall"],
            text_rate=r["text_rate"],
            p50=r["p50_ms"],
            p95=r["p95_ms"],
            mean=r["mean_ms"],
            edir=r["eval_dir"]
        ))
    return "\n".join(lines) + "\n"

# ---------------- Main

def main():
    ap = argparse.ArgumentParser(description="Comparativa FAISS vs Chroma con métricas de calidad y latencia.")
    ap.add_argument("--stores", default="chroma,faiss", help="Lista separada por comas. Ej: chroma,faiss")
    ap.add_argument("--ks", default="10", help="Lista separada por comas de valores k. Ej: 5,10,20")
    ap.add_argument("--collection", required=True)
    ap.add_argument("--queries-csv", required=True)
    ap.add_argument("--db-path", required=True)
    ap.add_argument("--models-dir", default="models")
    ap.add_argument("--model", help="(Opcional) Forzar modelo para ambos stores. Si no, se toma de index_meta.json")
    args = ap.parse_args()

    stores = [s.strip().lower() for s in (args.stores or "").split(",") if s.strip()]
    ks = [int(x) for x in (args.ks or "").split(",") if str(x).strip()]
    models_dir = Path(args.models_dir).resolve()
    rows = load_validation_rows(Path(args.queries_csv))

    run_ts = utc_ts()
    out_dir = models_dir / "compare" / args.collection / "eval" / run_ts
    out_dir.mkdir(parents=True, exist_ok=True)
    log_fp = out_dir / "stdout.jsonl"
    log_jsonl(log_fp, "compare.start", stores=stores, ks=ks, collection=args.collection, queries=len(rows))

    all_rows: List[Dict] = []
    for s in stores:
        for k in ks:
            res = evaluate_store_k(
                store=s,
                collection=args.collection,
                k=k,
                rows=rows,
                db_path=Path(args.db_path),
                models_dir=models_dir,
                override_model=args.model,
                run_ts=run_ts,   # para que todos los per-store eval tengan la misma marca de tiempo
            )
            all_rows.append(res)

    # Persistir matriz
    (out_dir / "matrix.json").write_text(json.dumps(all_rows, ensure_ascii=False, indent=2), encoding="utf-8")
    (out_dir / "matrix.md").write_text(render_matrix_md(all_rows), encoding="utf-8")
    log_jsonl(log_fp, "compare.done", out_dir=str(out_dir), n_cases=len(all_rows))

    print(json.dumps({"ok": True, "out_dir": str(out_dir), "n_cases": len(all_rows)}, ensure_ascii=False))

if __name__ == "__main__":
    main()
