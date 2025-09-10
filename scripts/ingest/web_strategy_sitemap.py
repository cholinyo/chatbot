# -*- coding: utf-8 -*-
from __future__ import annotations

import time
import requests
from types import SimpleNamespace
from typing import Iterable, List
from urllib.parse import urlsplit, urlunsplit, urldefrag

from app.rag.scrapers.sitemap import discover_sitemaps_from_robots, collect_all_pages

# extensiones binarias típicas (skip por URL)
BINARY_SUFFIXES = (
    ".pdf",".zip",".rar",".7z",".gz",".bz2",".tar",".iso",
    ".jpg",".jpeg",".png",".gif",".webp",".svg",".ico",
    ".mp4",".webm",".mov",".avi",".wmv",".mp3",".wav",".ogg",
    ".doc",".docx",".ppt",".pptx",".xls",".xlsx",".ods",".odt"
)

NON_HTML_CT_PREFIXES = ("application/", "image/", "audio/", "video/", "font/")  # si Content-Type empieza así, no es HTML


def _canon(url: str) -> str:
    url, _frag = urldefrag(url)
    parts = urlsplit(url)
    scheme = parts.scheme.lower() or "https"
    netloc = parts.netloc.lower()
    path = parts.path or "/"
    return urlunsplit((scheme, netloc, path, parts.query, ""))


def _should_skip_by_ext(url: str) -> bool:
    u = url.split("?", 1)[0].lower()
    return any(u.endswith(ext) for ext in BINARY_SUFFIXES)


def _throttle(host_last: dict, url: str, rate_per_host: float):
    if not rate_per_host:
        return
    host = urlsplit(url).netloc
    min_interval = 1.0 / max(0.001, float(rate_per_host))
    now = time.monotonic()
    last = host_last.get(host, 0.0)
    wait = last + min_interval - now
    if wait > 0:
        time.sleep(wait)
    host_last[host] = time.monotonic()


def _fetch(session: requests.Session, url: str, args, counters) -> SimpleNamespace | None:
    try:
        resp = session.get(url, timeout=float(getattr(args, "timeout", 15)))
    except requests.RequestException:
        counters["fetch_error"] = counters.get("fetch_error", 0) + 1
        return None

    sc = int(getattr(resp, "status_code", 0) or 0)
    if sc == 404:
        counters["fetch_404"] = counters.get("fetch_404", 0) + 1
        # Fallback http<->https si procede
        try_fallback = (url.startswith("https://") or url.startswith("http://"))
        if try_fallback:
            alt = ("http://" + url[8:]) if url.startswith("https://") else ("https://" + url[7:])
            try:
                r2 = session.get(alt, timeout=float(getattr(args, "timeout", 15)))
                if getattr(r2, "ok", False):
                    counters["http_fallback_ok"] = counters.get("http_fallback_ok", 0) + 1
                    resp = r2
                    url = alt
                    sc = int(getattr(resp, "status_code", 0) or 0)
                else:
                    counters["fetch_http_error"] = counters.get("fetch_http_error", 0) + 1
                    return None
            except requests.RequestException:
                counters["fetch_http_error"] = counters.get("fetch_http_error", 0) + 1
                return None
        else:
            return None
    elif sc >= 400:
        counters["fetch_http_error"] = counters.get("fetch_http_error", 0) + 1
        return None

    # Verifica Content-Type (evita binarios)
    ct = (resp.headers.get("Content-Type") or "").lower()
    if any(ct.startswith(prefix) for prefix in NON_HTML_CT_PREFIXES):
        counters["non_html_skipped"] = counters.get("non_html_skipped", 0) + 1
        return None

    html = resp.text or ""
    return SimpleNamespace(url=url, html=html, status_code=sc)


def collect_pages(cfg, args, log, counters) -> List[SimpleNamespace]:
    """
    Devuelve una lista con objetos SimpleNamespace(url, html, status_code)
    Mejoras:
      - Descubrimiento robusto de sitemaps desde robots.txt
      - Skip previo por extensión binaria
      - Rate-limit por host con cache temporal
      - Fallback http<->https cuando hay 404
      - Chequeo de Content-Type para asegurar HTML
      - Canonicalización + dedupe
    """
    seed = getattr(args, "seed", None) or getattr(cfg, "seed", None)
    if not seed:
        return []

    # 1) descubrir sitemaps desde robots.txt del dominio
    try:
        sitemaps = list(discover_sitemaps_from_robots(seed))
    except Exception:
        sitemaps = []

    # 2) expandir páginas desde los sitemaps
    urls: Iterable[str] = []
    try:
        urls = collect_all_pages(sitemaps, cfg)  # tu utilidad ya filtra/limita según config
    except Exception:
        urls = []

    # 3) fetch con mejoras
    session = requests.Session()
    session.headers.update({"User-Agent": getattr(args, "user_agent", "Mozilla/5.0")})

    max_pages = int(getattr(args, "max_pages", 0) or 0)
    rate_per_host = float(getattr(args, "rate_per_host", 1.0) or 1.0)

    pages: list[SimpleNamespace] = []
    seen = set()
    host_last: dict[str, float] = {}

    for raw_url in urls:
        u = _canon(raw_url)
        if not u or u in seen:
            continue
        if _should_skip_by_ext(u):
            counters["skip_by_ext"] = counters.get("skip_by_ext", 0) + 1
            log(f"[skip] sitemap.skip_by_ext {u}")
            continue

        _throttle(host_last, u, rate_per_host)
        ns = _fetch(session, u, args, counters)
        if ns is None:
            continue

        seen.add(u)
        pages.append(ns)
        if max_pages and len(pages) >= max_pages:
            break

    return pages
