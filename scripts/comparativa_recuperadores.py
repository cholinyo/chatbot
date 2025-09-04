# scripts/comparativa_recuperadores.py
# -*- coding: utf-8 -*-
"""
Comparativa automática de recuperadores (Chroma/FAISS) y valores de k.
Ejecuta 'scripts.evaluacion_recuperadores' en matriz de {stores} x {ks}
y agrega resultados en un resumen único.

Artefactos:
  models/compare/<collection>/eval/<ts>/
    - matrix.json  (lista por caso con métricas clave)
    - matrix.md    (tabla Markdown lista para documentación)
    - stdout.jsonl (logs estructurados de la comparativa)

Uso básico:
  python -m scripts.comparativa_recuperadores ^
    --stores chroma,faiss ^
    --ks 10 ^
    --collection chunks_default ^
    --queries-csv data/validation/queries.csv ^
    --db-path data/processed/tracking.sqlite

Sweep típico:
  python -m scripts.comparativa_recuperadores ^
    --stores chroma,faiss ^
    --ks 5,10,20 ^
    --collection chunks_default ^
    --queries-csv data/validation/queries.csv ^
    --db-path data/processed/tracking.sqlite
"""
import argparse, json, subprocess, sys, time, math
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Dict, Any

def now_ts() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")

def log(fp, **kv):
    fp.write(json.dumps(kv, ensure_ascii=False) + "\n")
    fp.flush()

def pct(x: float) -> str:
    try:
        return f"{x*100:.1f}%"
    except Exception:
        return "0.0%"

