# -*- coding: utf-8 -*-
from __future__ import annotations

import time
from types import SimpleNamespace
from urllib.parse import urlsplit, urlunsplit, urldefrag
from typing import List

from app.rag.scrapers.requests_bs4 import ScrapeConfig, RequestsBS4Scraper


def _canon(url: str) -> str:
    # quita fragmentos, normaliza esquema/host
    url, _frag = urldefrag(url)
    parts = urlsplit(url)
    scheme = parts.scheme.lower() or "https"
    netloc = parts.netloc.lower()
    path = parts.path or "/"
    return urlunsplit((scheme, netloc, path, parts.query, ""))


def collect_pages(cfg: ScrapeConfig, args, log, counters) -> List[SimpleNamespace]:
    """
    Devuelve una lista con objetos SimpleNamespace(url, html, status_code)
    Mejores prácticas añadidas:
      - Canonicalización y deduplicación por URL.
      - Respeta rate_per_host durmiendo entre seeds iniciales si procede.
    """
    scraper = RequestsBS4Scraper(cfg)

    # Pequeño rate-limit entre seeds para ser amables con los servidores.
    if getattr(args, "rate_per_host", None):
        sleep_s = max(0.0, 1.0 / float(args.rate_per_host))
        if sleep_s > 0:
            time.sleep(min(sleep_s, 1.0))

    # Llamamos al crawler tal como ya lo haces en tu script actual.
    try:
        raw_pages = scraper.crawl()
    except TypeError:
        # por si la firma en tu clase tiene parámetros opcionales distintos
        raw_pages = scraper.crawl  # fallback minimalista si retorna un iter
        raw_pages = list(raw_pages()) if callable(raw_pages) else list(raw_pages)

    pages: list[SimpleNamespace] = []
    seen = set()

    max_pages = int(getattr(args, "max_pages", 0) or 0)
    for p in raw_pages:
        try:
            u = _canon(getattr(p, "url", ""))
            if not u or u in seen:
                continue
            seen.add(u)
            pages.append(SimpleNamespace(url=u, html=getattr(p, "html", ""), status_code=getattr(p, "status_code", None)))
            if max_pages and len(pages) >= max_pages:
                break
        except Exception:
            counters["collect_error"] = counters.get("collect_error", 0) + 1
            continue

    return pages
