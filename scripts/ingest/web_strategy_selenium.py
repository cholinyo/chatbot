# -*- coding: utf-8 -*-
from __future__ import annotations

import re
import time
from types import SimpleNamespace
from typing import List, Tuple, Optional, Iterable, Dict, Set
from urllib.parse import urlsplit, urlunsplit, urldefrag, urljoin

# Usa tus clases nativas
SeleniumScraper = SeleniumOptions = None
try:
    from app.rag.scrapers.selenium_fetcher import SeleniumScraper as _SelScraper, SeleniumOptions as _SelOptions  # type: ignore
    SeleniumScraper, SeleniumOptions = _SelScraper, _SelOptions
except Exception:
    pass


# --------------------- helpers comunes (canon, filtros) ---------------------

REGEX_CHARS = r".*?[]()|\\"

def _canon(url: str) -> str:
    url, _frag = urldefrag(url)
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


# --------------------- adaptación de cfg y opciones ---------------------

def _parse_window_size(ws: str | Tuple[int, int] | None) -> Tuple[int, int] | None:
    if ws is None:
        return None
    if isinstance(ws, tuple) and len(ws) == 2:
        try:
            return int(ws[0]), int(ws[1])
        except Exception:
            return None
    if isinstance(ws, str):
        s = ws.replace(" ", "").replace("x", ",")
        if "," in s:
            try:
                w, h = s.split(",", 1)
                return int(w), int(h)
            except Exception:
                return None
    return None

class _CfgAdapter:
    """
    Adapta el cfg/args del orquestador a un objeto con .normalized()
    con los campos que suele consumir SeleniumScraper.
    """
    def __init__(self, cfg, args):
        self.seed = getattr(cfg, "seed", None) or getattr(args, "seed", None)
        self.allowed_domains = list(getattr(cfg, "allowed_domains", []) or [])
        self.include = list(getattr(cfg, "include", []) or [])
        self.exclude = list(getattr(cfg, "exclude", []) or [])
        self.user_agent = getattr(cfg, "user_agent", None) or getattr(args, "user_agent", None) or "Mozilla/5.0"
        self.timeout = int(getattr(cfg, "timeout", 15) or getattr(args, "timeout", 15) or 15)
        self.max_pages = int(getattr(cfg, "max_pages", 0) or getattr(args, "max_pages", 0) or 0)
        self.depth = int(getattr(cfg, "depth", 0) or getattr(args, "depth", 0) or 0)
        self.robots_policy = getattr(cfg, "robots_policy", None) or getattr(args, "robots_policy", None) or "strict"
        self.force_https = bool(getattr(cfg, "force_https", False) or getattr(args, "force_https", False))
        # ambos nombres, por compatibilidad con RateLimiter del scraper
        self.rate_per_host = float(getattr(cfg, "rate_per_host", 1.0) or getattr(args, "rate_per_host", 1.0) or 1.0)
        self.rate_limit_per_host = self.rate_per_host

    def normalized(self):
        seed = self.seed
        return SimpleNamespace(
            # semillas
            seed=seed,
            seeds=[seed] if seed else [],
            start_url=seed,
            start_urls=[seed] if seed else [],
            # límites
            max_pages=self.max_pages,
            max_urls=self.max_pages,
            limit=self.max_pages,
            limit_urls=self.max_pages,
            # profundidad
            depth=self.depth,
            max_depth=self.depth,
            crawl_depth=self.depth,
            depth_limit=self.depth,
            follow_links=True,
            # dominios / filtros
            allowed_domains=list(self.allowed_domains or []),
            allowed_hosts=list(self.allowed_domains or []),
            domains=list(self.allowed_domains or []),
            include=list(self.include or []),
            include_patterns=list(self.include or []),
            whitelist_patterns=list(self.include or []),
            exclude=list(self.exclude or []),
            exclude_patterns=list(self.exclude or []),
            blacklist_patterns=list(self.exclude or []),
            # red / headers
            timeout=self.timeout,
            timeout_seconds=self.timeout,
            user_agent=self.user_agent,
            # **ambos nombres para el rate**
            rate_per_host=self.rate_per_host,
            rate_limit_per_host=self.rate_limit_per_host,
            # robots / https
            robots_policy=self.robots_policy,
            respect_robots=(self.robots_policy != "ignore"),
            force_https=self.force_https,
        )