def run_single_eval(python_exe: str, store: str, k: int, collection: str,
                    queries_csv: Path, db_path: Path, model: str, models_dir: Path) -> Dict[str, Any]:
    """Lanza `python -m scripts.evaluacion_recuperadores ...` y parsea el JSON final."""
    cmd = [
        python_exe, "-m", "scripts.evaluacion_recuperadores",
        "--store", store,
        "--collection", collection,
        "--k", str(k),
        "--queries-csv", str(queries_csv),
        "--db-path", str(db_path),
        "--model", model,
        "--models-dir", str(models_dir),
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    stdout = (proc.stdout or "").strip().splitlines()
    stderr = (proc.stderr or "").strip()
    # Buscar la última línea JSON válida
    parsed = None
    for line in reversed(stdout):
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
            if isinstance(obj, dict) and "ok" in obj:
                parsed = obj
                break
        except Exception:
            continue
    if parsed is None:
        raise RuntimeError(f"No se pudo parsear salida JSON del evaluador. Stderr:\n{stderr}\nStdout:\n{proc.stdout}")
    if not parsed.get("ok", False):
        raise RuntimeError(f"Evaluación falló: {parsed}")
    return parsed

def main():
    ap = argparse.ArgumentParser(description="Comparativa de recuperadores (stores) y ks.")
    ap.add_argument("--stores", default="chroma,faiss", help="Lista separada por comas (ej.: chroma,faiss)")
    ap.add_argument("--ks", default="10", help="Lista de k separados por comas (ej.: 5,10,20)")
    ap.add_argument("--collection", required=True)
    ap.add_argument("--queries-csv", required=True)
    ap.add_argument("--db-path", default="data/processed/tracking.sqlite")
    ap.add_argument("--model", default="sentence-transformers/all-MiniLM-L6-v2")
    ap.add_argument("--models-dir", default="models")
    args = ap.parse_args()

    stores: List[str] = [s.strip() for s in args.stores.split(",") if s.strip()]
    ks: List[int] = [int(x) for x in args.ks.split(",") if x.strip()]
    collection = args.collection
    queries_csv = Path(args.queries_csv).resolve()
    db_path = Path(args.db_path).resolve()
    models_dir = Path(args.models_dir).resolve()
    py = sys.executable

    ts = now_ts()
    out_dir = models_dir / "compare" / collection / "eval" / ts
    out_dir.mkdir(parents=True, exist_ok=True)
    stdout_path = out_dir / "stdout.jsonl"

    rows: List[Dict[str, Any]] = []

    with stdout_path.open("w", encoding="utf-8") as fp:
        log(fp, level="INFO", event="compare.start",
            stores=stores, ks=ks, collection=collection, queries=str(queries_csv), db=str(db_path), ts=ts)
        for st in stores:
            for k in ks:
                t0 = time.perf_counter()
                try:
                    res = run_single_eval(py, st, k, collection, queries_csv, db_path, args.model, models_dir)
                    metrics = res.get("metrics", {})
                    elapsed = (time.perf_counter() - t0) * 1000.0
                    row = {
                        "store": metrics.get("store", st),
                        "collection": metrics.get("collection", collection),
                        "model": metrics.get("model", args.model),
                        "k": metrics.get("k", k),
                        "n_queries": metrics.get("n_queries", 0),
                        "counts": metrics.get("counts", {}),
                        "chunk_recall": metrics.get("chunk", {}).get("recall_at_k", 0.0),
                        "chunk_mrr": metrics.get("chunk", {}).get("mrr_at_k", 0.0),
                        "docid_recall": metrics.get("doc_id", {}).get("recall_at_k", 0.0),
                        "docid_mrr": metrics.get("doc_id", {}).get("mrr_at_k", 0.0),
                        "title_recall": metrics.get("doc_title_contains", {}).get("recall_at_k", 0.0),
                        "text_rate": metrics.get("text_contains", {}).get("rate_at_k", 0.0),
                        "p50_ms": metrics.get("latency_ms", {}).get("p50", 0.0),
                        "p95_ms": metrics.get("latency_ms", {}).get("p95", 0.0),
                        "mean_ms": metrics.get("latency_ms", {}).get("mean", 0.0),
                        "eval_dir": res.get("eval_dir", ""),
                        "compare_runtime_ms": elapsed,
                    }
                    rows.append(row)
                    log(fp, level="INFO", event="compare.case.done", store=st, k=k,
                        eval_dir=row["eval_dir"],
                        chunk_recall=row["chunk_recall"], chunk_mrr=row["chunk_mrr"],
                        docid_recall=row["docid_recall"], docid_mrr=row["docid_mrr"],
                        p50_ms=row["p50_ms"], p95_ms=row["p95_ms"])
                except Exception as e:
                    log(fp, level="ERROR", event="compare.case.error", store=st, k=k, msg=str(e))

        # Guardar matrix.json
        (out_dir / "matrix.json").write_text(json.dumps(rows, indent=2, ensure_ascii=False), encoding="utf-8")

        # Generar matrix.md (tabla markdown)
        lines = []
        lines.append(f"# Comparativa de recuperadores — colección `{collection}`")
        lines.append("")
        lines.append("| Store | k | n | chunk@k | MRR | doc@k | docMRR | title@k | text@k | p50 ms | p95 ms | mean ms | eval_dir |")
        lines.append("|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---|")
        for r in rows:
            lines.append(
                f"| {r['store']} | {r['k']} | {r['n_queries']} | {pct(r['chunk_recall'])} | {r['chunk_mrr']:.3f} | "
                f"{pct(r['docid_recall'])} | {r['docid_mrr']:.3f} | {pct(r['title_recall'])} | {pct(r['text_rate'])} | "
                f"{r['p50_ms']:.1f} | {r['p95_ms']:.1f} | {r['mean_ms']:.1f} | {r['eval_dir']} |"
            )
        (out_dir / "matrix.md").write_text("\n".join(lines), encoding="utf-8")

        log(fp, level="INFO", event="compare.end", out_dir=str(out_dir), n_cases=len(rows))

    print(json.dumps({"ok": True, "out_dir": str(out_dir), "n_cases": len(rows)}, ensure_ascii=False))

if __name__ == "__main__":
    main()
