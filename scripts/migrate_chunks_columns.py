import sqlite3, sys
from pathlib import Path

db = Path("tracking.sqlite")
if not db.exists():
    print("ERROR: tracking.sqlite no encontrado en la ruta actual", file=sys.stderr)
    sys.exit(1)

con = sqlite3.connect(str(db))
cur = con.cursor()

def has_column(table, col):
    cur.execute(f"PRAGMA table_info('{table}')")
    return any(r[1].lower() == col.lower() for r in cur.fetchall())

changed = False
if not has_column("chunks", "created_at"):
    cur.execute("ALTER TABLE chunks ADD COLUMN created_at TEXT DEFAULT (datetime('now'));")
    print("[OK] Añadida columna chunks.created_at")
    changed = True
else:
    print("[SKIP] chunks.created_at ya existe")

if not has_column("chunks", "updated_at"):
    cur.execute("ALTER TABLE chunks ADD COLUMN updated_at TEXT;")
    print("[OK] Añadida columna chunks.updated_at")
    changed = True
else:
    print("[SKIP] chunks.updated_at ya existe")

con.commit()
con.close()
print("[DONE] Migración chunks completada" + (" (cambios)" if changed else " (sin cambios)"))
