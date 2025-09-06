# scripts/list_docid_gold.py
# -*- coding: utf-8 -*-
"""
Lista las queries que tienen 'expected_document_id' (oro por documento).

Uso:
  python -m scripts.list_docid_gold --csv data/validation/queries.csv
  python -m scripts.list_docid_gold --csv data/validation/queries.csv --format md --out data/validation/queries_docid_gold.md
  python -m scripts.list_docid_gold --csv data/validation/queries.csv --format json --out data/validation/queries_docid_gold.json
"""
import argparse
import csv
import io
import json
from pathlib import Path
from typing import List, Tuple

def sniff_delimiter(path: Path) -> str:
    sample = path.read_bytes()[:4096]
    # quitar BOM si existe y decodificar
    text = sample.decode("utf-8", errors="ignore")
    try:
        dialect = csv.Sniffer().sniff(text, delimiters=[",",";","|","\t"])
        return dialect.delimiter
    except Exception:
        return ","  # por defecto

def load_rows(csv_path: Path) -> List[dict]:
    # utf-8-sig para tragar BOM si lo hubiera
    with csv_path.open("r", encoding="utf-8-sig", newline="") as f:
        data = f.read()
    # detectar delimitador
    delim = sniff_delimiter(csv_path)
    reader = csv.DictReader(io.StringIO(data), delimiter=delim)
    # normalizar cabeceras a minúsculas
    reader.fieldnames = [h.lower() for h in (reader.fieldnames or [])]
    rows = []
    for row in reader:
        rows.append({k.lower(): v for k,v in row.items()})
    return rows

def main():
    ap = argparse.ArgumentParser(description="Lista queries con expected_document_id (oro por documento).")
    ap.add_argument("--csv", required=True, help="Ruta al CSV de queries (p.ej. data/validation/queries.csv)")
    ap.add_argument("--format", choices=["plain","md","json"], default="plain", help="Formato de salida (stdout o --out).")
    ap.add_argument("--out", help="Fichero de salida opcional. Si no se indica, imprime por stdout.")
    args = ap.parse_args()

    csv_path = Path(args.csv)
    if not csv_path.exists():
        print(json.dumps({"ok": False, "error": f"CSV no encontrado: {csv_path}"}))
        raise SystemExit(1)

    rows = load_rows(csv_path)

    # campos esperados (en minúsculas)
    got = []
    for i, row in enumerate(rows, start=1):
        q = (row.get("query") or "").strip()
        docid = (row.get("expected_document_id") or "").strip()
        if docid:
            got.append((i, q, docid))

    n = len(got)

    if args.format == "plain":
        lines = [f"n_docid_gold={n}"]
        for idx, q, docid in got:
            lines.append(f"{idx}\t{docid}\t{q}")
        output = "\n".join(lines)

    elif args.format == "md":
        lines = [f"# Queries con expected_document_id (n={n})", "", "idx | expected_document_id | query", "---:|---:|---"]
        for idx, q, docid in got:
            safe_q = q.replace("|"," ").replace("\n"," ")
            lines.append(f"{idx} | {docid} | {safe_q}")
        output = "\n".join(lines)

    else:  # json
        payload = {
            "ok": True,
            "n_docid_gold": n,
            "items": [{"idx": idx, "query": q, "expected_document_id": docid} for idx, q, docid in got],
        }
        output = json.dumps(payload, ensure_ascii=False, indent=2)

    if args.out:
        out_path = Path(args.out)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(output, encoding="utf-8")
        print(json.dumps({"ok": True, "csv": str(csv_path), "format": args.format, "out": str(out_path), "n_docid_gold": n}, ensure_ascii=False))
    else:
        print(output)

if __name__ == "__main__":
    main()
