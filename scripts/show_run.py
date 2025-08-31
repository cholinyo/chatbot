# -*- coding: utf-8 -*-
"""
Muestra stdout/stderr capturado en IngestionRun.meta para un run dado,
y si hay run_dir intenta leer stdout.txt de disco.
Uso:
  python scripts/show_run.py --id 123
"""
import argparse, json, os, sqlite3, sys
DB = os.path.join("data","processed","tracking.sqlite")

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=DB)
    ap.add_argument("--id", type=int, required=True, help="ID del IngestionRun")
    args = ap.parse_args()

    con = sqlite3.connect(args.db)
    con.row_factory = sqlite3.Row
    cur = con.cursor()
    cur.execute("SELECT id, source_id, status, meta FROM ingestion_runs WHERE id=?", (args.id,))
    row = cur.fetchone()
    if not row:
        print(f"[ERROR] No existe run_id={args.id}")
        return 2

    print(f"RUN id={row['id']} source_id={row['source_id']} status={row['status']}")
    meta = row["meta"]
    try:
        meta = json.loads(meta) if isinstance(meta, str) else meta
    except Exception as e:
        print(f"[WARN] meta no legible como JSON: {e}")
        meta = {}

    stdout = (meta or {}).get("stdout")
    run_dir = (meta or {}).get("run_dir")
    if stdout:
        print("\n=== STDOUT (DB) ===")
        print(stdout)
    else:
        print("\n(DB) No hay stdout en meta.")

    if run_dir:
        p = os.path.join(run_dir, "stdout.txt")
        if os.path.exists(p):
            print("\n=== stdout.txt (archivo) ===")
            try:
                with open(p, "r", encoding="utf-8", errors="ignore") as fh:
                    print(fh.read())
            except Exception as e:
                print(f"[WARN] No se pudo leer {p}: {e}")
        else:
            print(f"\nNo existe archivo {p}")
    else:
        print("\nNo hay run_dir en meta.")

if __name__ == "__main__":
    sys.exit(main())
