# -*- coding: utf-8 -*-
"""
Sitemap strategy con soporte de PDFs + fallbacks robustos.

- Usa tus helpers si existen (local sitemap.py o app.rag.scrapers.sitemap).
- Si no existen o devuelven vacío, descubre sitemaps leyendo robots.txt y/o
  probando rutas típicas: /sitemap.xml, /sitemap_index.xml y sus variantes .gz.
- Recolecta URLs HTML y PDF desde <urlset> y también recorre <sitemapindex> (hasta 1–2 niveles).
- Descarga HTML en .content (str) y PDF en .content_bytes (bytes).
"""

from __future__ import annotations

import io
import gzip
import time
import typing as t
import requests
from types import SimpleNamespace
from urllib.parse import urlparse, urljoin
from xml.etree import ElementTree as ET

# ---------------------------------------------
# Helpers opcionales de tu repo (dos rutas)
# ---------------------------------------------
discover_sitemaps_from_robots = collect_all_pages = None
try:
    # 1) módulo local (p.ej. scripts/sitemap.py)
    from sitemap import discover_sitemaps_from_robots, collect_all_pages  # type: ignore
except Exception:
    try:
        # 2) paquete del proyecto
        from app.rag.scrapers.sitemap import discover_sitemaps_from_robots, collect_all_pages  # type: ignore
    except Exception:
        pass


# -------------------- utilidades --------------------

def _cfg_get(cfg: t.Any, key: str, default=None):
    """Obtiene una clave de cfg (dict o SimpleNamespace)."""
    if isinstance(cfg, dict):
        return cfg.get(key, default)
    return getattr(cfg, key, default)

def _is_http(u: str) -> bool:
    return u.startswith("http://") or u.startswith("https://")

def _same_domain(url: str, allowed_domains: t.Iterable[str] | None) -> bool:
    if not allowed_domains:
        return True
    host = urlparse(url).netloc.lower()
    for d in allowed_domains:
        d = (d or "").lower().strip()
        if not d:
            continue
        if host == d or host.endswith("." + d):
            return True
    return False

def _match_any(patterns: t.List[str], text: str, default: bool) -> bool:
    import re
    if not patterns:
        return default
    for pat in patterns:
        try:
            if any(ch in pat for ch in ".*?[]()|\\"):  # regex
                if re.search(pat, text):
                    return True
            else:
                if pat in text:
                    return True
        except Exception:
            if pat in text:
                return True
    return False

def _should_visit(url: str, allowed_domains: t.List[str], include: t.List[str], exclude: t.List[str]) -> bool:
    if not _same_domain(url, allowed_domains):
        return False
    if include and not _match_any(include, url, default=True):
        return False
    if exclude and _match_any(exclude, url, default=False):
        return False
    return True

def _rate_sleep(rate_per_host: float):
    delay = 1.0 / max(1e-6, rate_per_host)
    if delay > 0:
        time.sleep(delay)

def _fetch(url: str, *, timeout: int, user_agent: str) -> requests.Response:
    return requests.get(url, timeout=timeout, headers={"User-Agent": user_agent})

def _try_gunzip(data: bytes) -> bytes:
    try:
        return gzip.decompress(data)
    except Exception:
        try:
            return gzip.GzipFile(fileobj=io.BytesIO(data)).read()
        except Exception:
            return data

def _load_xml(url: str, *, timeout: int, user_agent: str) -> ET.Element | None:
    try:
        resp = _fetch(url, timeout=timeout, user_agent=user_agent)
        if resp.status_code != 200:
            return None
        data = resp.content
        if url.endswith(".gz"):
            data = _try_gunzip(data)
        return ET.fromstring(data)
    except Exception:
        return None


# ---------------- descubrimiento de sitemaps ----------------

