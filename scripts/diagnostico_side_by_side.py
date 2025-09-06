# scripts/diagnostico_side_by_side.py
# -*- coding: utf-8 -*-
"""
Diagnóstico side-by-side de recuperadores (FAISS vs Chroma) por query.

Genera, para cada consulta del CSV:
- Top-k de FAISS y Chroma sobre la MISMA colección
- Enriquecimiento desde SQLite (document_id, title)
- Métricas de solape (chunk_id y document_id) y Jaccard
- Informes Markdown por query y un summary agregado

Uso:
  python -m scripts.diagnostico_side_by_side \
    --collection onda_docs \
    --stores faiss,chroma \
    --db-path data/processed/tracking.sqlite \
    --queries-csv data/validation/queries.csv \
    --k 20

Salidas:
  models/compare/<collection>/diagnose/<ts>/
    ├─ summary.json
    ├─ summary.md
    ├─ queries/<idx>_<slug>.md
    └─ stdout.jsonl
"""
import argparse, csv, json, os, re, sqlite3, sys, time
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Tuple, Optional

# ---------- Utilidades
def utc_ts() -> str:
    # Formato compacto y estable
    return time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())

def slugify(s: str, maxlen: int = 80) -> str:
    s = (s or "").strip().lower()
    s = re.sub(r"[^a-z0-9áéíóúüñ\s\-_/]+", "", s)
    s = s.replace("/", " ")
    s = re.sub(r"\s+", "_", s)
    return s[:maxlen] or "query"

def read_queries(csv_path: Path) -> List[str]:
    out = []
    with csv_path.open("r", encoding="utf-8") as f:
        r = csv.DictReader(f)
        for row in r:
            q = (row.get("query") or "").strip()
            if q:
                out.append(q)
    return out

def read_index_meta(models_dir: Path, store: str, collection: str) -> Dict:
    meta_p = models_dir / store / collection / "index_meta.json"
    return json.loads(meta_p.read_text(encoding="utf-8"))

def log_jsonl(fp: Path, event: str, **fields):
    rec = {"ts": utc_ts(), "event": event}
    rec.update(fields or {})
    with fp.open("a", encoding="utf-8") as w:
        w.write(json.dumps(rec, ensure_ascii=False) + "\n")

# ---------- Enriquecimiento SQLite
@dataclass
class ChunkInfo:
    chunk_id: str
    document_id: Optional[int]
    document_title: Optional[str]

def enrich_chunks(db_path: Path, chunk_ids: List[str]) -> Dict[str, ChunkInfo]:
    if not chunk_ids:
        return {}
    con = sqlite3.connect(str(db_path)); con.row_factory = sqlite3.Row
    cur = con.cursor()
    ph = ",".join("?" * len(chunk_ids))
    sql = f"""
      SELECT c.id AS chunk_id, c.document_id, d.title AS document_title
      FROM chunks c
      JOIN documents d ON d.id = c.document_id
      WHERE CAST(c.id AS TEXT) IN ({ph})
    """
    out: Dict[str, ChunkInfo] = {}
    for r in cur.execute(sql, [str(x) for x in chunk_ids]):
        out[str(r["chunk_id"])] = ChunkInfo(
            chunk_id=str(r["chunk_id"]),
            document_id=r["document_id"],
            document_title=r["document_title"],
        )
    con.close()
    return out

# ---------- Retrievers
class FaissRetriever:
    def __init__(self, base_dir: Path, collection: str, model_name: str):
        import faiss
        from sentence_transformers import SentenceTransformer
        self.base = base_dir / "faiss" / collection
        self.index = faiss.read_index(str(self.base / "index.faiss"))
        man = json.loads((self.base / "index_manifest.json").read_text(encoding="utf-8"))
        self.chunk_ids = [str(x) for x in man["chunk_ids"]]
        self.model = SentenceTransformer(model_name)

    def search(self, query: str, k: int):
        import numpy as np
        q = self.model.encode([query], normalize_embeddings=True)
        q = np.asarray(q, dtype="float32")
        D, I = self.index.search(q, k)
        res = []
        for pos, (idx, score) in enumerate(zip(I[0], D[0]), start=1):
            if idx < 0: 
                continue
            cid = self.chunk_ids[idx]
            res.append({"rank": pos, "chunk_id": cid, "score": float(score)})
        return res

