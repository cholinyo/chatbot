# scripts/make_queries_template.py
# -*- coding: utf-8 -*-
"""
Genera data/validation/queries.csv con columnas:
  query, expected_chunk_id, expected_chunk_ids,
  expected_document_id, expected_document_title_contains, expected_text_contains

Fuentes:
  (a) SQLite -> Document(s).title
  (b) SQLite -> frases cortas de Chunk(s).text/content (fallback)
  (c) Artefactos de ingesta web: data/processed/runs/**/summary.json (title) (opcional)
"""

import argparse, csv, json, re, sqlite3
from pathlib import Path
from typing import List, Set, Optional

STOP = set("""
de la del los las en el y o u a por para con sin una un al lo sus
sobre entre hasta desde como cuando donde que qué cual cuál cuales cuáles
más menos muy este esta estos estas ese esa esos esas aquel aquella aquellos aquellas
""".split())

TOKEN_RE = re.compile(r"[A-Za-zÁÉÍÓÚÜáéíóúüñÑ][\wÁÉÍÓÚÜáéíóúüñÑ]+", re.UNICODE)

def normalize_ws(s: str) -> str:
    return " ".join((s or "").split())

def get_existing_table(cur: sqlite3.Cursor, candidates: List[str]) -> str:
    cur.execute("SELECT name FROM sqlite_master WHERE type='table'")
    names = {r[0] for r in cur.fetchall()}
    for c in candidates:
        if c in names:
            return c
    return ""

def get_existing_column(cur: sqlite3.Cursor, table: str, candidates: List[str]) -> str:
    cur.execute(f"PRAGMA table_info({table})")
    cols = {r[1] for r in cur.fetchall()}
    for c in candidates:
        if c in cols:
            return c
    return ""

def from_sqlite_titles(db_path: Path, limit: int) -> List[str]:
    out: List[str] = []
    if not db_path or not db_path.exists():
        return out
    con = sqlite3.connect(str(db_path))
    cur = con.cursor()
    try:
        t_docs = get_existing_table(cur, ["documents", "Document"])
        if not t_docs:
            return out
        col_title = get_existing_column(cur, t_docs, ["title", "Title", "name", "Name", "titulo", "Titulo"])
        if not col_title:
            return out
        sql = f"""
            SELECT DISTINCT TRIM({col_title})
            FROM {t_docs}
            WHERE {col_title} IS NOT NULL AND TRIM({col_title}) <> ''
            ORDER BY LENGTH(TRIM({col_title})) DESC
            LIMIT ?;
        """
        cur.execute(sql, (limit,))
        out = [normalize_ws(r[0]) for r in cur.fetchall() if r and r[0]]
    except Exception:
        out = []
    finally:
        con.close()
    return out

def from_summaries(limit: int) -> List[str]:
    root = Path("data/processed/runs")
    if not root.exists():
        return []
    out: List[str] = []
    for summary in root.rglob("summary.json"):
        try:
            data = json.loads(summary.read_text(encoding="utf-8"))
            t = normalize_ws(data.get("title") or "")
            if t:
                out.append(t)
        except Exception:
            continue
        if len(out) >= limit:
            break
    return out[:limit]

def ngram_phrases(text: str, nmin=2, nmax=4) -> List[str]:
    toks = [t for t in TOKEN_RE.findall(text or "")]
    toks = [t for t in toks if t.lower() not in STOP and len(t) > 2]
    phrases: List[str] = []
    for n in range(nmin, nmax+1):
        for i in range(len(toks)-n+1):
            phrases.append(" ".join(toks[i:i+n]))
    return phrases

def from_sqlite_chunks(db_path: Path, limit: int, max_scan: int = 2000) -> List[str]:
    """
    Extrae frases cortas de los primeros max_scan chunks como fallback.
    Usa COALESCE(text, content, '') y detecta tabla chunks/Chunk.
    """
    if not db_path or not db_path.exists():
        return []
    con = sqlite3.connect(str(db_path))
    cur = con.cursor()
    phrases: List[str] = []
    try:
        t_chunks = get_existing_table(cur, ["chunks", "Chunk"])
        if not t_chunks:
            return []
        col_text = get_existing_column(cur, t_chunks, ["text", "content", "Text", "Content"])
        if not col_text:
            sql = f"SELECT COALESCE(text, content, '') FROM {t_chunks} LIMIT ?"
        else:
            sql = f"SELECT COALESCE({col_text}, '') FROM {t_chunks} LIMIT ?"
        cur.execute(sql, (max_scan,))
        for (txt,) in cur.fetchall():
            if not txt:
                continue
            phrases.extend(ngram_phrases(txt, 2, 4))
            if len(phrases) > limit * 20:
                break
    except Exception:
        phrases = []
    finally:
        con.close()
    return phrases

