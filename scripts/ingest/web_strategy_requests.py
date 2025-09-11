# -*- coding: utf-8 -*-
from __future__ import annotations

import os, time, re, inspect
import requests
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeout
from bs4 import BeautifulSoup
from types import SimpleNamespace
from typing import List, Optional, Iterable, Dict, Set, Tuple
from urllib.parse import urlsplit, urlunsplit, urldefrag, urljoin

# Intenta usar tu scraper nativo si está disponible
ScrapeConfig = RequestsBS4Scraper = None
try:
    from app.rag.scrapers.requests_bs4 import ScrapeConfig as _ScrapeConfig, RequestsBS4Scraper as _ReqScraper
    ScrapeConfig, RequestsBS4Scraper = _ScrapeConfig, _ReqScraper
except Exception:
    pass

REGEX_CHARS = r".*?[]()|\\"

def _canon(url: str) -> str:
    url, _ = urldefrag(url)
    parts = urlsplit(url)
    scheme = parts.scheme.lower() or "https"
    netloc = parts.netloc.lower()
    path = parts.path or "/"
    return urlunsplit((scheme, netloc, path, parts.query, ""))

def _same_domain(url: str, allowed_domains: Optional[Iterable[str]]) -> bool:
    if not allowed_domains:
        return True
    host = urlsplit(url).netloc.lower()
    for d in allowed_domains:
        d = (d or "").lower().strip()
        if not d:
            continue
        if host == d or host.endswith("." + d):
            return True
    return False

def _match_any(patterns: List[str], text: str, default: bool) -> bool:
    if not patterns:
        return default
    for pat in patterns:
        pat = pat.strip()
        if not pat:
            continue
        if any(ch in pat for ch in REGEX_CHARS):
            try:
                if re.search(pat, text):
                    return True
            except re.error:
                if pat in text:
                    return True
        else:
            if pat in text:
                return True
    return False

def _should_visit(url: str, allowed_domains: List[str], include: List[str], exclude: List[str]) -> bool:
    if not _same_domain(url, allowed_domains):
        return False
    if include and not _match_any(include, url, default=True):
        return False
    if exclude and _match_any(exclude, url, default=False):
        return False
    return True

def _rate_limit_sleep(rate_per_host: float, host_last: Dict[str, float], url: str):
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

# -------------------- Intento 1: usar tu RequestsBS4Scraper (con timeout) --------------------

def _instantiate_scrape_config(cfg, args) -> object | None:
    if ScrapeConfig is None:
        return None

    seed_val = getattr(cfg, "seed", None) or getattr(args, "seed", None)
    max_pages_val = int(getattr(cfg, "max_pages", 0) or getattr(args, "max_pages", 0) or 0)
    depth_val = int(getattr(cfg, "depth", 0) or getattr(args, "depth", 0) or 0)

    kw_candidates = {
        "seed": seed_val, "seeds": [seed_val] if seed_val else [],
        "start_url": seed_val, "start_urls": [seed_val] if seed_val else [],
        "max_pages": max_pages_val, "max_urls": max_pages_val, "limit": max_pages_val, "limit_urls": max_pages_val,
        "depth": depth_val, "max_depth": depth_val, "crawl_depth": depth_val, "depth_limit": depth_val,
        "follow_links": True,
        "allowed_domains": list(getattr(cfg, "allowed_domains", []) or []),
        "allowed_hosts": list(getattr(cfg, "allowed_domains", []) or []),
        "domains": list(getattr(cfg, "allowed_domains", []) or []),
        "include": list(getattr(cfg, "include", []) or []),
        "include_patterns": list(getattr(cfg, "include", []) or []),
        "whitelist_patterns": list(getattr(cfg, "include", []) or []),
        "exclude": list(getattr(cfg, "exclude", []) or []),
        "exclude_patterns": list(getattr(cfg, "exclude", []) or []),
        "blacklist_patterns": list(getattr(cfg, "exclude", []) or []),
        "timeout": int(getattr(cfg, "timeout", 0) or getattr(args, "timeout", 0) or 15),
        "timeout_seconds": int(getattr(cfg, "timeout", 0) or getattr(args, "timeout", 0) or 15),
        "user_agent": getattr(cfg, "user_agent", None) or getattr(args, "user_agent", None) or "Mozilla/5.0",
        "rate_per_host": float(getattr(cfg, "rate_per_host", 0.0) or getattr(args, "rate_per_host", 0.0) or 0.0),
        "robots_policy": getattr(cfg, "robots_policy", None) or getattr(args, "robots_policy", None) or "strict",
        "respect_robots": (getattr(cfg, "robots_policy", None) or getattr(args, "robots_policy", None) or "strict") != "ignore",
        "force_https": bool(getattr(cfg, "force_https", False) or getattr(args, "force_https", False)),
    }

    def _build_kwargs_for(cls):
        try:
            sig = inspect.signature(cls)
        except Exception:
            try:
                sig = inspect.signature(cls.__init__)
            except Exception:
                sig = None
        if not sig:
            return None
        params = sig.parameters
        valid = {k: v for k, v in kw_candidates.items() if k in params}
        return valid

    try:
        kwargs = _build_kwargs_for(ScrapeConfig)
        if not isinstance(kwargs, dict):
            return None
        return ScrapeConfig(**kwargs)
    except Exception:
        return None

