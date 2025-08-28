# scripts/ingest_web.py
# -----------------------------------------------------------------------------
# CLI de ingesta Web para el modelo RAG (persistencia vía SQLAlchemy).
# - Estrategias:
#     * requests_bs4  -> BFS clásico desde seeds, con filtros y robots.
#     * sitemap       -> Descubre URLs desde sitemap(s) y descarga cada página.
# - Persistencia: Document, Chunk, IngestionRun.
# - Chunking simple por palabras (size/overlap).
# -----------------------------------------------------------------------------

from __future__ import annotations

# --- bootstrap sys.path (si aún no lo tienes) ---
import sys, os
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import argparse
import json
import logging
import re
import sys
import uuid
from datetime import datetime, UTC
from typing import List

from app.extensions.db import create_all, get_session
import app.models  
# fuerza a importar el modelo Source para que el registry lo conozca
from app.models.source import Source  # noqa: F401
from app.models.document import Document
from app.models.chunk import Chunk
from app.models.ingestion_run import IngestionRun
from app.rag.scrapers.requests_bs4 import RequestsBS4Scraper, ScrapeConfig
from app.rag.scrapers.web_normalizer import html_to_text, NormalizeConfig
from app.rag.scrapers.sitemap import (
    discover_sitemaps,
    discover_urls_from_sitemaps,
)

# Modelos ORM reales
from app.models.document import Document
from app.models.chunk import Chunk
from app.models.ingestion_run import IngestionRun

logger = logging.getLogger("ingestion.web.cli")


# ------------------------------ utilidades ------------------------------

def chunk_text(text: str, size: int = 700, overlap: int = 100) -> List[str]:
    """
    Particiona texto en ventanas superpuestas (simple, determinista) por PALABRAS.
    - size: nº máximo de palabras por chunk
    - overlap: solapamiento de palabras entre chunks consecutivos
    """
    if size <= 0:
        return [text]
    tokens = text.split()
    chunks: List[str] = []
    i = 0
    step = max(1, size - overlap)
    while i < len(tokens):
        window = tokens[i : i + size]
        chunks.append(" ".join(window))
        if i + size >= len(tokens):
            break
        i += step
    return chunks


def _compile_patterns_glob_or_regex(patterns: List[str]) -> List[re.Pattern]:
    """
    Compila patrones tipo 'glob-like' (con '*') o regex ya formadas.
    Usado en la rama 'sitemap' para filtrar URLs descubiertas.
    """
    out: List[re.Pattern] = []
    for p in patterns or []:
        if "*" in p and not p.startswith(".*"):
            p = re.escape(p).replace(r"\*", ".*")
        out.append(re.compile(p))
    return out


# --------------------------------- CLI ----------------------------------

def parse_args():
    p = argparse.ArgumentParser(description="Ingesta de fuentes web en modelo RAG (SQLAlchemy)")
    p.add_argument("--source-id", required=True, help="Identificador lógico de la fuente (ej. web_onda)")
    p.add_argument("--strategy", default="requests_bs4",
                   choices=["requests_bs4", "sitemap"],
                   help="Estrategia de scraping (por defecto: requests_bs4)")
    p.add_argument("--seed", action="append", required=True, help="URL semilla (repetible)")
    p.add_argument("--depth", type=int, default=1, help="Profundidad BFS (solo requests_bs4)")
    p.add_argument("--allowed-domains", default="", help="Lista separada por comas")
    p.add_argument("--include", action="append", default=[], help="Patrón include (repetible)")
    p.add_argument("--exclude", action="append", default=[], help="Patrón exclude (repetible)")
    p.add_argument("--max-pages", type=int, default=200, help="Límite de páginas totales a procesar")
    p.add_argument("--timeout", type=int, default=15, help="Timeout HTTP (seg)")
    p.add_argument("--rate", type=float, default=1.0, help="Rate limit por host (req/seg)")
    p.add_argument("--no-robots", action="store_true", help="Ignorar robots.txt (para pruebas/MVP)")
    p.add_argument("--verbose", action="store_true", help="Logs INFO")
    # chunking
    p.add_argument("--chunk-size", type=int, default=700)
    p.add_argument("--chunk-overlap", type=int, default=100)
    return p.parse_args()


