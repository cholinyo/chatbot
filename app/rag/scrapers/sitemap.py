# app/rag/scrapers/sitemap.py
# -----------------------------------------------------------------------------
# Descubrimiento de URLs a partir de sitemaps:
# - Intenta localizar sitemaps desde robots.txt (líneas "Sitemap: ...")
# - Fallback a /sitemap.xml en el host de la seed
# - Soporta <sitemapindex> y <urlset>, y .xml.gz
# - Devuelve una lista de URLs canónicas (sin #fragment), sin duplicados
# - Aplica filtros: dominios permitidos + patrones include/exclude (compatibles
#   con los usados por RequestsBS4Scraper)
# -----------------------------------------------------------------------------

from __future__ import annotations

import io
import gzip
import logging
import urllib.parse as up
from typing import Iterable, List, Optional, Set, Dict
from xml.etree import ElementTree as ET

import requests

logger = logging.getLogger("ingestion.web.sitemap")

DEFAULT_TIMEOUT = 15
XML_CT_HINTS = ("xml", "text/xml", "application/xml")
GZIP_SUFFIX = (".gz",)

# --------------------------- utilidades de URL ---------------------------

def _canonicalize(url: str) -> str:
    u = up.urlsplit(url)
    u = u._replace(fragment="")
    netloc = u.netloc.lower()
    return up.urlunsplit((u.scheme, netloc, u.path, u.query, ""))

def _same_or_subdomain(host: str, allowed: Set[str]) -> bool:
    host = host.lower()
    return any(host == d or host.endswith("." + d) for d in allowed)

# ----------------------------- descargar --------------------------------

def _get(session: requests.Session, url: str, timeout: int = DEFAULT_TIMEOUT) -> Optional[requests.Response]:
    try:
        r = session.get(url, timeout=timeout, allow_redirects=True)
        if r.status_code >= 400:
            logger.info("sitemap.get.fail: %s (%s)", url, r.status_code)
            return None
        return r
    except Exception as e:
        logger.info("sitemap.get.error: %s (%s)", url, e)
        return None

def _maybe_decompress(content: bytes, url: str) -> bytes:
    if url.endswith(GZIP_SUFFIX):
        try:
            return gzip.decompress(content)
        except Exception:
            pass
    return content

# --------------------------- parseo XML sitemap --------------------------

def _parse_sitemap_xml(xml_bytes: bytes, base_url: str) -> List[str]:
    """
    Soporta:
      - <sitemapindex><sitemap><loc>...</loc></sitemap>...</sitemapindex>
      - <urlset><url><loc>...</loc></url>...</urlset>
    """
    urls: List[str] = []
    try:
        root = ET.fromstring(xml_bytes)
    except Exception as e:
        logger.info("sitemap.xml.parse.error: %s (%s)", base_url, e)
        return urls

    tag = (root.tag or "").lower()
    # Normalizar nombres con namespace
    if tag.endswith("sitemapindex"):
        for sm in root.findall(".//{*}sitemap/{*}loc"):
            loc = (sm.text or "").strip()
            if not loc:
                continue
            urls.append(loc)
    elif tag.endswith("urlset"):
        for n in root.findall(".//{*}url/{*}loc"):
            loc = (n.text or "").strip()
            if not loc:
                continue
            urls.append(loc)
    else:
        # Intento amplio (por si namespaces extraños)
        for n in root.findall(".//{*}loc"):
            loc = (n.text or "").strip()
            if loc:
                urls.append(loc)

    # Canonicalizar
    return [_canonicalize(up.urljoin(base_url, u)) for u in urls if u]

# ----------------------------- robots.txt --------------------------------

