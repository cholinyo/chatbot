# scripts/check_docid_presence.py
# -*- coding: utf-8 -*-
"""
Comprueba, para queries con 'expected_document_id' en el CSV, si ese documento aparece
en el top-k de FAISS y/o Chroma y en qué rank (y también en top-probe_k).

Uso:
  python -m scripts.check_docid_presence ^
    --collection onda_docs ^
    --stores faiss,chroma ^
    --db-path data/processed/tracking.sqlite ^
    --queries-csv data/validation/queries.csv ^
    --k 20 ^
    --probe-k 200

Salidas (ejemplo):
  models/compare/<collection>/docid_check/<ts>/
    ├─ results.json
    ├─ results.md
    └─ stdout.jsonl
"""
import argparse, csv, io, json, os, re, sqlite3, time
from pathlib import Path
from typing import List, Dict, Optional

def utc_ts() -> str:
    return time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())

def log_jsonl(fp: Path, event: str, **fields):
    rec = {"ts": utc_ts(), "event": event}
    rec.update(fields or {})
    with fp.open("a", encoding="utf-8") as w:
        w.write(json.dumps(rec, ensure_ascii=False) + "\n")

def read_index_meta(models_dir: Path, store: str, collection: str) -> Dict:
    return json.loads((models_dir / store / collection / "index_meta.json").read_text(encoding="utf-8"))

def normalize_title(s: str) -> str:
    return (s or "").replace("|"," ").replace("\n"," ").strip()

def load_queries_with_docid(csv_path: Path) -> List[Dict]:
    # Maneja BOM y delimitador por defecto ","
    text = csv_path.read_text(encoding="utf-8-sig")
    reader = csv.DictReader(io.StringIO(text))
    # normaliza cabeceras a minúsculas
    reader.fieldnames = [h.lower() for h in (reader.fieldnames or [])]
    out = []
    for i, row in enumerate(reader, start=1):
        q = (row.get("query") or "").strip()
        docid = (row.get("expected_document_id") or "").strip()
        if q and docid:
            out.append({"idx": i, "query": q, "docid": str(docid)})
    return out

# --------- Enriquecimiento SQLite
def enrich_chunk_docs(db_path: Path, chunk_ids: List[str]) -> Dict[str, Dict]:
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
    out = {}
    for r in cur.execute(sql, [str(x) for x in chunk_ids]):
        out[str(r["chunk_id"])] = {"document_id": str(r["document_id"]), "document_title": r["document_title"]}
    con.close()
    return out

# --------- Retrievers
class FaissRetriever:
    def __init__(self, models_dir: Path, collection: str, model_name: str):
        import faiss, json as _json
        from sentence_transformers import SentenceTransformer
        base = models_dir / "faiss" / collection
        self.index = faiss.read_index(str(base / "index.faiss"))
        man = _json.loads((base / "index_manifest.json").read_text(encoding="utf-8"))
        self.chunk_ids = [str(x) for x in man["chunk_ids"]]
        self.model = SentenceTransformer(model_name)

    def search(self, query: str, k: int) -> List[Dict]:
        import numpy as np
        q = self.model.encode([query], normalize_embeddings=True)
        q = np.asarray(q, dtype="float32")
        D, I = self.index.search(q, k)
        out = []
        for pos, (idx, score) in enumerate(zip(I[0], D[0]), start=1):
            if idx < 0: continue
            out.append({"rank": pos, "chunk_id": self.chunk_ids[idx], "score": float(score)})
        return out

