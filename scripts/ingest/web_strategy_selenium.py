# -*- coding: utf-8 -*-
from __future__ import annotations

from types import SimpleNamespace
from typing import List
from urllib.parse import urlsplit, urlunsplit, urldefrag

from app.rag.scrapers.selenium_fetcher import SeleniumScraper, SeleniumOptions


def _canon(url: str) -> str:
    url, _frag = urldefrag(url)
    parts = urlsplit(url)
    scheme = parts.scheme.lower() or "https"
    netloc = parts.netloc.lower()
    path = parts.path or "/"
    return urlunsplit((scheme, netloc, path, parts.query, ""))


def collect_pages(cfg, args, log, counters) -> List[SimpleNamespace]:
    """
    Devuelve una lista con objetos SimpleNamespace(url, html, status_code)
    Mejoras:
      - Canonicalización + dedupe.
      - Parámetros Selenium tomados de args si existen.
    """
    # Construimos opciones de Selenium respetando tus flags actuales
    kw = {}
    # headless inverso de 'no_headless'
    if hasattr(args, "no_headless"):
        kw["headless"] = not bool(args.no_headless)
    for name in ("render_wait_ms", "scroll", "scroll_steps", "scroll_wait_ms", "wait_selector", "window_size"):
        if hasattr(args, name):
            kw[name] = getattr(args, name)

    try:
        options = SeleniumOptions(**kw)
    except TypeError:
        # Si la firma no coincide exactamente, creamos y seteamos atributos a mano
        options = SeleniumOptions()
        for k, v in kw.items():
            try:
                setattr(options, k, v)
            except Exception:
                pass

    scraper = SeleniumScraper(cfg, options)
    try:
        raw_pages = scraper.crawl()
    except TypeError:
        raw_pages = list(scraper.crawl) if callable(scraper.crawl) else []

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
