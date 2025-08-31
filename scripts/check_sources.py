# -*- coding: utf-8 -*-
"""
Listado y comprobación rápida de fuentes (tabla 'sources') y, opcionalmente, runs.
Evita depender de la CLI sqlite3. Funciona con Python estándar.

Uso básico:
  python scripts/check_sources.py
  python scripts/check_sources.py --type web --limit 50
  python scripts/check_sources.py --id 42 --show-config
  python scripts/check_sources.py --runs --limit 20
  python scripts/check_sources.py --db data/processed/tracking.sqlite

Salidas:
- Tabla con id, type, name, url
- --show-config imprime el JSON completo de config para cada fila
- --runs muestra también IngestionRun recientes (id, source_id, status)
"""
from __future__ import annotations

import argparse
import json
import os
import sqlite3
from typing import Any, Iterable, List, Tuple

DEFAULT_DB = os.path.join("data", "processed", "tracking.sqlite")


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Listar fuentes y runs desde tracking.sqlite")
    p.add_argument("--db", default=DEFAULT_DB, help=f"Ruta a la BD (por defecto: {DEFAULT_DB})")
    p.add_argument("--type", choices=["web", "docs"], help="Filtrar por tipo de fuente")
    p.add_argument("--id", type=int, help="Mostrar solo la fuente con este ID")
    p.add_argument("--limit", type=int, default=10, help="Límite de filas a mostrar (por defecto: 10)")
    p.add_argument("--show-config", action="store_true", help="Imprimir JSON de config por fila")
    p.add_argument("--runs", action="store_true", help="Mostrar también runs recientes")
    return p.parse_args()


def connect(db_path: str) -> sqlite3.Connection:
    if not os.path.exists(db_path):
        raise FileNotFoundError(f"No existe la BD: {db_path}")
    con = sqlite3.connect(db_path)
    # Que devuelva dict-like:
    con.row_factory = sqlite3.Row
    return con


def fetch_sources(con: sqlite3.Connection, *, src_id: int | None, src_type: str | None, limit: int) -> List[sqlite3.Row]:
    sql = "SELECT id, type, url, name, config FROM sources"
    where = []
    params: List[Any] = []
    if src_id is not None:
        where.append("id = ?")
        params.append(src_id)
    if src_type is not None:
        where.append("type = ?")
        params.append(src_type)
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY id DESC"
    if limit:
        sql += " LIMIT ?"
        params.append(limit)
    cur = con.cursor()
    cur.execute(sql, params)
    return cur.fetchall()


def fetch_runs(con: sqlite3.Connection, *, limit: int) -> List[sqlite3.Row]:
    sql = "SELECT id, source_id, status, meta FROM ingestion_runs ORDER BY id DESC LIMIT ?"
    cur = con.cursor()
    cur.execute(sql, (limit,))
    return cur.fetchall()


def _short(s: str | None, maxlen: int = 80) -> str:
    if not s:
        return "-"
    if len(s) <= maxlen:
        return s
    return s[: maxlen - 1] + "…"


def print_table(rows: Iterable[Tuple[str, ...]], headers: Tuple[str, ...]) -> None:
    cols = len(headers)
    widths = [len(h) for h in headers]
    cache: List[Tuple[str, ...]] = []
    for r in rows:
        r = tuple("" if v is None else str(v) for v in r)
        cache.append(r)
        for i in range(cols):
            widths[i] = max(widths[i], len(r[i]))
    # Header
    line = "  ".join(h.ljust(widths[i]) for i, h in enumerate(headers))
    sep = "  ".join("-" * widths[i] for i in range(cols))
    print(line)
    print(sep)
    for r in cache:
        print("  ".join(r[i].ljust(widths[i]) for i in range(cols)))


def main() -> int:
    args = parse_args()
    try:
        con = connect(args.db)
    except Exception as e:
        print(f"[ERROR] {e}")
        return 2

    # ---- Fuentes ----
    try:
        sources = fetch_sources(con, src_id=args.id, src_type=args.type, limit=args.limit)
    except sqlite3.OperationalError as oe:
        print(f"[ERROR] Consulta 'sources' falló: {oe}")
        print("¿Seguro que la BD y el esquema son correctos? Ruta:", args.db)
        return 3

    if not sources:
        print("(No hay fuentes que coincidan con el filtro)")
    else:
        rows = []
        for r in sources:
            rows.append(
                (
                    str(r["id"]),
                    r["type"],
                    _short(r["name"], 36),
                    _short(r["url"], 80),
                )
            )
        print("=== SOURCES ===")
        print_table(rows, headers=("id", "type", "name", "url"))
        if args.show_config:
            print("\n-- config JSON --")
            for r in sources:
                print(f"\n[id={r['id']}]")
                try:
                    cfg = r["config"]
                    if isinstance(cfg, (bytes, bytearray)):
                        cfg = cfg.decode("utf-8", errors="ignore")
                    if isinstance(cfg, str):
                        cfg_json = json.loads(cfg)
                    else:
                        # Algunos ORMs guardan ya como dict en SQLite
                        cfg_json = cfg
                    print(json.dumps(cfg_json, ensure_ascii=False, indent=2))
                except Exception as e:
                    print(f"(config no legible) {e}")

    # ---- Runs (opcional) ----
    if args.runs:
        try:
            runs = fetch_runs(con, limit=args.limit)
            print("\n=== RUNS ===")
            rows = []
            for r in runs:
                # meta puede ser un JSON; no lo expandimos para mantener legible
                rows.append((str(r["id"]), str(r["source_id"]), r["status"]))
            print_table(rows, headers=("id", "source_id", "status"))
        except sqlite3.OperationalError as oe:
            print(f"[WARN] No se ha podido leer 'ingestion_runs': {oe}")

    con.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