class ChromaRetriever:
    def __init__(self, models_dir: Path, collection: str, model_name: str):
        import chromadb
        from sentence_transformers import SentenceTransformer
        base = models_dir / "chroma" / collection
        self.client = chromadb.PersistentClient(path=str(base))
        self.coll = self.client.get_collection(collection)  # sin metadata (compat)
        self.model = SentenceTransformer(model_name)

    def search(self, query: str, k: int) -> List[Dict]:
        emb = self.model.encode([query], normalize_embeddings=False).tolist()
        res = self.coll.query(query_embeddings=emb, n_results=k, include=["metadatas","distances"])
        metas = (res.get("metadatas") or [[]])[0]
        dists = (res.get("distances") or [[]])[0]
        ids = []
        for i, m in enumerate(metas):
            cid = (m or {}).get("chunk_id")
            if cid is None:
                ids_all = (res.get("ids") or [[]])[0]
                cid = ids_all[i] if i < len(ids_all) else None
            ids.append(str(cid))
        sims = [1.0 - float(d) for d in dists]
        out = []
        for pos, (cid, s) in enumerate(zip(ids, sims), start=1):
            if cid is None: continue
            out.append({"rank": pos, "chunk_id": cid, "score": s})
        return out

def find_doc_rank(results: List[Dict], info: Dict[str, Dict], target_docid: str) -> Optional[int]:
    rank = None
    for r in results:
        docid = (info.get(r["chunk_id"]) or {}).get("document_id")
        if docid == target_docid:
            rank = r["rank"]
            break
    return rank

