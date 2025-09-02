# scripts/sqlite_migrator.py
# -*- coding: utf-8 -*-
"""
Añade columnas que falten en 'documents' y 'chunks' y crea índices útiles.
NO requiere SQLAlchemy ni importar 'app'.
Uso:
  python scripts/sqlite_migrator.py --db "RUTA\A\tracking.sqlite" --apply
  # o vista previa:
  python scripts/sqlite_migrator.py --db "RUTA\A\tracking.sqlite" --dry-run
"""
import sqlite3, argparse, os
from pathlib import Path

DOCS_COLUMNS = [
    ("title", "TEXT"),
    ("ext", "TEXT"),
    ("size", "INTEGER"),
    ("mtime_ns", "INTEGER"),
    ("hash", "TEXT"),
    ("meta", "TEXT"),
    ("created_at", "DATETIME"),
]
# Ajusta estos nombres a tu modelo real
CHUNKS_COLUMNS = [
    ("source_id", "INTEGER"),
    ("document_id", "INTEGER"),
    ("index", "INTEGER"),      # si tu modelo usa 'ordinal', cambia aquí y en la app
    ("text", "TEXT"),          # si tu NOT NULL es 'content', cámbialo
    ("content", "TEXT"),
    ("meta", "TEXT"),
    ("created_at", "DATETIME"),
]

INDEXES = [
    ("idx_documents_source", "documents", "source_id"),
    ("idx_chunks_document", "chunks", "document_id"),
    ("idx_chunks_source", "chunks", "source_id"),
    ("idx_runs_source_id", "ingestion_runs", "source_id, id"),
]

def table_columns(con, table):
    cur = con.execute(f"PRAGMA table_info({table})")
    return [r[1] for r in cur.fetchall()]

def ensure_columns(con, table, needed_cols, dry):
    try:
        existing = set(table_columns(con, table))
    except sqlite3.OperationalError as e:
        print(f"(WARN) La tabla '{table}' no existe aún: {e}")
        return
    for col, coltype in needed_cols:
        if col not in existing:
            # Escapar el nombre de la columna con backticks
            sql = f"ALTER TABLE {table} ADD COLUMN `{col}` {coltype}"
            if dry:
                print("DRY-RUN:", sql)
            else:
                con.execute(sql)
                print("ADD COLUMN:", table, col, coltype)

def ensure_index(con, index_name, table, columns, dry):
    cur = con.execute(
        "SELECT name FROM sqlite_master WHERE type='index' AND name=?",
        (index_name,),
    )
    if cur.fetchone():
        return
    sql = f"CREATE INDEX {index_name} ON {table} ({columns})"
    if dry:
        print("DRY-RUN:", sql)
    else:
        con.execute(sql)
        print("CREATE INDEX:", index_name)

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", required=True, help="Ruta a tracking.sqlite")
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--apply", action="store_true", help="Aplica cambios")
    g.add_argument("--dry-run", action="store_true", help="Muestra cambios sin aplicar")
    args = ap.parse_args()

    db_path = Path(args.db)
    if not db_path.exists():
        raise SystemExit(f"ERROR: No existe {db_path}")

    con = sqlite3.connect(str(db_path))
    try:
        print("BD:", db_path)
        for t in ("sources", "ingestion_runs", "documents", "chunks"):
            try:
                print(f"- {t} cols:", table_columns(con, t))
            except sqlite3.OperationalError as e:
                print(f"- {t}: (no existe) {e}")

        ensure_columns(con, "documents", DOCS_COLUMNS, args.dry_run)
        ensure_columns(con, "chunks", CHUNKS_COLUMNS, args.dry_run)

        for idx_name, tbl, cols in INDEXES:
            try:
                ensure_index(con, idx_name, tbl, cols, args.dry_run)
            except sqlite3.OperationalError as e:
                print(f"(skip index {idx_name}): {e}")

        if args.dry_run:
            print("DRY-RUN: sin cambios.")
        else:
            con.commit()
            print("OK: migración aplicada.")
    finally:
        con.close()

if __name__ == "__main__":
    main()
