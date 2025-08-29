#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
Migración: normalizar a atributo 'meta' (evitar nombre reservado 'metadata').

- Backup del SQLite.
- Re-crea tablas documents/chunks si detecta columnas 'metadata' o faltan las nuevas.
- Copia datos y renombra columnas a 'meta'.

Uso:
  python scripts/migrate_20250829b_meta_fix.py
"""
from __future__ import annotations
import shutil, sqlite3
from pathlib import Path

DB_PATH = Path("data/processed/tracking.sqlite").resolve()
BACKUP_PATH = DB_PATH.with_suffix(".bak_20250829b")

def table_cols(cur, table: str) -> set[str]:
    cur.execute(f"PRAGMA table_info({table});")
    return {row[1] for row in cur.fetchall()}

def main() -> int:
    if not DB_PATH.exists():
        print(f"[ERR] No existe la BD en {DB_PATH}")
        return 2

    if not BACKUP_PATH.exists():
        shutil.copy2(DB_PATH, BACKUP_PATH)
        print(f"[OK ] Backup creado: {BACKUP_PATH}")
    else:
        print(f"[OK ] Backup ya existente: {BACKUP_PATH}")

    con = sqlite3.connect(str(DB_PATH))
    con.execute("PRAGMA foreign_keys=OFF;")
    try:
        cur = con.cursor()

        # ---- DOCUMENTS ----
        docs_cols = table_cols(cur, "documents")
        docs_target = {"id","source_id","path","title","ext","size","mtime_ns","hash","meta","created_at"}
        if not docs_target.issubset(docs_cols):
            print("[.. ] Migrando documents -> documents_new (meta)")
            cur.executescript("""
                CREATE TABLE IF NOT EXISTS documents_new (
                  id INTEGER PRIMARY KEY AUTOINCREMENT,
                  source_id INTEGER NOT NULL,
                  path VARCHAR(1024),
                  title VARCHAR(255),
                  ext VARCHAR(20),
                  size INTEGER,
                  mtime_ns INTEGER,
                  hash VARCHAR(64),
                  meta JSON NOT NULL DEFAULT '{}',
                  created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                  FOREIGN KEY(source_id) REFERENCES sources(id) ON DELETE CASCADE
                );
            """)
            # Mapear posibles orígenes
            has_metadata = "metadata" in docs_cols
            meta_expr = "metadata" if has_metadata else ("meta" if "meta" in docs_cols else "'{}'")
            path_expr = "path"
            title_expr = "title" if "title" in docs_cols else ("filename" if "filename" in docs_cols else "NULL")
            size_expr = "size" if "size" in docs_cols else "NULL"
            mtime_expr = "mtime_ns" if "mtime_ns" in docs_cols else "NULL"
            hash_expr = "hash" if "hash" in docs_cols else "NULL"
            created_expr = "created_at" if "created_at" in docs_cols else "CURRENT_TIMESTAMP"

            cur.execute(f"""
                INSERT INTO documents_new (id, source_id, path, title, ext, size, mtime_ns, hash, meta, created_at)
                SELECT id, source_id, {path_expr}, {title_expr}, NULL, {size_expr}, {mtime_expr}, {hash_expr}, COALESCE({meta_expr}, '{{}}'), {created_expr}
                FROM documents;
            """)
            cur.executescript("""
                DROP TABLE documents;
                ALTER TABLE documents_new RENAME TO documents;
            """)
            print("[OK ] documents migrada")

        # ---- CHUNKS ----
        chunks_cols = table_cols(cur, "chunks")
        chunks_target = {"id","source_id","document_id","index","text","content","meta"}
        if not chunks_target.issubset(chunks_cols):
            print("[.. ] Migrando chunks -> chunks_new (meta)")
            cur.executescript("""
                CREATE TABLE IF NOT EXISTS chunks_new (
                  id INTEGER PRIMARY KEY AUTOINCREMENT,
                  source_id INTEGER NOT NULL,
                  document_id INTEGER,
                  "index" INTEGER,
                  text TEXT NOT NULL,
                  content TEXT NOT NULL,
                  meta JSON NOT NULL DEFAULT '{}',
                  FOREIGN KEY(source_id) REFERENCES sources(id) ON DELETE CASCADE,
                  FOREIGN KEY(document_id) REFERENCES documents(id) ON DELETE CASCADE
                );
            """)
            has_metadata = "metadata" in chunks_cols
            meta_expr = "metadata" if has_metadata else ("meta" if "meta" in chunks_cols else "'{}'")
            index_expr = '"index"' if "index" in chunks_cols else "NULL"
            text_expr = "text" if "text" in chunks_cols else "content"
            cur.execute(f"""
                INSERT INTO chunks_new (id, source_id, document_id, "index", text, content, meta)
                SELECT id, source_id, document_id, {index_expr}, {text_expr}, content, COALESCE({meta_expr}, '{{}}')
                FROM chunks;
            """)
            cur.executescript("""
                DROP TABLE chunks;
                ALTER TABLE chunks_new RENAME TO chunks;
            """)
            print("[OK ] chunks migrada")

        con.commit()
        print("[DONE] Migración completada")
        return 0
    except Exception as e:
        con.rollback()
        print(f"[ERR] Migración fallida: {e}")
        return 1
    finally:
        con.execute("PRAGMA foreign_keys=ON;")
        con.close()

if __name__ == "__main__":
    raise SystemExit(main())
