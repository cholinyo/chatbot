# scripts/fill_queries_contains.py
# -*- coding: utf-8 -*-
"""
Rellena expected_document_title_contains y expected_text_contains con la query
si están vacíos. Útil para datasets iniciales.

Uso:
  python scripts/fill_queries_contains.py --in data/validation/queries.csv --out data/validation/queries.csv
"""
import argparse, csv
from pathlib import Path

def norm(s):
    return " ".join((s or "").split())

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--in", dest="inp", required=True)
    ap.add_argument("--out", dest="out", required=True)
    args = ap.parse_args()

    inp = Path(args.inp)
    out = Path(args.out)

    fieldnames = [
        "query",
        "expected_chunk_id",
        "expected_chunk_ids",
        "expected_document_id",
        "expected_document_title_contains",
        "expected_text_contains",
    ]

    rows = []
    with inp.open("r", encoding="utf-8-sig", newline="") as f:
        rd = csv.DictReader(f)
        for row in rd:
            q = norm(row.get("query") or "")
            if not q:
                continue
            for k in fieldnames:
                row.setdefault(k, "")
            if not norm(row.get("expected_document_title_contains")):
                row["expected_document_title_contains"] = q
            if not norm(row.get("expected_text_contains")):
                row["expected_text_contains"] = q
            rows.append({k: row.get(k, "") for k in fieldnames})

    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w", encoding="utf-8", newline="") as f:
        wr = csv.DictWriter(f, fieldnames=fieldnames)
        wr.writeheader()
        wr.writerows(rows)

if __name__ == "__main__":
    main()