def dedupe(items: List[str]) -> List[str]:
    seen: Set[str] = set()
    out: List[str] = []
    for s in items:
        k = s.lower()
        if k and k not in seen:
            seen.add(k)
            out.append(s)
    return out

def build_queries(db_path: Path, limit: int, use_summaries: bool, min_chunk_scan: int) -> List[str]:
    queries: List[str] = []
    # 1) Títulos de Document(s)
    queries += from_sqlite_titles(db_path, limit*2)
    # 2) Summaries web (opcional)
    if use_summaries and len(queries) < limit:
        queries += from_summaries(limit*2)
    queries = dedupe([normalize_ws(q) for q in queries if q])
    # 3) Fallback con frases de chunks hasta completar
    if len(queries) < limit:
        cand = from_sqlite_chunks(db_path, limit, max_scan=min_chunk_scan)
        cand = dedupe(cand)
        queries += cand
        queries = dedupe(queries)
    # 4) Asegurar un mínimo con un set fijo si aún faltan
    if len(queries) < limit:
        defaults = [
            "empadronamiento",
            "licencia de obra menor",
            "bonificaciones IBI",
            "registro electrónico sede",
            "cita previa urbanismo",
            "plazos de presentación",
            "modelo de instancia general",
            "tasas municipales",
            "subvenciones cultura",
            "atención ciudadana cita presencial",
            "presentación telemática",
        ]
        for q in defaults:
            if q not in queries:
                queries.append(q)
            if len(queries) >= limit:
                break
    return queries[:limit]

def main():
    ap = argparse.ArgumentParser(description="Genera data/validation/queries.csv (plantilla con campos de oro).")
    ap.add_argument("--out", default="data/validation/queries.csv")
    ap.add_argument("--db", default="data/processed/tracking.sqlite", help="Ruta a la BD SQLite.")
    ap.add_argument("--limit", type=int, default=50)
    ap.add_argument("--no-summaries", action="store_true", help="No usar summaries de ingesta web.")
    ap.add_argument("--min-chunk-scan", type=int, default=2000, help="Máx. chunks a escanear para frases fallback.")
    ap.add_argument("--prefill-doc-gold", "--prefill_doc_gold",
                    dest="prefill_doc_gold",
                    choices=["none","title","id"], default="title",
                    help="Prefill de oro: 'title' -> expected_document_title_contains=query; 'id' -> Document(s).id; 'none' -> no rellena.")
    args = ap.parse_args()

    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    db_path = Path(args.db) if args.db else None

    qs = build_queries(db_path, args.limit, use_summaries=(not args.no_summaries), min_chunk_scan=args.min_chunk_scan)

    # (Opcional) prefill de “oro”
    id_by_title = {}
    if args.prefill_doc_gold == "id" and db_path and db_path.exists():
        try:
            con = sqlite3.connect(str(db_path))
            cur = con.cursor()
            t_docs = get_existing_table(cur, ["documents", "Document"])
            col_title = get_existing_column(cur, t_docs, ["title","Title","name","Name","titulo","Titulo"]) if t_docs else ""
            if t_docs and col_title:
                cur.execute(f"SELECT TRIM({col_title}), id FROM {t_docs} WHERE {col_title} IS NOT NULL AND TRIM({col_title}) <> ''")
                for t, i in cur.fetchall():
                    if t:
                        id_by_title[normalize_ws(t)] = i
            con.close()
        except Exception:
            id_by_title = {}

    with out_path.open("w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow([
            "query",
            "expected_chunk_id",
            "expected_chunk_ids",
            "expected_document_id",
            "expected_document_title_contains",
            "expected_text_contains",
        ])
        for q in qs:
            exp_id = ""
            exp_title = ""
            if args.prefill_doc_gold == "title":
                exp_title = q
            elif args.prefill_doc_gold == "id":
                exp_id = id_by_title.get(q, "")
            w.writerow([q, "", "", exp_id, exp_title, ""])

    print(f"OK: generado {out_path} con {len(qs)} queries. "
          f"(prefill={args.prefill_doc_gold}, db={'yes' if db_path and db_path.exists() else 'no'})")

if __name__ == "__main__":
    main()