def _sitemaps_via_robots(seed: str, *, timeout: int, user_agent: str, force_https: bool) -> t.List[str]:
    """Lee robots.txt y extrae líneas Sitemap:"""
    parsed = urlparse(seed)
    scheme = "https" if force_https else (parsed.scheme or "http")
    robots_url = f"{scheme}://{parsed.netloc}/robots.txt"
    out: t.List[str] = []
    try:
        r = _fetch(robots_url, timeout=timeout, user_agent=user_agent)
        if r.status_code == 200:
            for line in (r.text or "").splitlines():
                if line.lower().startswith("sitemap:"):
                    url = line.split(":", 1)[1].strip()
                    if url and _is_http(url):
                        out.append(url)
    except Exception:
        pass
    # dedupe conservando orden
    seen, res = set(), []
    for u in out:
        if u not in seen:
            res.append(u); seen.add(u)
    return res

def _discover_sitemaps(seed: str, *, timeout: int, user_agent: str, force_https: bool) -> t.List[str]:
    """Intenta helpers; si no, robots.txt y rutas típicas."""
    # 1) helpers del repo
    if discover_sitemaps_from_robots:
        try:
            sm = discover_sitemaps_from_robots(seed, user_agent=user_agent, timeout=timeout, force_https=force_https) or []
            if sm:
                return _dedupe(sm)
        except Exception:
            pass

    # 2) robots.txt manual
    sm = _sitemaps_via_robots(seed, timeout=timeout, user_agent=user_agent, force_https=force_https)
    if sm:
        return _dedupe(sm)

    # 3) rutas típicas
    parsed = urlparse(seed)
    scheme = "https" if force_https else (parsed.scheme or "http")
    base = f"{scheme}://{parsed.netloc}"
    candidates = [
        urljoin(base, "/sitemap.xml"),
        urljoin(base, "/sitemap_index.xml"),
        urljoin(base, "/sitemap.xml.gz"),
        urljoin(base, "/sitemap_index.xml.gz"),
    ]
    return _dedupe(candidates)

def _dedupe(seq: t.Iterable[str]) -> t.List[str]:
    seen, out = set(), []
    for x in seq:
        if x not in seen:
            out.append(x); seen.add(x)
    return out


# ----------- parseo de sitemaps (sin helpers) -------------

def _iter_sitemap_urls(root: ET.Element) -> t.Iterable[str]:
    tag = root.tag.lower()
    if tag.endswith("sitemapindex"):
        for el in root.findall(".//*"):
            if el.tag.lower().endswith("loc") and el.text:
                yield el.text.strip()
    else:  # urlset
        for el in root.findall(".//*"):
            if el.tag.lower().endswith("loc") and el.text:
                yield el.text.strip()

def _collect_urls_from_sitemaps(
    seed: str,
    *,
    allowed_domains: t.List[str],
    include: t.List[str],
    exclude: t.List[str],
    user_agent: str,
    timeout: int,
    force_https: bool,
    max_urls: int,
    kind: str | None = None,  # None=todo, "pdf", "html"
) -> t.List[str]:
    """Parsea sitemap(s) y devuelve URLs filtradas."""
    smaps = _discover_sitemaps(seed, timeout=timeout, user_agent=user_agent, force_https=force_https)
    urls: t.List[str] = []
    seen: set[str] = set()

    def accept(u: str) -> bool:
        lu = (u or "").lower()
        if not _is_http(u):
            return False
        if kind == "pdf" and not lu.endswith(".pdf"):
            return False
        if kind == "html" and lu.endswith(".pdf"):
            return False
        return _should_visit(u, allowed_domains, include, exclude)

    # Recorremos raiz + un nivel (si hay sitemapindex)
    for sm in smaps:
        root = _load_xml(sm, timeout=timeout, user_agent=user_agent)
        if root is None:
            continue
        if root.tag.lower().endswith("sitemapindex"):
            for child in _iter_sitemap_urls(root):
                r2 = _load_xml(child, timeout=timeout, user_agent=user_agent)
                if r2 is None:
                    continue
                for loc in _iter_sitemap_urls(r2):
                    if accept(loc) and loc not in seen:
                        urls.append(loc); seen.add(loc)
                        if len(urls) >= max_urls:
                            return urls
        else:
            for loc in _iter_sitemap_urls(root):
                if accept(loc) and loc not in seen:
                    urls.append(loc); seen.add(loc)
                    if len(urls) >= max_urls:
                        return urls
    return urls