def main():
    ap = argparse.ArgumentParser(description="Comprueba rank de expected_document_id por store")
    ap.add_argument("--collection", required=True)
    ap.add_argument("--stores", default="faiss,chroma", help="faiss,chroma (orden no afecta)")
    ap.add_argument("--db-path", required=True)
    ap.add_argument("--queries-csv", required=True)
    ap.add_argument("--k", type=int, default=20)
    ap.add_argument("--probe-k", type=int, default=200)
    ap.add_argument("--models-dir", default="models")
    args = ap.parse_args()

    models_dir = Path(args.models_dir).resolve()
    out_dir = models_dir / "compare" / args.collection / "docid_check" / utc_ts()
    out_dir.mkdir(parents=True, exist_ok=True)
    log_fp = out_dir / "stdout.jsonl"
    log_jsonl(log_fp, "docid_check.start", collection=args.collection, k=args.k, probe_k=args.probe_k)

    rows = load_queries_with_docid(Path(args.queries_csv))
    if not rows:
        print(json.dumps({"ok": False, "error": "No hay queries con expected_document_id en el CSV"}, ensure_ascii=False))
        return

    # Modelos desde meta (evita mismatches)
    fa_model = read_index_meta(models_dir, "faiss", args.collection).get("model")
    ch_model = read_index_meta(models_dir, "chroma", args.collection).get("model")

    fa = FaissRetriever(models_dir, args.collection, fa_model)
    ch = ChromaRetriever(models_dir, args.collection, ch_model)

    results = {"collection": args.collection, "k": args.k, "probe_k": args.probe_k,
               "n": len(rows), "items": [], "agg": {}}

    def record_for_store(store_name: str, topk: List[Dict], probek: List[Dict], info: Dict[str, Dict], target_docid: str) -> Dict:
        r_at_k = find_doc_rank(topk, info, target_docid)
        r_at_probe = find_doc_rank(probek, info, target_docid)
        n_hits_k = sum(1 for r in topk if (info.get(r["chunk_id"]) or {}).get("document_id") == target_docid)
        return {
            "rank_at_k": r_at_k,
            "rank_at_probe": r_at_probe,
            "n_hits_k": n_hits_k,
            "gap_from_k": None if (r_at_k and r_at_k <= args.k) else (None if r_at_probe is None else max(0, r_at_probe - args.k))
        }

    for it in rows:
        q = it["query"]; docid = it["docid"]; idx = it["idx"]

        # recuperaciones
        fa_k  = fa.search(q, args.k)
        fa_p  = fa.search(q, args.probe_k) if args.probe_k > args.k else fa_k
        ch_k  = ch.search(q, args.k)
        ch_p  = ch.search(q, args.probe_k) if args.probe_k > args.k else ch_k

        # enriquecer una sola vez
        ids = [r["chunk_id"] for r in fa_p] + [r["chunk_id"] for r in ch_p]
        info = enrich_chunk_docs(Path(args.db_path), ids)

        row_res = {
            "idx": idx,
            "query": q,
            "expected_document_id": docid,
            "faiss": record_for_store("faiss", fa_k, fa_p, info, docid),
            "chroma": record_for_store("chroma", ch_k, ch_p, info, docid),
        }
        results["items"].append(row_res)
        log_jsonl(log_fp, "docid_check.item", idx=idx, query=q, docid=docid, faiss=row_res["faiss"], chroma=row_res["chroma"])

    # Agregado
    def count_found(store: str, within: str) -> int:
        c = 0
        for it in results["items"]:
            r = it[store]
            if within == "k":
                if r["rank_at_k"] is not None and r["rank_at_k"] <= args.k: c += 1
            elif within == "probe":
                if r["rank_at_probe"] is not None and r["rank_at_probe"] <= args.probe_k: c += 1
        return c

    def avg_gap(store: str) -> float:
        gaps = [it[store]["gap_from_k"] for it in results["items"] if it[store]["gap_from_k"] is not None]
        return sum(gaps)/len(gaps) if gaps else 0.0

    results["agg"] = {
        "found_at_k": {
            "faiss": count_found("faiss", "k"),
            "chroma": count_found("chroma", "k"),
        },
        "found_at_probe": {
            "faiss": count_found("faiss", "probe"),
            "chroma": count_found("chroma", "probe"),
        },
        "avg_gap_from_k": {
            "faiss": avg_gap("faiss"),
            "chroma": avg_gap("chroma"),
        }
    }

    # Persistir JSON
    (out_dir / "results.json").write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")

    # Render markdown
    lines = []
    lines.append(f"# DocID presence — colección `{args.collection}` (k={args.k}, probe_k={args.probe_k}, n={results['n']})")
    lines.append("")
    lines.append(f"- Encontrados en top-k: **FAISS {results['agg']['found_at_k']['faiss']} / {results['n']}**, **Chroma {results['agg']['found_at_k']['chroma']} / {results['n']}**")
    lines.append(f"- Encontrados en probe_k: **FAISS {results['agg']['found_at_probe']['faiss']} / {results['n']}**, **Chroma {results['agg']['found_at_probe']['chroma']} / {results['n']}**")
    lines.append(f"- Gap medio (rank - k) cuando está fuera de k: **FAISS {results['agg']['avg_gap_from_k']['faiss']:.1f}**, **Chroma {results['agg']['avg_gap_from_k']['chroma']:.1f}**")
    lines.append("")
    lines.append("idx | docid | query | FAISS rank@k | FAISS rank@probe | gap | Chroma rank@k | Chroma rank@probe | gap")
    lines.append("---:|---:|---|---:|---:|---:|---:|---:|---:")
    for it in results["items"]:
        frk = it["faiss"]["rank_at_k"]; frp = it["faiss"]["rank_at_probe"]; fg = it["faiss"]["gap_from_k"]
        crk = it["chroma"]["rank_at_k"]; crp = it["chroma"]["rank_at_probe"]; cg = it["chroma"]["gap_from_k"]
        def fmt(x): return "-" if x is None else str(x)
        lines.append(f"{it['idx']} | {it['expected_document_id']} | {it['query'].replace('|',' ')} | "
                     f"{fmt(frk)} | {fmt(frp)} | {fmt(fg)} | {fmt(crk)} | {fmt(crp)} | {fmt(cg)}")
    (out_dir / "results.md").write_text("\n".join(lines) + "\n", encoding="utf-8")

    log_jsonl(log_fp, "docid_check.done", out_dir=str(out_dir))
    print(json.dumps({"ok": True, "out_dir": str(out_dir)}, ensure_ascii=False))

if __name__ == "__main__":
    from pathlib import Path
    main()
