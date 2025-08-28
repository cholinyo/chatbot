#!/usr/bin/env python
"""
CLI: Ingesta de Documentos (enumerar → extraer → limpiar → split → persistir)

Uso básico:
  python scripts/ingest_documents.py --input-dir "data/raw/documents"

Ejemplos:
  # Carpeta distinta + chunks personalizados
  python scripts/ingest_documents.py --input-dir "D:/vcaruncho/Downloads" \
      --chunk-size 800 --chunk-overlap 120

  # Solo PDF y DOCX, con política mtime
  python scripts/ingest_documents.py --include-ext pdf docx --policy mtime
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import traceback
from datetime import datetime
from pathlib import Path


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Ingesta de Documentos — TFM RAG")
    p.add_argument("--project-root", default=".", help="Ruta del proyecto (donde está la carpeta app/)")
    p.add_argument("--source-id", default="docs_general")
    p.add_argument("--source-name", default="Documentos")
    p.add_argument("--input-dir", default="data/raw/documents")
    p.add_argument("--include-ext", nargs="+", default=["pdf","docx","txt","csv"], help="Extensiones permitidas (sin punto)")
    p.add_argument("--recursive", dest="recursive", action="store_true", default=True)
    p.add_argument("--no-recursive", dest="recursive", action="store_false")
    p.add_argument("--exclude-patterns", nargs="*", default=[], help="Patrones a excluir (glob relativo)")
    p.add_argument("--encoding-default", default="utf-8")
    p.add_argument("--csv-delimiter", default=",")
    p.add_argument("--csv-quotechar", default='"')
    p.add_argument("--csv-header", dest="csv_header", action="store_true", default=True)
    p.add_argument("--no-csv-header", dest="csv_header", action="store_false")
    p.add_argument("--csv-columns", nargs="*", default=None, help="Columnas (por nombre si hay cabecera; pos si no)")
    p.add_argument("--chunk-size", type=int, default=512)
    p.add_argument("--chunk-overlap", type=int, default=64)
    p.add_argument("--policy", choices=["hash","mtime"], default="hash")
    p.add_argument("--verbose-json", action="store_true")
    return p


def main() -> int:
    args = build_parser().parse_args()

    project_root = Path(args.project_root).resolve()
    if not (project_root / "app").exists():
        sys.stderr.write(f"No encuentro la carpeta 'app' bajo {project_root}.\n")
        return 2

    # Asegurar import de 'app'
    if str(project_root) not in sys.path:
        sys.path.insert(0, str(project_root))
    os.chdir(project_root)

    try:
        from app import create_app
        from app.extensions.db import get_session
        from app.models.source import Source
        from app.blueprints.ingestion.services import ingest_documents_by_source_id
    except Exception as e:
        sys.stderr.write(f"ImportError: {e}\n")
        return 2

    app = create_app()

    cfg = {
        "input_dir": args.input_dir,
        "recursive": bool(args.recursive),
        "include_ext": [e.lower().lstrip('.') for e in args.include_ext],
        "exclude_patterns": args.exclude_patterns,
        "encoding_default": args.encoding_default,
        "csv": {
            "delimiter": args.csv_delimiter,
            "quotechar": args.csv_quotechar,
            "header": bool(args.csv_header),
            "columns": args.csv_columns,
        },
        "indexing": {"policy": args.policy},
        "rag": {"chunk_size": int(args.chunk_size), "chunk_overlap": int(args.chunk_overlap)},
    }

    # Garantiza carpeta de entrada
    Path(cfg["input_dir"]).mkdir(parents=True, exist_ok=True)

    # Crear/actualizar fuente y ejecutar ingesta
    try:
        with get_session() as s:
            src = s.get(Source, args.source_id)
            if src is None:
                from app.models.source import Source as _Source
                src = _Source(id=args.source_id, type="document", name=args.source_name, config=cfg, enabled=True)
                s.add(src)
            else:
                src.name = args.source_name
                src.config = cfg

        run = ingest_documents_by_source_id(args.source_id)
        payload = {
            "run_id": run.run_id,
            "source_id": run.source_id,
            "status": run.status,
            "stats": run.stats,
        }
        js = json.dumps(payload, ensure_ascii=False)
        if args.verbose_json:
            print(js)
        else:
            # Resumen legible
            st = run.stats or {}
            print(f"Status: {run.status}")
            print(
                "Stats : scanned={scanned} new={new_docs} updated={updated_docs} "
                "skipped={skipped_unchanged} failed={failed} chunks={total_chunks}".format(**{**{k:0 for k in [
                    'scanned','new_docs','updated_docs','skipped_unchanged','failed','total_chunks']}, **st})
            )
        out_dir = Path("data/processed/documents/runs"); out_dir.mkdir(parents=True, exist_ok=True)
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        (out_dir / f"run_{args.source_id}_{ts}.json").write_text(js, encoding="utf-8")
        return 0
    except SystemExit:
        raise
    except Exception:
        traceback.print_exc()
        return 1


if __name__ == "__main__":
    raise SystemExit(main())