def main():
    args = parse_args()
    logging.basicConfig(
        level=logging.INFO if args.verbose else logging.WARNING,
        format="%(asctime)s %(levelname)s %(name)s :: %(message)s",
    )

    # Garantiza que las tablas existen (según tus modelos)
    create_all()

    allowed = {d.strip().lower() for d in args.allowed_domains.split(",") if d.strip()} or None

    # Config común para el scraper (sesión, filtros, robots, etc.)
    cfg = ScrapeConfig(
        seeds=args.seed,
        depth=args.depth,
        allowed_domains=allowed,
        include_url_patterns=args.include or None,
        exclude_url_patterns=args.exclude or None,
        respect_robots=not args.no_robots,
        timeout_seconds=args.timeout,
        rate_limit_per_host=args.rate,
        max_pages=args.max_pages,
    )

    # Crea el IngestionRun inicial
    run_id = uuid.uuid4().hex
    stats = {
        "queued": 0,
        "fetched_ok": 0,
        "fetched_fail": 0,
        "parsed_ok": 0,
        "skipped": {"robots": 0, "patterns": 0, "non_html": 0, "empty_text": 0},
        "total_chunks": 0,
    }

    with get_session() as session:
        run = IngestionRun(
            run_id=run_id,  # si tu modelo usa otro nombre de PK, ajusta aquí y la lectura posterior
            source_id=args.source_id,
            source_type="web", 
            source_scope=",".join(args.seed),
            started_at=datetime.now(UTC),
            params={
                "depth": args.depth,
                "strategy": args.strategy,
                "max_pages": args.max_pages,
                "chunk_size": args.chunk_size,
                "chunk_overlap": args.chunk_overlap,
                "include": args.include or [],
                "exclude": args.exclude or [],
                "allowed_domains": list(allowed) if allowed else [],
            },
            stats=stats,
        )
        session.add(run)
        session.commit()

    # Instancia del scraper (comparte sesión/headers/UA para ambas estrategias)
    scraper = RequestsBS4Scraper(cfg)

    # ------------------------- Selección de estrategia -------------------------
    if args.strategy == "requests_bs4":
        # BFS clásico a partir de seeds (profundidad limitada).
        pages_iter = scraper.crawl()

    elif args.strategy == "sitemap":
        # 1) Patrones include/exclude compilados (glob/regex) para FILTRAR las URLs del sitemap.
        include_re = _compile_patterns_glob_or_regex(args.include or [])
        exclude_re = _compile_patterns_glob_or_regex(args.exclude or [])

        # 2) Descubrir los sitemaps (robots.txt -> líneas "Sitemap:", fallback a /sitemap.xml)
        smaps = discover_sitemaps(scraper._session, args.seed)
        logger.info("sitemap.discovered: %d", len(smaps))

        # 3) Expandir los sitemaps a URLs finales (filtradas por dominio y patrones)
        urls = discover_urls_from_sitemaps(
            scraper._session,
            smaps,
            allowed_domains=allowed,
            include_patterns=include_re or None,
            exclude_patterns=exclude_re or None,
            limit=args.max_pages,  # protección por si hay miles de URLs
        )
        logger.info("sitemap.urls: %d", len(urls))

        # 4) Generador que descarga cada URL (aplica robots y filtros internos del scraper)
        def _pages():
            count = 0
            for u in urls:
                if count >= args.max_pages:
                    break
                page = scraper.fetch_url(u)  # usa el propio motor del scraper
                if page:
                    count += 1
                    yield page

        pages_iter = _pages()

    else:
        raise ValueError(f"Estrategia no soportada: {args.strategy}")

    # --------------------------- Bucle principal ---------------------------
    processed = 0
    for page in pages_iter:
        processed += 1
        stats["fetched_ok"] += 1

        try:
            # Normalización HTML -> texto
            text = html_to_text(page.html, NormalizeConfig())
            if not text.strip():
                stats["skipped"]["empty_text"] += 1
                logger.info("parse.skip.empty: %s", page.url)
                continue

            # Persistimos Document + Chunks
            with get_session() as session:
                doc_id = uuid.uuid4().hex
                doc = Document(
                    doc_id=doc_id,
                    source_id=args.source_id,
                    source_type="web",
                    uri=page.url,
                    title=None,                 # si extraes <title> en el normalizador, puedes guardarlo aquí
                    lang=None,
                    mime="text/html",
                    version=None,
                    collected_at=datetime.now(UTC),
                    size_bytes=len(page.html.encode("utf-8")) if page.html else None,
                    origin_hash=page.origin_hash,
                    normalized_hash=None,       # si añades un hash del texto normalizado, guárdalo aquí
                    license=None,
                    confidentiality=None,
                )
                session.add(doc)
                session.flush()  # asegura doc_id visible (aunque lo generamos a mano)

                parts = chunk_text(text, size=args.chunk_size, overlap=args.chunk_overlap)
                for pos, content in enumerate(parts):
                    ch = Chunk(
                        chunk_id=uuid.uuid4().hex,
                        doc_id=doc_id,
                        position=pos,
                        content=content,
                        tokens=None,            # si calculas tokens, guárdalos
                        lang=None,
                        title=None,
                        retrieval_tags={},
                        provenance={"loader": "web", "strategy": args.strategy},
                    )
                    session.add(ch)

                stats["parsed_ok"] += 1
                stats["total_chunks"] += len(parts)

            logger.info("parse.ok: %s (chunks=%d)", page.url, len(parts))

        except Exception as e:
            stats["fetched_fail"] += 1
            logger.warning("parse.fail: %s (%s)", page.url, e)

    # ------------------------------ cierre run ------------------------------
    with get_session() as session:
        # OJO: si IngestionRun no usa run_id como PK, cambia esta lectura.
        run = session.get(IngestionRun, run_id)
        if run:
            run.ended_at = datetime.now(UTC)
            run.stats = stats
            session.add(run)

    print("Ingesta completada. Stats:")
    print(json.dumps(stats, indent=2))


if __name__ == "__main__":
    sys.exit(main())
