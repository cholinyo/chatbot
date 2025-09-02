#!/usr/bin/env python
# verify_ingestion_sqlite.py
# Quick checks for documents/chunks saved by scripts/ingest_web.py
#
# Usage examples (run from repo root):
#   python verify_ingestion_sqlite.py --db data/processed/tracking.sqlite --run-id 20250902
#   python verify_ingestion_sqlite.py --db data/processed/tracking.sqlite --run-id 20250902 --source-id 103 --limit 10

import argparse, json, sqlite3, sys, re
from pathlib import Path
from datetime import datetime

def human(n):
    try:
        return f"{n:,}".replace(",", ".")
    except Exception:
        return str(n)

def run_queries(db_path: str, run_id: int, source_id: int|None, limit: int):
    con = sqlite3.connect(db_path)
    con.row_factory = sqlite3.Row
    cur = con.cursor()

    # Detect table names
    cur.execute("SELECT name FROM sqlite_master WHERE type='table'")
    tables = {r["name"].lower() for r in cur.fetchall()}
    if "documents" not in tables or "chunks" not in tables:
        print("[ERROR] No encuentro tablas 'documents' y 'chunks' en la BD. Tablas presentes:", tables)
        sys.exit(2)

    like_run = f'%\"run_id\": {run_id}%'
    where_docs = "WHERE meta LIKE ?"
    params_docs = [like_run]
    if source_id is not None:
        where_docs += " AND source_id = ?"
        params_docs.append(source_id)

    # 1) Totales por run (y fuente opcional)
    cur.execute(f"SELECT COUNT(*) AS n_docs, SUM(size) AS bytes FROM documents {where_docs}", params_docs)
    row = cur.fetchone()
    n_docs = row["n_docs"] or 0
    total_bytes = row["bytes"] or 0

    # 2) Chunks totales asociados a esos documentos
    cur.execute(f"""
        SELECT COUNT(c.id) AS n_chunks
        FROM chunks c
        JOIN documents d ON d.id = c.document_id
        {where_docs.replace('WHERE','WHERE d.')}
    """, params_docs)
    r2 = cur.fetchone()
    n_chunks = r2["n_chunks"] or 0

    print("=== Totales en BD ===")
    print("Run ID:            ", run_id)
    print("Source ID:         ", source_id if source_id is not None else "(todas)")
    print("Documentos:        ", human(n_docs))
    print("Chunks:            ", human(n_chunks))
    print("Bytes (documents): ", human(total_bytes))
    print()

    # 3) Top documentos por nº de chunks
    print(f"=== Top documentos por nº chunks (top {limit}) ===")
    cur.execute(f"""
        SELECT d.id AS document_id, d.path, d.title, d.source_id,
               COUNT(c.id) AS chunks, SUM(LENGTH(c.text)) AS text_len
        FROM documents d
        LEFT JOIN chunks c ON c.document_id = d.id
        {where_docs.replace('WHERE','WHERE d.')}
        GROUP BY d.id
        ORDER BY chunks DESC, d.id ASC
        LIMIT {limit}
    """, params_docs)
    rows = cur.fetchall()
    for r in rows:
        print(f"- doc_id={r['document_id']:<6} src={r['source_id']:<4} chunks={r['chunks']:<5} text_len={human(r['text_len'] or 0):<8} url={r['path'][:120]}")
    print()

    # 4) Sanidad de chunks: text NULL, longitud 0, etc.
    print("=== Chequeos de sanidad de chunks ===")
    cur.execute("SELECT COUNT(*) AS n FROM chunks WHERE text IS NULL")
    print("chunks con text IS NULL: ", cur.fetchone()["n"])
    cur.execute("SELECT COUNT(*) AS n FROM chunks WHERE LENGTH(COALESCE(text,''))=0")
    print("chunks con text vacío:   ", cur.fetchone()["n"])
    # Presencia de la columna "index" (ordinal)
    has_index = False
    cur.execute("PRAGMA table_info(chunks)")
    for c in cur.fetchall():
        if c["name"].lower() == "index":
            has_index = True
            break
    print('columna "index" existe:  ', has_index)
    print()

    # 5) Muestra de 3 chunks del documento con más chunks
    if rows:
        top_doc_id = rows[0]["document_id"]
        print(f"=== Muestra de 3 chunks de doc_id={top_doc_id} ===")
        cur.execute("""
            SELECT document_id, "index" AS ordinal, SUBSTR(text,1,200) AS snippet
            FROM chunks
            WHERE document_id = ?
            ORDER BY "index" ASC
            LIMIT 3
        """, (top_doc_id,))
        for r in cur.fetchall():
            print(f"[{r['ordinal']}] {r['snippet'].replace('\\n',' ')}")
        print()

    # 6) Comparativa con summary.json (si existe)
    summary_path = Path(f"data/processed/runs/web/run_{run_id}/summary.json")
    if summary_path.exists():
        try:
            js = json.loads(summary_path.read_text(encoding="utf-8"))
            st = js.get("totals", {})
            print("=== Comparativa con summary.json ===")
            print("summary.json pages:  ", human(st.get("pages", 0)))
            print("summary.json chunks: ", human(st.get("chunks", 0)))
            print("summary.json bytes:  ", human(st.get("bytes", 0)))
            if st.get("pages", 0) != n_docs or st.get("chunks", 0) != n_chunks:
                print("AVISO: Totales de summary.json no cuadran con la BD (puede haber runs previos mezclados); revisa --source-id y run_id.")
        except Exception as e:
            print("No se pudo leer summary.json:", e)
    else:
        print(f"(No encontré {summary_path})")

    con.close()

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", required=True, help="Ruta a tracking.sqlite, ej. data/processed/tracking.sqlite")
    ap.add_argument("--run-id", required=True, type=int)
    ap.add_argument("--source-id", type=int, default=None)
    ap.add_argument("--limit", type=int, default=15)
    args = ap.parse_args()
    run_queries(args.db, args.run_id, args.source_id, args.limit)

if __name__ == "__main__":
    main()
