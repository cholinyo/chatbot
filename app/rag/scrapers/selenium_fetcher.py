# app/rag/scrapers/selenium_fetcher.py
from __future__ import annotations

import time
import logging
import urllib.parse as up
from typing import Iterable, List, Optional, Set, Tuple
from dataclasses import dataclass

from bs4 import BeautifulSoup

# Reutilizamos tu Page, ScrapeConfig y utilidades de robots/rate
from app.rag.scrapers.requests_bs4 import (
    ScrapeConfig, Page, RobotsCache, RateLimiter,
    _canonicalize, _force_https_if_needed, _same_or_subdomain, sha256_hexdigest
)

from selenium import webdriver
from selenium.webdriver.chrome.options import Options as ChromeOptions
from selenium.webdriver.firefox.options import Options as FirefoxOptions
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import WebDriverException, TimeoutException

logger = logging.getLogger("ingestion.web.selenium")

@dataclass
class SeleniumOptions:
    driver: str = "chrome"              # "chrome" | "firefox"
    headless: bool = True
    wait_selector: Optional[str] = None # CSS selector a esperar (render)
    render_wait_ms: int = 3000          # espera extra incluso sin selector
    scroll: bool = False
    scroll_steps: int = 4
    scroll_wait_ms: int = 500
    window_size: str = "1366,900"       # "width,height"

