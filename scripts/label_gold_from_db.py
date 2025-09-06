# scripts/label_gold_from_db.py
# -*- coding: utf-8 -*-
"""
Etiqueta 'oro' (expected_document_id y expected_chunk_ids) en un CSV de queries usando SQLite.
- Autodetección robusta de nombres de tablas/columnas:
  * Tablas: Document/Chunk (o documents/document, chunks/chunk)
  * Columnas: Document.title|name|titulo ; Chunk.text|content
- Orden de chunks por documento: intenta usar "index"/"chunk_index"/"position"/"ord"/"order"/"seq"; si no, cae a id ASC.
- Rellena también expected_document_title_contains y expected_text_contains con la query si estaban vacíos.
- NO introduce frameworks nuevos.

Uso:
  python -m scripts.label_gold_from_db ^
    --in  data/validation/queries.csv ^
    --out data/validation/queries.csv ^
    --db  data/processed/tracking.sqlite ^
    --top-chunks 5
"""

import argparse, csv, sqlite3, re, unicodedata, json
from pathlib import Path
from typing import List, Dict, Tuple, Optional

def norm_ws(s: Optional[str]) -> str:
    return " ".join((s or "").split())

def strip_accents(s: str) -> str:
    s = s or ""
    return "".join(ch for ch in unicodedata.normalize("NFD", s) if unicodedata.category(ch) != "Mn")

def to_key(s: Optional[str]) -> str:
    return strip_accents((s or "").casefold())

TOKEN_RE = re.compile(r"\w+", flags=re.UNICODE)
def tokens(s: str) -> List[str]:
    return [t for t in TOKEN_RE.findall(to_key(s)) if len(t) > 1]

def detect_tables_and_columns(conn: sqlite3.Connection) -> Dict[str, Optional[str]]:
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    cur.execute("SELECT name FROM sqlite_master WHERE type='table'")
    names = {r["name"].lower(): r["name"] for r in cur.fetchall()}

    def pick_table(cands: List[str]) -> Optional[str]:
        for c in cands:
            if c.lower() in names:
                return names[c.lower()]
        return None

    t_doc = pick_table(["Document","Documents","document","documents"])
    t_chunk = pick_table(["Chunk","Chunks","chunk","chunks"])

    col_title = col_text = col_order = None

    if t_doc:
        cur.execute(f"PRAGMA table_info({t_doc})")
        cols = {r["name"].lower(): r["name"] for r in cur.fetchall()}
        for c in ["title","name","titulo"]:
            if c in cols:
                col_title = cols[c]
                break

    if t_chunk:
        cur.execute(f"PRAGMA table_info({t_chunk})")
        cols = {r["name"].lower(): r["name"] for r in cur.fetchall()}
        for c in ["text","content"]:
            if c in cols:
                col_text = cols[c]
                break
        for c in ["index","chunk_index","position","ord","order","seq","seq_id"]:
            if c in cols:
                col_order = cols[c]
                break

    return {"t_doc": t_doc, "t_chunk": t_chunk, "col_title": col_title, "col_text": col_text, "col_order": col_order}

def title_score(doc_title: str, pattern: str) -> float:
    if not pattern:
        return 0.0
    t = to_key(doc_title)
    p = to_key(pattern)
    ptoks = tokens(pattern)
    if not ptoks:
        return 0.0
    hit = sum(1 for tok in ptoks if tok in t)
    score = hit / len(ptoks)
    if p in t:
        score += 0.5
    return score

def ensure_headers(row: Dict[str, str]) -> Dict[str, str]:
    fieldnames = [
        "query",
        "expected_chunk_id",
        "expected_chunk_ids",
        "expected_document_id",
        "expected_document_title_contains",
        "expected_text_contains",
    ]
    out = {k: row.get(k, "") for k in fieldnames}
    for k in list(row.keys()):
        lk = k.replace("\ufeff","").strip().lower().replace(" ", "_")
        if lk == "doc_title_contains" and not out["expected_document_title_contains"]:
            out["expected_document_title_contains"] = row[k]
        if lk == "text_contains" and not out["expected_text_contains"]:
            out["expected_text_contains"] = row[k]
    return out

def choose_document_by_title(conn: sqlite3.Connection, pattern: str,
                             t_doc: Optional[str], col_title: Optional[str],
                             min_tokens: int = 1) -> Optional[Tuple[int, str]]:
    if not pattern or not t_doc or not col_title:
        return None
    ptoks = tokens(pattern)
    if len(ptoks) < min_tokens:
        return None
    cur = conn.cursor()
    cur.execute(f"SELECT id, {col_title} AS title FROM {t_doc} WHERE {col_title} IS NOT NULL AND TRIM({col_title}) <> ''")
    best = None
    best_score = 0.0
    for did, title in cur.fetchall():
        sc = title_score(title or "", pattern)
        if sc > best_score:
            best_score = sc
            best = (int(did), title or "")
    if best and best_score >= 0.6:
        return best
    return None

def fallback_document_by_text(conn: sqlite3.Connection, phrase: str,
                              t_chunk: Optional[str], col_text: Optional[str]) -> Optional[Tuple[int,int]]:
    if not phrase or not t_chunk or not col_text:
        return None
    cur = conn.cursor()
    try:
        cur.execute(f"SELECT document_id, id FROM {t_chunk} WHERE {col_text} LIKE ? LIMIT 1", (f"%{phrase}%",))
        row = cur.fetchone()
        if row:
            return (int(row[0]), int(row[1]))
    except sqlite3.Error:
        pass
    return None

