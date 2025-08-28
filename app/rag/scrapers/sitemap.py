# app/rag/scrapers/sitemap.py
from __future__ import annotations

import io
import logging
from typing import List, Tuple, Set
from urllib.parse import urlparse, urlunparse

import requests
import xml.etree.ElementTree as ET

logger = logging.getLogger("ingestion.web.sitemap")

XML_NS = {"sm": "http://www.sitemaps.org/schemas/sitemap/0.9"}


def _normalize_scheme(url: str, force_https: bool) -> str:
    p = urlparse(url)
    if force_https and p.scheme == "http":
        p = p._replace(scheme="https")
    return urlunparse(p)


def _robots_url(base_url: str, force_https: bool) -> str:
    p = urlparse(base_url)
    p = p._replace(path="/robots.txt", params="", query="", fragment="")
    return _normalize_scheme(urlunparse(p), force_https)


def _default_sitemap(base_url: str, force_https: bool) -> str:
    p = urlparse(base_url)
    p = p._replace(path="/sitemap.xml", params="", query="", fragment="")
    return _normalize_scheme(urlunparse(p), force_https)


def _fetch(url: str, timeout: float = 20.0, headers: dict | None = None) -> tuple[int, str]:
    r = requests.get(url, timeout=timeout, headers=headers or {})
    return r.status_code, r.text or ""


def discover_sitemaps_from_robots(base_url: str, *, force_https: bool, user_agent: str) -> List[str]:
    """
    Busca entradas 'Sitemap:' en robots.txt. Si no hay, vuelve con /sitemap.xml.
    """
    robots = _robots_url(base_url, force_https)
    try:
        code, body = _fetch(robots, headers={"User-Agent": user_agent})
        if code == 200 and body:
            sitemaps = []
            for line in body.splitlines():
                line_l = line.strip().lower()
                if line_l.startswith("sitemap:"):
                    raw = line.split(":", 1)[1].strip()
                    if raw:
                        sitemaps.append(_normalize_scheme(raw, force_https))
            if sitemaps:
                logger.info("sitemap.discovered.from.robots count=%d", len(sitemaps))
                return sitemaps
    except Exception as e:
        logger.warning("sitemap.robots.error url=%s err=%s", robots, e)

    default = _default_sitemap(base_url, force_https)
    logger.info("sitemap.fallback default=%s", default)
    return [default]


def parse_sitemap_or_index(url: str, *, user_agent: str) -> Tuple[List[str], List[str]]:
    """
    Devuelve:
      pages -> URLs de <urlset>
      subs  -> URLs de sitemaps hijos si era <sitemapindex>
    """
    code, xml = _fetch(url, headers={"User-Agent": user_agent})
    if code != 200 or not xml.strip():
        logger.warning("sitemap.fetch.error status=%s url=%s", code, url)
        return [], []
    try:
        root = ET.parse(io.StringIO(xml)).getroot()
    except Exception as e:
        logger.warning("sitemap.parse.error url=%s err=%s", url, e)
        return [], []

    tag = root.tag.lower()
    pages, subs = [], []
    if tag.endswith("urlset"):
        for el in root.findall("sm:url/sm:loc", XML_NS):
            if el.text:
                pages.append(el.text.strip())
        logger.info("sitemap.urlset pages=%d url=%s", len(pages), url)
    elif tag.endswith("sitemapindex"):
        for el in root.findall("sm:sitemap/sm:loc", XML_NS):
            if el.text:
                subs.append(el.text.strip())
        logger.info("sitemap.index subs=%d url=%s", len(subs), url)
    else:
        logger.warning("sitemap.unknown.root tag=%s url=%s", root.tag, url)

    return pages, subs


def collect_all_pages(seed_sitemaps: List[str], *, force_https: bool, user_agent: str) -> Tuple[List[str], List[str]]:
    """
    Recorre sitemapindex -> sitemaps -> urlset de forma recursiva.
    Retorna (pages, visited_sitemaps).
    """
    visited: Set[str] = set()
    out_pages: Set[str] = set()
    queue: List[str] = list(seed_sitemaps)

    while queue:
        sm = queue.pop()
        sm = _normalize_scheme(sm, force_https)
        if sm in visited:
            continue
        visited.add(sm)

        pages, subs = parse_sitemap_or_index(sm, user_agent=user_agent)
        for p in pages:
            out_pages.add(_normalize_scheme(p, force_https))
        for s in subs:
            s = _normalize_scheme(s, force_https)
            if s not in visited:
                queue.append(s)

    return sorted(out_pages), sorted(visited)