def _build_options_from_args(args):
    kw = {}
    # headless inverso de 'no_headless'
    if hasattr(args, "no_headless"):
        kw["headless"] = not bool(args.no_headless)
    # Driver
    if hasattr(args, "driver") and getattr(args, "driver"):
        kw["driver"] = getattr(args, "driver")
    # Render wait
    if hasattr(args, "render_wait_ms"):
        try:
            kw["render_wait_ms"] = int(getattr(args, "render_wait_ms") or 0)
        except Exception:
            kw["render_wait_ms"] = 0
    # Wait selector
    if hasattr(args, "wait_selector"):
        kw["wait_selector"] = getattr(args, "wait_selector") or ""
    # Window size
    ws = getattr(args, "window_size", None)
    parsed_ws = _parse_window_size(ws)
    if parsed_ws:
        kw["window_size"] = parsed_ws  # (w, h) si la clase lo acepta
    else:
        if ws:
            kw["window_size"] = ws
    # Scroll (desde "--scroll pasos ms" o bandera simple)
    scroll_arg = getattr(args, "scroll", None)
    if isinstance(scroll_arg, list) and len(scroll_arg) > 0:
        kw["scroll"] = True
        try:
            kw["scroll_steps"] = int(scroll_arg[0])
        except Exception:
            pass
        if len(scroll_arg) > 1:
            try:
                kw["scroll_wait_ms"] = int(scroll_arg[1])
            except Exception:
                pass
    elif isinstance(scroll_arg, (bool, int)) and scroll_arg:
        kw["scroll"] = True

    # Intenta construir SeleniumOptions con kwargs tolerantes
    try:
        return SeleniumOptions(**kw)  # type: ignore[arg-type]
    except TypeError:
        options = SeleniumOptions()  # type: ignore[call-arg]
        for k, v in kw.items():
            try:
                setattr(options, k, v)
            except Exception:
                pass
        return options


# --------------------- Fallbacks con Selenium “puro” ---------------------

def _selenium_new_driver(user_agent: str, headless: bool, window_size: Optional[Tuple[int,int]]):
    from selenium import webdriver
    from selenium.webdriver.chrome.options import Options as ChromeOptions
    copt = ChromeOptions()
    # headless “new” evita bloqueos
    if headless:
        copt.add_argument("--headless=new")
    copt.add_argument("--disable-gpu")
    copt.add_argument("--disable-dev-shm-usage")
    copt.add_argument("--no-sandbox")
    copt.add_argument("--window-size=1366,900")
    if user_agent:
        copt.add_argument(f"--user-agent={user_agent}")
    driver = webdriver.Chrome(options=copt)
    if isinstance(window_size, tuple) and len(window_size) == 2:
        try:
            driver.set_window_size(window_size[0], window_size[1])
        except Exception:
            pass
    return driver

def _try_accept_cookies(driver):
    # heurísticas suaves; no pasa nada si fallan
    selectors = [
        "#onetrust-accept-btn-handler",
        "button#onetrust-accept-btn-handler",
        "button[aria-label*='Aceptar']",
        "button[aria-label*='accept']",
        "button:contains('Aceptar')",
        "button:contains('Accept')",
        ".cookies-accept, .cookie-accept, .accept-cookies",
    ]
    for css in selectors:
        try:
            el = driver.find_element("css selector", css)
            el.click()
            time.sleep(0.2)
            break
        except Exception:
            continue