class ChromaRetriever:
    def __init__(self, base_dir: Path, collection: str, model_name: str):
        import chromadb
        from sentence_transformers import SentenceTransformer
        self.base = base_dir / "chroma" / collection
        self.client = chromadb.PersistentClient(path=str(self.base))
        self.coll = self.client.get_collection(collection)
        self.model = SentenceTransformer(model_name)

    def search(self, query: str, k: int):
        emb = self.model.encode([query], normalize_embeddings=False).tolist()
        res = self.coll.query(query_embeddings=emb, n_results=k, include=["metadatas","distances"])
        metas = (res.get("metadatas") or [[]])[0]
        dists = (res.get("distances") or [[]])[0]
        # ids puede o no venir según versión; preferimos metadata.chunk_id
        ids = []
        for i, m in enumerate(metas):
            cid = (m or {}).get("chunk_id")
            if cid is None:
                # fallback no esperado, intenta recuperar de ids si vienen
                ids_all = (res.get("ids") or [[]])[0]
                cid = ids_all[i] if i < len(ids_all) else None
            ids.append(str(cid))
        sims = [1.0 - float(d) for d in dists]  # cosine similarity
        out = []
        for pos, (cid, s) in enumerate(zip(ids, sims), start=1):
            if cid is None:
                continue
            out.append({"rank": pos, "chunk_id": cid, "score": s})
        return out

# ---------- Métricas simples
def jaccard(a: List[str], b: List[str]) -> float:
    A, B = set(a), set(b)
    if not A and not B:
        return 1.0
    if not A or not B:
        return 0.0
    return len(A & B) / float(len(A | B))

def compute_overlap_stats(fa: List[Dict], ch: List[Dict], enrich: Dict[str, ChunkInfo]) -> Dict:
    fa_ids = [r["chunk_id"] for r in fa]
    ch_ids = [r["chunk_id"] for r in ch]
    fa_doc = [enrich.get(cid).document_id for cid in fa_ids if cid in enrich]
    ch_doc = [enrich.get(cid).document_id for cid in ch_ids if cid in enrich]
    return {
        "chunks": {
            "overlap_count": len(set(fa_ids) & set(ch_ids)),
            "jaccard": jaccard(fa_ids, ch_ids),
        },
        "documents": {
            "overlap_count": len(set(fa_doc) & set(ch_doc)),
            "jaccard": jaccard([str(x) for x in fa_doc], [str(x) for x in ch_doc]),
        }
    }

# ---------- Render Markdown
def table_row(r, info: Dict[str, ChunkInfo]) -> str:
    cid = r["chunk_id"]
    meta = info.get(cid)
    did = meta.document_id if meta else ""
    ttl = (meta.document_title or "") if meta else ""
    ttl = ttl.replace("|"," ").replace("\n"," ")
    return f"{r['rank']} | {cid} | {r['score']:.4f} | {did} | {ttl[:120]}"

def render_query_md(q_idx: int, query: str, fa: List[Dict], ch: List[Dict], info: Dict[str, ChunkInfo], stats: Dict) -> str:
    head = f"# [{q_idx:04d}] {query}\n\n"
    summ = f"**Overlap chunks**: {stats['chunks']['overlap_count']}, **Jaccard**: {stats['chunks']['jaccard']:.3f}  \n" \
           f"**Overlap docs**: {stats['documents']['overlap_count']}, **Jaccard**: {stats['documents']['jaccard']:.3f}\n\n"
    hdr = "rank | chunk_id | score | document_id | title\n---:|---|---:|---:|---\n"
    left = "\n".join(table_row(r, info) for r in fa)
    right = "\n".join(table_row(r, info) for r in ch)
    body = "## FAISS (top-k)\n" + hdr + left + "\n\n" + "## Chroma (top-k)\n" + hdr + right + "\n"
    return head + summ + body

