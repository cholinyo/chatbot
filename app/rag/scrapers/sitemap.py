# app/rag/scrapers/sitemap.py
from __future__ import annotations

import logging
import re
from typing import List, Tuple, Set, Optional, Union
from urllib.parse import urlparse, urlunparse

import requests
import xml.etree.ElementTree as ET

logger = logging.getLogger("ingestion.web.sitemap")

XML_NS = {"sm": "http://www.sitemaps.org/schemas/sitemap/0.9"}


def _normalize_scheme(url: str, force_https: bool = False) -> str:
    """
    Normaliza el esquema de la URL. Si force_https=True, cambia http→https manteniendo host y path.
    """
    try:
        u = urlparse(url)
        scheme = "https" if force_https else (u.scheme or "https")
        return urlunparse((scheme, u.netloc, u.path or "/", u.params, u.query, u.fragment))
    except Exception:
        return url


def _get(url: str, *, user_agent: str, timeout: int = 15) -> requests.Response:
    headers = {"User-Agent": user_agent or "Mozilla/5.0"}
    resp = requests.get(url, headers=headers, timeout=timeout)
    resp.raise_for_status()
    return resp


def parse_sitemap_or_index(url: str, *, user_agent: str, timeout: int = 15) -> Tuple[List[str], List[str]]:
    """
    Devuelve (pages, subsitemaps) leídos desde `url`, que puede ser un sitemap.xml o un sitemapindex.xml.
    """
    try:
        r = _get(url, user_agent=user_agent, timeout=timeout)
    except Exception as e:
        logger.warning("sitemap.get error url=%s: %r", url, e)
        return [], []

    content = r.content
    try:
        root = ET.fromstring(content)
    except Exception as e:
        logger.warning("sitemap.parse error url=%s: %r", url, e)
        return [], []

    pages: List[str] = []
    subs: List[str] = []

    tag = root.tag.lower()
    # namespaced tags often endwith 'urlset' or 'sitemapindex'
    if tag.endswith("urlset"):
        for loc in root.findall(".//sm:url/sm:loc", XML_NS):
            if loc.text:
                pages.append(loc.text.strip())
    elif tag.endswith("sitemapindex"):
        for s in root.findall(".//sm:sitemap/sm:loc", XML_NS):
            if s.text:
                subs.append(s.text.strip())
    else:
        # Intento genérico sin namespace
        for loc in root.findall(".//url/loc"):
            if loc.text:
                pages.append(loc.text.strip())
        for s in root.findall(".//sitemap/loc"):
            if s.text:
                subs.append(s.text.strip())

    return pages, subs


def collect_all_pages(
    seed_sitemaps: Union[List[str], str],
    *,
    force_https: bool = False,
    user_agent: str = "Mozilla/5.0",
    allowed_domains: Optional[List[str]] = None,
    include: Optional[Union[List[str], str]] = None,
    exclude: Optional[Union[List[str], str]] = None,
    max_pages: Optional[int] = None,
    timeout: int = 15,
) -> Tuple[List[str], List[str]]:
    """
    Recorre sitemapindex -> sitemaps -> urlset de forma recursiva.
    Aplica filtrado por dominio y patrones, y limita con max_pages si se indica.
    Retorna (pages_filtradas, visited_sitemaps).
    """
    # Normalizar semillas
    if isinstance(seed_sitemaps, str):
        queue: List[str] = [seed_sitemaps]
    else:
        queue = list(seed_sitemaps or [])

    # Normalizar filtros
    allowed_domains = (allowed_domains or [])
    if isinstance(include, str):
        include = [include]
    if isinstance(exclude, str):
        exclude = [exclude]

    def _normalize(url: str) -> str:
        return _normalize_scheme(url, force_https)

    def _domain_allowed(u: str) -> bool:
        if not allowed_domains:
            return True
        host = urlparse(u).netloc.lower()
        return any(host == d.lower() or host.endswith("." + d.lower()) for d in allowed_domains)

    def _pattern_ok(u: str) -> bool:
        # Usamos subcadenas o regex simples (compatibles con la UI)
        def _match(pat: str, txt: str) -> bool:
            # Si parece regex, usa re; si no, substring
            if any(ch in pat for ch in ".*?[]()|\\"):
                return re.search(pat, txt, re.IGNORECASE) is not None
            return pat.lower() in txt.lower()
        if include:
            if not any(p and _match(p, u) for p in include):
                return False
        if exclude:
            if any(p and _match(p, u) for p in exclude):
                return False
        return True

    visited: Set[str] = set()
    out_pages: List[str] = []

    while queue:
        sm = _normalize(queue.pop())
        if sm in visited:
            continue
        visited.add(sm)

        pages, subs = parse_sitemap_or_index(sm, user_agent=user_agent, timeout=timeout)

        # Añadir páginas filtradas (y cortar temprano si se alcanza max_pages)
        for p in pages:
            p = _normalize(p)
            if _domain_allowed(p) and _pattern_ok(p):
                out_pages.append(p)
                if max_pages and max_pages > 0 and len(out_pages) >= max_pages:
                    queue.clear()
                    break

        # Encolar sitemaps hijos
        for s in subs:
            s = _normalize(s)
            if s not in visited:
                queue.append(s)

    # Asegurar límite si no se cortó antes
    if max_pages and max_pages > 0 and len(out_pages) > max_pages:
        out_pages = out_pages[:max_pages]
    return out_pages, sorted(visited)