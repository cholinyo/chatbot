# scripts/migrate_sqlite_add_columns.py  (versión segura)
import argparse, sqlite3, sys, shutil
from pathlib import Path

def find_db_from_flask():
    try:
        from app import create_app
        app = create_app()
        uri = app.config.get("SQLALCHEMY_DATABASE_URI", "")
        if not uri or not uri.startswith("sqlite"):
            return None
        if uri.startswith("sqlite:////"):
            return Path(uri.replace("sqlite:////", "/", 1))
        return Path(uri.replace("sqlite:///", "", 1))
    except Exception:
        return None

def has_column(cur, table, col):
    cur.execute(f"PRAGMA table_info('{table}')")
    return any(r[1].lower() == col.lower() for r in cur.fetchall())

def main():
    p = argparse.ArgumentParser()
    p.add_argument("--db", help="Ruta al fichero .sqlite (opcional). Si no se pasa, se intenta leer de Flask.")
    args = p.parse_args()

    db_path = Path(args.db) if args.db else find_db_from_flask()
    if not db_path:
        print("ERROR: No se pudo determinar la ruta de la BD. Pasa --db o configura SQLALCHEMY_DATABASE_URI.", file=sys.stderr)
        sys.exit(1)
    if not db_path.exists():
        print(f"ERROR: BD no encontrada en: {db_path}", file=sys.stderr)
        sys.exit(1)

    print(f"[INFO] Usando BD: {db_path}")
    con = sqlite3.connect(str(db_path))
    cur = con.cursor()

    changed = False

    # created_at: sin DEFAULT, luego backfill
    if not has_column(cur, "chunks", "created_at"):
        cur.execute("ALTER TABLE chunks ADD COLUMN created_at TEXT;")
        cur.execute("UPDATE chunks SET created_at = CURRENT_TIMESTAMP WHERE created_at IS NULL;")
        print("[OK] Añadida y rellenada columna chunks.created_at")
        changed = True
    else:
        print("[SKIP] chunks.created_at ya existe")

    # updated_at: opcional, sin DEFAULT (lo gestionará la app en inserts/updates)
    if not has_column(cur, "chunks", "updated_at"):
        cur.execute("ALTER TABLE chunks ADD COLUMN updated_at TEXT;")
        print("[OK] Añadida columna chunks.updated_at")
        changed = True
    else:
        print("[SKIP] chunks.updated_at ya existe")

    con.commit()
    con.close()
    print("[DONE] Migración completada" + (" (con cambios)" if changed else " (sin cambios)"))

if __name__ == "__main__":
    main()