def _collect_with_native_scraper(cfg, args, log, counters) -> List[SimpleNamespace] | None:
    if RequestsBS4Scraper is None:
        return None
    sc = _instantiate_scrape_config(cfg, args)
    if sc is None:
        return None

    native_timeout = float(os.getenv("INGEST_NATIVE_TIMEOUT", "20"))  # segundos
    try:
        scraper = RequestsBS4Scraper(sc)

        def _run():
            out = scraper.crawl()
            # Asegura lista consumible
            return list(out) if out is not None else []

        with ThreadPoolExecutor(max_workers=1) as ex:
            fut = ex.submit(_run)
            raw_pages = fut.result(timeout=native_timeout)

        pages: List[SimpleNamespace] = []
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
    except FuturesTimeout:
        log(f"[requests] timeout del scraper nativo tras {native_timeout:.0f}s; usando fallback")
        return None
    except Exception as e:
        log(f"[requests] aviso: fallback al mini-crawler interno por: {e}")
        return None

# -------------------- Intento 2 (fallback): mini-crawler interno --------------------

def _mini_fetch(session: requests.Session, url: str, timeout: int, headers: dict) -> Tuple[str, int, Dict[str, str]]:
    resp = session.get(url, timeout=timeout, headers=headers, allow_redirects=True)
    html = resp.text or ""
    sc = int(resp.status_code or 0)

    # meta-refresh simple
    try:
        if sc in (200, 204, 206) and "http-equiv" in (html.lower() if html else ""):
            m = re.search(r'http-equiv=["\']refresh["\']\s*content=["\'][^;]*;\s*url=([^"\']+)', html, re.I)
            if m:
                nxt = m.group(1).strip()
                if nxt and nxt.startswith(("http://", "https://")):
                    r2 = session.get(nxt, timeout=timeout, headers={**headers, "Referer": url}, allow_redirects=True)
                    html = r2.text or ""
                    sc = int(r2.status_code or 0)
    except Exception:
        pass

    return html, sc, {k: v for k, v in resp.headers.items()}

