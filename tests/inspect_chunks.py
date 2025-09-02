# scripts/inspect_chunks.py
# Uso: python .\tests\inspect_chunks.py .\data\processed\tracking.sqlite  
import sqlite3, sys
db = sys.argv[1] if len(sys.argv) > 1 else "tracking.sqlite"
con = sqlite3.connect(db)
con.row_factory = sqlite3.Row

print("Total chunks:", con.execute("SELECT COUNT(*) FROM chunks").fetchone()[0])

print("\nChunks por documento (últimos 20):")
for r in con.execute("""
  SELECT d.id AS doc_id, d.title, COUNT(c.id) AS n_chunks
  FROM documents d LEFT JOIN chunks c ON c.document_id = d.id
  GROUP BY d.id ORDER BY d.id DESC LIMIT 20
"""):
    print(f"- doc {r['doc_id']:>6} | chunks={r['n_chunks']:>4} | {r['title'] or ''}")

print("\nEjemplo de chunks (5 filas):")
for r in con.execute("""
  SELECT id, document_id, substr(COALESCE(text, content),1,200) AS snippet
  FROM chunks ORDER BY id DESC LIMIT 5
"""):
    print(f"- chunk {r['id']:>6} | doc {r['document_id']:>6} | {r['snippet']!r}")