def _wait_render(driver, wait_selector: str, wait_ms: int):
    try:
        from selenium.webdriver.common.by import By
        from selenium.webdriver.support.ui import WebDriverWait
        from selenium.webdriver.support import expected_conditions as EC
        if wait_selector:
            WebDriverWait(driver, max(1, wait_ms // 1000)).until(
                EC.presence_of_element_located((By.CSS_SELECTOR, wait_selector))
            )
        else:
            time.sleep(wait_ms / 1000.0)
    except Exception:
        time.sleep(wait_ms / 1000.0)

def _do_scroll(driver, steps: int, wait_ms: int):
    try:
        for _ in range(max(0, int(steps))):
            driver.execute_script("window.scrollTo(0, document.body.scrollHeight);")
            time.sleep(max(0, wait_ms) / 1000.0)
    except Exception:
        pass

def _extract_links(driver) -> List[str]:
    try:
        els = driver.find_elements("css selector", "a[href]")
        hrefs = []
        for el in els:
            try:
                href = el.get_attribute("href") or ""
                if href and href.startswith(("http://","https://")):
                    hrefs.append(href)
            except Exception:
                continue
        return hrefs
    except Exception:
        return []

def _fallback_spider(cfg, options, log) -> List[SimpleNamespace]:
    """
    Crawler simple con Selenium: BFS hasta depth<=2
    """
    headless = bool(getattr(options, "headless", True))
    ws = getattr(options, "window_size", None)
    wait_ms = int(getattr(options, "render_wait_ms", 2500) or 2500)
    wait_selector = getattr(options, "wait_selector", "") or "a"
    scroll = bool(getattr(options, "scroll", False))
    scroll_steps = int(getattr(options, "scroll_steps", 4) or 4)
    scroll_wait_ms = int(getattr(options, "scroll_wait_ms", 400) or 400)
    ua = getattr(cfg, "user_agent", None) or "Mozilla/5.0"

    seed = getattr(cfg, "seed", None)
    max_pages = int(getattr(cfg, "max_pages", 0) or 0)
    depth = int(getattr(cfg, "depth", 0) or 0)
    allowed = list(getattr(cfg, "allowed_domains", []) or [])
    include = list(getattr(cfg, "include", []) or [])
    exclude = list(getattr(cfg, "exclude", []) or [])

    driver = _selenium_new_driver(ua, headless=headless, window_size=ws if isinstance(ws, tuple) else None)
    pages: List[SimpleNamespace] = []
    seen: Set[str] = set()
    queue: List[Tuple[str,int]] = [(seed, 0)]
    try:
        while queue and (not max_pages or len(pages) < max_pages):
            url, d = queue.pop(0)
            cu = _canon(url)
            if cu in seen:
                continue
            seen.add(cu)

            try:
                driver.get(cu)
                _try_accept_cookies(driver)
                _wait_render(driver, wait_selector, wait_ms)
                if scroll:
                    _do_scroll(driver, scroll_steps, scroll_wait_ms)
                html = driver.page_source or ""
                curr = driver.current_url or cu
                curr = _canon(curr)
                pages.append(SimpleNamespace(url=curr, html=html, status_code=200))
            except Exception as e:
                if log:
                    log(f"[selenium.simple] error navegando {cu}: {e}")
                continue

            if d >= depth:
                continue

            # extraer enlaces renderizados
            hrefs = _extract_links(driver)
            if log:
                log(f"[selenium.simple] enlaces_encontrados={len(hrefs)} url={cu}")

            for h in hrefs:
                nxt = _canon(urljoin(curr, h))
                if not nxt.startswith(("http://","https://")):
                    continue
                if nxt in seen:
                    continue
                if _should_visit(nxt, allowed, include, exclude):
                    queue.append((nxt, d+1))
                # corta si ya llegamos al límite
                if max_pages and len(pages) + len(queue) >= max_pages + 2:
                    break
    finally:
        try:
            driver.quit()
        except Exception:
            pass

    return pages


# --------------------- Camino principal ---------------------

def collect_pages(cfg, args, log, counters) -> List[SimpleNamespace]:
    """
    Devuelve lista de SimpleNamespace(url, html, status_code) usando SeleniumScraper.
    Si el scraper nativo devuelve 0 páginas, usamos un fallback "simple spider" con Selenium.
    """
    if SeleniumScraper is None or SeleniumOptions is None:
        raise RuntimeError("selenium_fetcher no disponible")

    options = _build_options_from_args(args)
    cfg_adapted = _CfgAdapter(cfg, args)

    # 1) scraper nativo
    try:
        scraper = SeleniumScraper(cfg_adapted, options)  # type: ignore[arg-type]
        try:
            raw_pages = scraper.crawl()
        except TypeError:
            raw_pages = list(scraper.crawl) if callable(scraper.crawl) else []
    except Exception as e:
        raw_pages = []
        if log:
            log(f"[selenium] aviso: scraper nativo no disponible: {e}")

    pages: list[SimpleNamespace] = []
    seen = set()
    max_pages = int(getattr(args, "max_pages", 0) or 0)

    for p in raw_pages or []:
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

    # 2) Fallbacks: si 0 páginas, intenta spider con Selenium “puro”
    if not pages:
        if log:
            log("[selenium] scraper devolvió 0 páginas; lanzando fallback spider…")
        seed_pages = _fallback_spider(cfg_adapted.normalized(), options, log)
        # dedupe adicional
        out: List[SimpleNamespace] = []
        seen2: Set[str] = set()
        for sp in seed_pages:
            u = _canon(getattr(sp, "url", ""))
            if not u or u in seen2:
                continue
            seen2.add(u)
            out.append(sp)
            if max_pages and len(out) >= max_pages:
                break
        return out

    return pages