def top_chunks_for_document(conn: sqlite3.Connection, doc_id: int, t_chunk: Optional[str],
                            top_n: int = 5, order_col: Optional[str] = None) -> List[int]:
    if not t_chunk:
        return []
    cur = conn.cursor()
    if order_col:
        oc = f"\"{order_col}\"" if order_col.lower() == "index" else order_col
        sql = f"SELECT id FROM {t_chunk} WHERE document_id=? ORDER BY {oc} ASC LIMIT ?"
    else:
        sql = f"SELECT id FROM {t_chunk} WHERE document_id=? ORDER BY id ASC LIMIT ?"
    cur.execute(sql, (doc_id, top_n))
    return [int(r[0]) for r in cur.fetchall()]

def main():
    ap = argparse.ArgumentParser(description="Etiqueta oro (doc_id y chunk_ids) en queries.csv desde SQLite.")
    ap.add_argument("--in", dest="inp", required=True)
    ap.add_argument("--out", dest="out", required=True)
    ap.add_argument("--db", dest="db", required=True)
    ap.add_argument("--top-chunks", dest="top_chunks", type=int, default=5)
    ap.add_argument("--use", choices=["title","text","title,text","text,title"], default="title,text",
                    help="Estrategias en orden: por título y/o por texto.")
    ap.add_argument("--overwrite", action="store_true", help="Sobrescribe campos si ya tenían valor.")
    ap.add_argument("--min-tokens", type=int, default=1, help="Mínimo de tokens para matching por título.")
    args = ap.parse_args()

    inp = Path(args.inp)
    out = Path(args.out)
    dbp = Path(args.db)

    if not dbp.exists():
        raise SystemExit(f"ERROR: no existe la BD en {dbp}")

    rows_in: List[Dict[str,str]] = []
    with inp.open("r", encoding="utf-8-sig", newline="") as f:
        rd = csv.DictReader(f)
        for row in rd:
            rows_in.append(ensure_headers(row))

    conn = sqlite3.connect(str(dbp))
    schema = detect_tables_and_columns(conn)
    t_doc, t_chunk = schema["t_doc"], schema["t_chunk"]
    col_title, col_text, col_order = schema["col_title"], schema["col_text"], schema["col_order"]

    if not t_doc and not t_chunk:
        raise SystemExit("ERROR: no se detectan tablas Document/Chunk (ni variantes). Revisa la BD.")

    updated: List[Dict[str,str]] = []
    stats = {
        "rows": 0, "filled_doc_id": 0, "filled_chunk_ids": 0, "fallback_text": 0,
        "prefilled_contains": 0, "schema": schema
    }

    for row in rows_in:
        stats["rows"] += 1
        q = norm_ws(row["query"])

        # Prefill contains si faltan
        prefilled = False
        if not norm_ws(row.get("expected_document_title_contains")):
            row["expected_document_title_contains"] = q
            prefilled = True
        if not norm_ws(row.get("expected_text_contains")):
            row["expected_text_contains"] = q
            prefilled = True
        if prefilled:
            stats["prefilled_contains"] += 1

        doc_id_existing = norm_ws(row.get("expected_document_id"))
        chunk_ids_existing = norm_ws(row.get("expected_chunk_ids"))

        need_doc = (args.overwrite or not doc_id_existing)
        need_chunks = (args.overwrite or not chunk_ids_existing)

        chosen_doc: Optional[int] = int(doc_id_existing) if doc_id_existing.isdigit() else None

        if (need_doc or need_chunks):
            used = args.use.split(",")
            pattern = norm_ws(row.get("expected_document_title_contains")) or q
            found = False

            for mode in used:
                if mode == "title" and not found and t_doc and col_title:
                    best = choose_document_by_title(conn, pattern, t_doc, col_title, min_tokens=args.min_tokens)
                    if best:
                        chosen_doc = best[0]
                        found = True
                if mode == "text" and not found and t_chunk and col_text:
                    fb = fallback_document_by_text(conn, norm_ws(row.get("expected_text_contains")) or q, t_chunk, col_text)
                    if fb:
                        chosen_doc = fb[0]
                        found = True
                        stats["fallback_text"] += 1

        if chosen_doc is not None and (args.overwrite or not doc_id_existing):
            row["expected_document_id"] = str(chosen_doc)
            stats["filled_doc_id"] += 1

        if chosen_doc is not None and (args.overwrite or not chunk_ids_existing):
            cids = top_chunks_for_document(conn, chosen_doc, t_chunk, args.top_chunks, order_col=col_order)
            if cids:
                row["expected_chunk_ids"] = "|".join(str(c) for c in cids)
                stats["filled_chunk_ids"] += 1

        updated.append(row)

    conn.close()

    out.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = [
        "query",
        "expected_chunk_id",
        "expected_chunk_ids",
        "expected_document_id",
        "expected_document_title_contains",
        "expected_text_contains",
    ]
    with out.open("w", encoding="utf-8", newline="") as f:
        wr = csv.DictWriter(f, fieldnames=fieldnames)
        wr.writeheader()
        for r in updated:
            wr.writerow({k: r.get(k,"") for k in fieldnames})

    print(json.dumps({
        "ok": True,
        "in": str(inp),
        "out": str(out),
        "db": str(dbp),
        "stats": stats
    }, ensure_ascii=False))

if __name__ == "__main__":
    main()