def _mini_crawl(seed: str, *, depth: int, max_pages: int, timeout: int, user_agent: str,
                allowed_domains: List[str], include: List[str], exclude: List[str],
                rate_per_host: float, log=None) -> List[SimpleNamespace]:
    session = requests.Session()
    base_headers = {
        "User-Agent": user_agent or "Mozilla/5.0",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "es-ES,es;q=0.9,en;q=0.8",
        "Accept-Encoding": "gzip, deflate, br",
        "Connection": "keep-alive",
        "Upgrade-Insecure-Requests": "1",
    }

    visited: Set[str] = set()
    q: List[Tuple[str, int]] = [(seed, 0)]
    pages: List[SimpleNamespace] = []
    seen_out = set()
    host_last: Dict[str, float] = {}

    while q and len(pages) < max_pages:
        url, d = q.pop(0)
        u = _canon(url)
        if u in visited:
            continue
        visited.add(u)

        try:
            _rate_limit_sleep(rate_per_host, host_last, u)
            hdrs = dict(base_headers)
            if pages:
                hdrs["Referer"] = pages[-1].url

            html, status, _headers = _mini_fetch(session, u, timeout=timeout, headers=hdrs)
            pages.append(SimpleNamespace(url=u, html=html, status_code=status))

            if d >= depth:
                continue

            soup = BeautifulSoup(html or "", "html.parser")
            anchors = [a.get("href") for a in soup.select("a[href]") if a.get("href")]
            if log:
                log(f"[requests] enlaces_encontrados={len(anchors)} url={u}")

            for href in anchors:
                nxt = urljoin(u, href.strip())
                cn = _canon(nxt)
                if not cn.startswith("http"):
                    continue
                if cn in visited or cn in seen_out:
                    continue
                if _should_visit(cn, allowed_domains, include, exclude):
                    q.append((cn, d + 1))
                    seen_out.add(cn)

        except Exception:
            if log:
                log(f"[requests] fetch error: {u}")
            continue

    return pages

# ------------------------------ API pública ------------------------------

def collect_pages(cfg, args, log, counters) -> List[SimpleNamespace]:
    """Devuelve objetos con .url, .html, .status_code"""
    seed = getattr(cfg, "seed", None) or getattr(args, "seed", None)
    if not seed:
        return []

    # Permite forzar fallback rápido desde env (útil en PowerShell)
    force_fallback = (os.getenv("INGEST_FORCE_FALLBACK", "").strip() == "1")

    max_pages = int(getattr(cfg, "max_pages", 0) or getattr(args, "max_pages", 0) or 0)
    depth = int(getattr(cfg, "depth", 0) or getattr(args, "depth", 0) or 0)

    pages_native: List[SimpleNamespace] = []
    if not force_fallback:
        pages_native = _collect_with_native_scraper(cfg, args, log, counters) or []

    if len(pages_native) >= max_pages or depth <= 0:
        return pages_native[:max_pages] if max_pages else pages_native

    allowed = list(getattr(cfg, "allowed_domains", []) or [])
    include = list(getattr(cfg, "include", []) or [])
    exclude = list(getattr(cfg, "exclude", []) or [])
    timeout = int(getattr(cfg, "timeout", 15) or getattr(args, "timeout", 15) or 15)
    user_agent = getattr(cfg, "user_agent", None) or getattr(args, "user_agent", None) or "Mozilla/5.0"
    rate_per_host = float(getattr(cfg, "rate_per_host", 1.0) or getattr(args, "rate_per_host", 1.0) or 1.0)

    pages_fallback = _mini_crawl(
        seed,
        depth=depth,
        max_pages=max_pages,
        timeout=timeout,
        user_agent=user_agent,
        allowed_domains=allowed,
        include=include,
        exclude=exclude,
        rate_per_host=rate_per_host,
        log=log,
    )

    # Combinar nativo + fallback con dedupe
    out: List[SimpleNamespace] = []
    seen: Set[str] = set()
    for p in list(pages_native) + list(pages_fallback):
        u = _canon(getattr(p, "url", ""))
        if not u or u in seen:
            continue
        seen.add(u)
        out.append(SimpleNamespace(url=u, html=getattr(p, "html", ""), status_code=getattr(p, "status_code", None)))
        if max_pages and len(out) >= max_pages:
            break

    return out