# ---------- Main
def main():
    ap = argparse.ArgumentParser(description="Diagnóstico side-by-side FAISS vs Chroma")
    ap.add_argument("--collection", required=True)
    ap.add_argument("--stores", default="faiss,chroma", help="Orden de comparación, ej. faiss,chroma")
    ap.add_argument("--db-path", required=True)
    ap.add_argument("--queries-csv", required=True)
    ap.add_argument("--k", type=int, default=20)
    ap.add_argument("--models-dir", default="models")
    args = ap.parse_args()

    models_dir = Path(args.models_dir).resolve()
    out_dir = models_dir / "compare" / args.collection / "diagnose" / utc_ts()
    (out_dir / "queries").mkdir(parents=True, exist_ok=True)
    log_fp = out_dir / "stdout.jsonl"

    log_jsonl(log_fp, "diag.start", collection=args.collection, stores=args.stores, k=args.k)

    # Descubrir el/los modelos a usar desde meta (por store)
    fa_meta = read_index_meta(models_dir, "faiss", args.collection)
    ch_meta = read_index_meta(models_dir, "chroma", args.collection)
    fa_model = fa_meta.get("model")
    ch_model = ch_meta.get("model")

    # Cargar retrievers
    fa = FaissRetriever(models_dir, args.collection, fa_model)
    ch = ChromaRetriever(models_dir, args.collection, ch_model)

    queries = read_queries(Path(args.queries_csv))
    summary = {"collection": args.collection, "k": args.k, "n_queries": len(queries), "per_query": []}

    for i, q in enumerate(queries, start=1):
        t0 = time.perf_counter()
        fa_res = fa.search(q, args.k)
        ch_res = ch.search(q, args.k)
        lat_ms = (time.perf_counter() - t0) * 1000.0

        # Enriquecer
        ids = [r["chunk_id"] for r in fa_res] + [r["chunk_id"] for r in ch_res]
        enrich = enrich_chunks(Path(args.db_path), ids)
        stats = compute_overlap_stats(fa_res, ch_res, enrich)

        # Guardar por query (md)
        slug = slugify(q)
        q_md = render_query_md(i, q, fa_res, ch_res, enrich, stats)
        (out_dir / "queries" / f"{i:04d}_{slug}.md").write_text(q_md, encoding="utf-8")

        # Registro en summary
        summary["per_query"].append({
            "idx": i, "query": q, "faiss_top1": fa_res[0]["chunk_id"] if fa_res else None,
            "chroma_top1": ch_res[0]["chunk_id"] if ch_res else None,
            "chunks_overlap": stats["chunks"]["overlap_count"],
            "chunks_jaccard": stats["chunks"]["jaccard"],
            "docs_overlap": stats["documents"]["overlap_count"],
            "docs_jaccard": stats["documents"]["jaccard"],
            "latency_ms_total": lat_ms
        })
        log_jsonl(log_fp, "diag.query.done", idx=i, query=q, latency_ms=lat_ms,
                  chunks_overlap=stats["chunks"]["overlap_count"], docs_overlap=stats["documents"]["overlap_count"])

    # Agregado rápido
    def avg(xs): 
        xs = [x for x in xs if x is not None]
        return sum(xs)/len(xs) if xs else 0.0

    summary["agg"] = {
        "avg_chunks_jaccard": avg([p["chunks_jaccard"] for p in summary["per_query"]]),
        "avg_docs_jaccard": avg([p["docs_jaccard"] for p in summary["per_query"]]),
        "mean_latency_ms_total": avg([p["latency_ms_total"] for p in summary["per_query"]]),
    }
    (out_dir / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")

    # summary.md
    lines = [
        f"# Diagnóstico side-by-side — colección `{args.collection}`",
        f"- k = {args.k}, n_queries = {len(queries)}",
        f"- avg Jaccard (chunks) = {summary['agg']['avg_chunks_jaccard']:.3f}",
        f"- avg Jaccard (docs)   = {summary['agg']['avg_docs_jaccard']:.3f}",
        f"- mean total latency   = {summary['agg']['mean_latency_ms_total']:.1f} ms",
        "",
        "## Índice de informes por query",
    ]
    for p in summary["per_query"]:
        slug = slugify(p["query"])
        lines.append(f"- [{p['idx']:04d} — {p['query']}](/queries/{p['idx']:04d}_{slug}.md)  "
                     f"(Jaccard docs: {p['docs_jaccard']:.3f}, overlap docs: {p['docs_overlap']})")
    (out_dir / "summary.md").write_text("\n".join(lines) + "\n", encoding="utf-8")

    log_jsonl(log_fp, "diag.done", out_dir=str(out_dir))
    print(json.dumps({"ok": True, "out_dir": str(out_dir)}, ensure_ascii=False))

if __name__ == "__main__":
    main()