class SeleniumScraper:
    """
    BFS con render JS (Selenium). Respeta robots.txt/crawl-delay según la config.
    - discovery por enlaces del DOM renderizado
    - también expone fetch_url() por si quieres usarlo con sitemaps
    """
    def __init__(self, cfg: ScrapeConfig, sopt: SeleniumOptions) -> None:
        self.cfg = cfg.normalized()
        self.sopt = sopt
        self._driver = self._build_driver()
        self._robots_cache = RobotsCache(self.cfg.user_agent, force_https=self.cfg.force_https)
        self._ratelimiter = RateLimiter(self.cfg.rate_limit_per_host)

    def _build_driver(self):
        ua = self.cfg.user_agent
        w, h = (1366, 900)
        try:
            parts = [int(x) for x in self.sopt.window_size.split(",")]
            if len(parts) == 2:
                w, h = parts
        except Exception:
            pass

        if self.sopt.driver.lower() == "firefox":
            opts = FirefoxOptions()
            opts.headless = self.sopt.headless
            # UA en Firefox vía preferencia
            opts.set_preference("general.useragent.override", ua)
            driver = webdriver.Firefox(options=opts)
            driver.set_window_size(w, h)
            return driver
        else:
            # default: chrome
            opts = ChromeOptions()
            if self.sopt.headless:
                # Chrome moderno
                opts.add_argument("--headless=new")
            opts.add_argument(f"--user-agent={ua}")
            opts.add_argument(f"--window-size={w},{h}")
            opts.add_argument("--disable-gpu")
            # En Windows suele ir bien sin --no-sandbox
            driver = webdriver.Chrome(options=opts)
            return driver

    # -------------------- API pública --------------------
    def crawl(self) -> Iterable[Page]:
        seeds = self.cfg.seeds if isinstance(self.cfg.seeds, list) else [self.cfg.seeds]
        seeds = [_canonicalize(_force_https_if_needed(s, self.cfg.force_https)) for s in seeds]

        q: List[Tuple[str, int]] = [(s, 0) for s in seeds]
        seen: Set[str] = set()
        fetched = 0

        try:
            while q and fetched < self.cfg.max_pages:
                url, d = q.pop(0)
                if url in seen:
                    continue
                seen.add(url)

                if not self._should_visit(url):
                    logger.debug("skip.filters: %s", url)
                    continue

                if not self._is_allowed_by_robots(url):
                    logger.info("robots.block: %s", url)
                    continue

                page = self._fetch(url)
                if not page:
                    continue

                fetched += 1
                yield page

                if d < self.cfg.depth:
                    for nxt in page.links:
                        if nxt not in seen:
                            q.append((nxt, d + 1))
        finally:
            try:
                self._driver.quit()
            except Exception:
                pass

    def fetch_url(self, url: str) -> Optional[Page]:
        url = _canonicalize(_force_https_if_needed(url, self.cfg.force_https))
        if not self._should_visit(url):
            return None
        if not self._is_allowed_by_robots(url):
            return None
        try:
            page = self._fetch(url)
            return page
        finally:
            # Nota: no cerramos el driver aquí para permitir múltiples fetch_url()
            ...

    # -------------------- Internas --------------------
    def _should_visit(self, url: str) -> bool:
        try:
            pu = up.urlparse(url)
            if pu.scheme not in ("http", "https"):
                return False
            if self.cfg.allowed_domains:
                if not _same_or_subdomain(pu.netloc, self.cfg.allowed_domains):
                    return False
            pathq = pu.path + (f"?{pu.query}" if pu.query else "")
            # include
            if self.cfg.include_url_patterns:
                import re
                pats = []
                for p in self.cfg.include_url_patterns:
                    if "*" in p and not p.startswith(".*"):
                        p = re.escape(p).replace(r"\*", ".*")
                    pats.append(re.compile(p))
                if not any(r.search(pathq) for r in pats):
                    return False
            # exclude
            if self.cfg.exclude_url_patterns:
                import re
                pats = []
                for p in self.cfg.exclude_url_patterns:
                    if "*" in p and not p.startswith(".*"):
                        p = re.escape(p).replace(r"\*", ".*")
                    pats.append(re.compile(p))
                if any(r.search(pathq) for r in pats):
                    return False
            return True
        except Exception:
            return False

    def _is_allowed_by_robots(self, url: str) -> bool:
        if self.cfg.robots_policy == "ignore":
            return True
        netloc = up.urlparse(url).netloc.lower()
        if self.cfg.robots_policy == "list" and self.cfg.ignore_robots_for:
            # ignora robots en dominios de la lista
            from app.rag.scrapers.requests_bs4 import _same_or_subdomain as _sub
            if _sub(netloc, self.cfg.ignore_robots_for):
                return True
        return self._robots_cache.allowed(url)

    def _crawl_delay_if_any(self, url: str) -> Optional[float]:
        if self.cfg.robots_policy == "ignore":
            return None
        netloc = up.urlparse(url).netloc.lower()
        if self.cfg.robots_policy == "list" and self.cfg.ignore_robots_for:
            from app.rag.scrapers.requests_bs4 import _same_or_subdomain as _sub
            if _sub(netloc, self.cfg.ignore_robots_for):
                return None
        return self._robots_cache.crawl_delay_or_none(url)

    def _wait_render(self, driver, wait_selector: Optional[str], render_wait_ms: int):
        # Espera documento listo
        end = time.time() + max(render_wait_ms / 1000.0, 0.5)
        try:
            # Espera rápida a readyState=complete
            WebDriverWait(driver, max(1, min(5, render_wait_ms // 1000))).until(
                lambda d: d.execute_script("return document.readyState") == "complete"
            )
        except TimeoutException:
            pass
        # Si hay selector, espera su presencia
        if wait_selector:
            try:
                WebDriverWait(driver, render_wait_ms / 1000.0).until(
                    EC.presence_of_element_located((By.CSS_SELECTOR, wait_selector))
                )
            except TimeoutException:
                logger.debug("wait.selector.timeout: %s", wait_selector)
        # Espera pasiva residual
        now = time.time()
        if now < end:
            time.sleep(end - now)

    def _do_scroll(self, driver, steps: int, wait_ms: int):
        for _ in range(max(1, steps)):
            driver.execute_script("window.scrollTo(0, document.body.scrollHeight);")
            time.sleep(max(wait_ms, 0) / 1000.0)

    def _fetch(self, url: str) -> Optional[Page]:
        # Rate-limit + crawl-delay
        crawl_delay = self._crawl_delay_if_any(url)
        netloc = up.urlparse(url).netloc
        self._ratelimiter.wait(netloc, extra_min_interval=crawl_delay)

        try:
            self._driver.set_page_load_timeout(self.cfg.timeout_seconds)
            self._driver.get(url)
        except WebDriverException as e:
            logger.warning("selenium.get.fail: %s (%s)", url, e)
            return None

        # Render/esperas
        self._wait_render(self._driver, self.sopt.wait_selector, self.sopt.render_wait_ms)
        if self.sopt.scroll:
            self._do_scroll(self._driver, self.sopt.scroll_steps, self.sopt.scroll_wait_ms)

        # Extraer HTML + URL final
        try:
            base = self._driver.current_url
        except Exception:
            base = url
        base = _force_https_if_needed(base, self.cfg.force_https)
        html = self._driver.page_source or ""
        if len(html.encode("utf-8", errors="ignore")) < self.cfg.min_html_bytes:
            logger.debug("skip.too_small (selenium): %s", url)
            return None

        # Parse DOM ya renderizado
        soup = BeautifulSoup(html, "html.parser")
        title = None
        t = soup.find("title")
        if t and t.text:
            title = t.text.strip()[:500]

        # canonical
        canonical = soup.find("link", rel=lambda v: v and "canonical" in (v if isinstance(v, list) else [v]))
        if canonical and canonical.get("href"):
            can_url = up.urljoin(base, canonical["href"].strip())
            can_url = _canonicalize(_force_https_if_needed(can_url, self.cfg.force_https))
        else:
            can_url = _canonicalize(base)

        # links renderizados
        links: List[str] = []
        for a in soup.find_all("a", href=True):
            href = a["href"].strip()
            abs_url = up.urljoin(base, href)
            abs_url = _force_https_if_needed(abs_url, self.cfg.force_https)
            abs_url = _canonicalize(abs_url)
            links.append(abs_url)
        # de-dup preservando orden
        seen: Set[str] = set()
        uniq: List[str] = []
        for u in links:
            if u not in seen:
                seen.add(u)
                uniq.append(u)

        page = Page(
            url=can_url,
            base_url=base,
            html=html,
            status_code=200,               # Selenium no expone status fácil
            headers={},                    # opcional: vacío
            title=title,
            links=uniq,
        )
        page.origin_hash = sha256_hexdigest(html)
        logger.info("selenium.fetch.ok: %s (links=%d)", page.url, len(page.links))
        return page