def _sitemaps_from_robots(session: requests.Session, seed_url: str) -> List[str]:
    pu = up.urlparse(seed_url)
    base = f"{pu.scheme}://{pu.netloc}"
    robots_url = up.urljoin(base, "/robots.txt")
    r = _get(session, robots_url)
    if not r or not r.text:
        return []
    smaps: List[str] = []
    try:
        for line in r.text.splitlines():
            line = line.strip()
            if not line or line.lower().startswith("#"):
                continue
            if line.lower().startswith("sitemap:"):
                # Formato: "Sitemap: http(s)://..."
                loc = line.split(":", 1)[1].strip()
                if loc:
                    smaps.append(_canonicalize(up.urljoin(base, loc)))
    except Exception:
        pass
    return smaps

# ---------------------------- descubrimiento -----------------------------

def discover_sitemaps(session: requests.Session, seeds: Iterable[str]) -> List[str]:
    """
    Dada una lista de seeds, intenta descubrir URLs de sitemaps:
      1) robots.txt (líneas "Sitemap:")
      2) fallback a /sitemap.xml en cada host
    Devuelve lista única de URLs de sitemap (no de páginas).
    """
    sitemap_urls: List[str] = []
    seen: Set[str] = set()

    for s in seeds:
        s = _canonicalize(s)
        pu = up.urlparse(s)
        base = f"{pu.scheme}://{pu.netloc}"
        # 1) robots.txt
        from_robots = _sitemaps_from_robots(session, s)
        for u in from_robots:
            u = _canonicalize(u)
            if u not in seen:
                seen.add(u)
                sitemap_urls.append(u)
        # 2) fallback común
        fallback = _canonicalize(up.urljoin(base, "/sitemap.xml"))
        if fallback not in seen:
            seen.add(fallback)
            sitemap_urls.append(fallback)

    return sitemap_urls

def discover_urls_from_sitemaps(
    session: requests.Session,
    sitemap_urls: Iterable[str],
    *,
    allowed_domains: Optional[Set[str]] = None,
    include_patterns: Optional[list] = None,   # lista de regex YA compiladas
    exclude_patterns: Optional[list] = None,   # lista de regex YA compiladas
    limit: Optional[int] = None,
) -> List[str]:
    """
    Dada una lista de URLs de sitemaps:
      - Resuelve sitemapindex recursivamente
      - Extrae URLs de urlset
      - Filtra por dominios y patrones
      - Devuelve una lista única (orden de aparición)
    """
    out: List[str] = []
    seen: Set[str] = set()

    def _add(u: str):
        u = _canonicalize(u)
        if u in seen:
            return
        # Filtro dominio
        if allowed_domains:
            if not _same_or_subdomain(up.urlparse(u).netloc, allowed_domains):
                return
        # Filtro include
        if include_patterns:
            path = up.urlparse(u).path
            if not any(r.search(path) for r in include_patterns):
                return
        # Filtro exclude
        if exclude_patterns:
            path = up.urlparse(u).path
            if any(r.search(path) for r in exclude_patterns):
                return
        seen.add(u)
        out.append(u)

    # BFs simple sobre sitemaps y sitemapindex
    queue = list(sitemap_urls)
    while queue:
        sm_url = queue.pop(0)
        r = _get(session, sm_url)
        if not r:
            continue
        content = _maybe_decompress(r.content, sm_url)
        # Si el tipo de contenido no sugiere XML, intentamos parsear igual
        urls = _parse_sitemap_xml(content, sm_url)
        if not urls:
            continue

        # Heurística: si los hijos parecen sitemaps, encolamos; si parecen páginas, añadimos.
        # Clave: en sitemapindex los <loc> suelen ser otros sitemaps;
        # en urlset, <loc> son páginas finales.
        # No siempre hay forma 100% fiable, así que mezclamos:
        #   - si termina en .xml o .xml.gz => lo tratamos como posible sitemap
        #   - si contiene "/sitemap" en path => también lo tratamos como sitemap
        #   - en caso contrario => lo tratamos como página
        for u in urls:
            low = u.lower()
            if low.endswith(".xml") or low.endswith(".xml.gz") or "/sitemap" in low:
                # Consideramos que apunta a otro sitemap
                queue.append(u)
            else:
                _add(u)
                if limit and len(out) >= limit:
                    return out

    return out