# -------------------- entrada principal --------------------

def collect_pages(cfg, args, log, counters):
    """
    Devuelve lista de SimpleNamespace:
      - HTML: .content (str), .status_code, .headers
      - PDF : .content_bytes (bytes), .is_binary=True, .ext=".pdf"
    """
    seed = _cfg_get(cfg, "seed", None) or args.seed
    allowed_domains = _cfg_get(cfg, "allowed_domains", []) or []
    include = _cfg_get(cfg, "include", []) or []
    exclude = _cfg_get(cfg, "exclude", []) or []
    max_pages = int(_cfg_get(cfg, "max_pages", args.max_pages) or 100)
    user_agent = _cfg_get(cfg, "user_agent", args.user_agent) or args.user_agent
    timeout = int(_cfg_get(cfg, "timeout", args.timeout) or 15)
    rate_per_host = float(_cfg_get(cfg, "rate_per_host", args.rate_per_host) or 1.0)
    force_https = bool(_cfg_get(cfg, "force_https", args.force_https))

    # 1) HTML vía helper (si existe)
    html_urls: t.List[str] = []
    if collect_all_pages:
        try:
            html_urls = collect_all_pages(
                seed,
                allowed_domains=allowed_domains,
                include_patterns=include,
                exclude_patterns=exclude,
                user_agent=user_agent,
                timeout=timeout,
                force_https=force_https,
                max_urls=max_pages,
            ) or []
            if html_urls:
                log(f"[sitemap] HTML via helper: {len(html_urls)} urls")
        except Exception:
            html_urls = []

    # 2) PDFs parseando sitemaps (siempre)
    pdf_urls = _collect_urls_from_sitemaps(
        seed,
        allowed_domains=allowed_domains,
        include=include,
        exclude=exclude,
        user_agent=user_agent,
        timeout=timeout,
        force_https=force_https,
        max_urls=max_pages,
        kind="pdf",
    )
    if pdf_urls:
        log(f"[sitemap] PDFs: {len(pdf_urls)} urls")

    # 3) Si no hubo HTML por helper, intentamos HTML parseando sitemaps
    if not html_urls:
        html_urls = _collect_urls_from_sitemaps(
            seed,
            allowed_domains=allowed_domains,
            include=include,
            exclude=exclude,
            user_agent=user_agent,
            timeout=timeout,
            force_https=force_https,
            max_urls=max_pages,
            kind="html",
        )
        if html_urls:
            log(f"[sitemap] HTML via parse: {len(html_urls)} urls")

    # 4) unión sin duplicados (HTML primero)
    seen, merged = set(), []
    for u in html_urls + pdf_urls:
        if u not in seen:
            merged.append(u); seen.add(u)

    if not merged:
        log("[sitemap] no se encontraron URLs en sitemap(s)")
        return []

    # 5) descarga
    pages: t.List[SimpleNamespace] = []
    for u in merged[:max_pages]:
        try:
            _rate_sleep(rate_per_host)
            resp = _fetch(u, timeout=timeout, user_agent=user_agent)
            hdrs = dict(resp.headers or {})
            if u.lower().endswith(".pdf") or "application/pdf" in hdrs.get("Content-Type", "").lower():
                pages.append(SimpleNamespace(
                    url=u, content_bytes=resp.content, status_code=resp.status_code,
                    headers=hdrs, is_binary=True, ext=".pdf"
                ))
            else:
                pages.append(SimpleNamespace(
                    url=u, content=resp.text, status_code=resp.status_code, headers=hdrs
                ))
        except Exception:
            continue

    return pages
