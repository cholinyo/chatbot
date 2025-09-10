#!/usr/bin/env python3
# -*- coding: utf-8 -*-
import os, re, sys, json, time, argparse, logging, traceback
from pathlib import Path
from dataclasses import dataclass, asdict
from types import SimpleNamespace
from typing import Iterable, List, Optional, Set, Dict
from urllib.parse import urlparse, urljoin

import requests
from bs4 import BeautifulSoup

# Import sitemap helpers from either local module or package path
discover_sitemaps_from_robots = collect_all_pages = None
try:
    from sitemap import discover_sitemaps_from_robots, collect_all_pages  # local
except Exception:
    try:
        from app.rag.scrapers.sitemap import discover_sitemaps_from_robots, collect_all_pages  # package path
    except Exception:
        pass

# Optional selenium
try:
    from selenium_fetcher import SeleniumScraper, SeleniumOptions
except Exception:
    SeleniumScraper = None
    SeleniumOptions = None

logger = logging.getLogger("ingest_web")
logger.setLevel(logging.INFO)
handler = logging.StreamHandler(sys.stdout)
# Avoid UnicodeEncodeError on Windows cp1252 by replacing non-encodable chars
class SafeFormatter(logging.Formatter):
    def format(self, record):
        try:
            msg = super().format(record)
            return msg
        except UnicodeEncodeError:
            record.msg = str(record.msg).encode('utf-8','ignore').decode('utf-8','ignore')
            return super().format(record)
handler.setFormatter(SafeFormatter("%(message)s"))
logger.addHandler(handler)

def log(msg: str):
    # avoid arrows that break cp1252
    logger.info(str(msg).replace("→","->"))

REGEX_CHARS = ".*?[]()|\\"

def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Web ingestion orchestrator")
    p.add_argument("--seed", required=True)
    p.add_argument("--strategy", choices=["requests", "selenium", "sitemap"], required=True)
    p.add_argument("--source-id", type=int, required=True)
    p.add_argument("--run-id", type=int, required=True)
    p.add_argument("--max-pages", type=int, default=100)
    p.add_argument("--timeout", type=int, default=15)
    p.add_argument("--rate-per-host", type=float, default=1.0)
    p.add_argument("--user-agent", default="Mozilla/5.0")
    p.add_argument("--force-https", action="store_true")
    p.add_argument("--depth", type=int, default=2)
    p.add_argument("--allowed-domains", default="")
    p.add_argument("--include", default="")
    p.add_argument("--exclude", default=r"\.(png|jpg|jpeg|gif|css|js|pdf)$")
    p.add_argument("--robots-policy", choices=["strict", "ignore"], default="strict")
    p.add_argument("--driver", default="chrome")
    p.add_argument("--no-headless", action="store_true")
    p.add_argument("--render-wait-ms", type=int, default=2500)
    p.add_argument("--window-size", default="1366,900")
    p.add_argument("--wait-selector", default="")
    p.add_argument("--scroll", nargs="*")
    p.add_argument("--run-dir", default=os.environ.get("RUN_DIR", ""))
    return p

def split_csv(s: str) -> List[str]:
    return [x.strip() for x in s.split(",") if x.strip()] if s else []

def rate_limit_sleep(rate_per_host: float):
    delay = 1.0 / max(1e-6, rate_per_host)
    if delay > 0:
        time.sleep(delay)

def same_domain(url: str, allowed_domains: Optional[Iterable[str]]) -> bool:
    if not allowed_domains:
        return True
    host = urlparse(url).netloc.lower()
    for d in allowed_domains:
        d = d.lower()
        if host == d or host.endswith("." + d):
            return True
    return False

def match_any(patterns: List[str], text: str, default: bool) -> bool:
    if not patterns:
        return default
    for pat in patterns:
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

def should_visit(url: str, allowed_domains: List[str], include: List[str], exclude: List[str]) -> bool:
    if not same_domain(url, allowed_domains):
        return False
    if include and not match_any(include, url, default=True):
        return False
    if exclude and match_any(exclude, url, default=False):
        return False
    return True

def ensure_run_dirs(run_dir: Path):
    (run_dir / "raw").mkdir(parents=True, exist_ok=True)

@dataclass
class PageArtifact:
    url: str
    path: str
    status: int
    bytes: int

def fetch_url(url: str, *, timeout: int, user_agent: str) -> SimpleNamespace:
    resp = requests.get(url, timeout=timeout, headers={"User-Agent": user_agent})
    return SimpleNamespace(url=url, content=resp.text, status_code=resp.status_code, headers=dict(resp.headers))

def _is_http(u: str) -> bool:
    return u.startswith("http://") or u.startswith("https://")

def crawl_requests(seed: str, *, depth: int, max_pages: int, timeout: int, user_agent: str,
                   allowed_domains: List[str], include: List[str], exclude: List[str],
                   rate_per_host: float) -> List[SimpleNamespace]:
    visited: Set[str] = set()
    queue: List[tuple[str,int]] = [(seed, 0)]
    pages: List[SimpleNamespace] = []
    while queue and len(pages) < max_pages:
        url, d = queue.pop(0)
        if url in visited:
            continue
        visited.add(url)
        try:
            rate_limit_sleep(rate_per_host)
            page = fetch_url(url, timeout=timeout, user_agent=user_agent)
            pages.append(page)
            if d >= depth or page.status_code != 200:
                continue
            soup = BeautifulSoup(page.content, "html.parser")
            for a in soup.select("a[href]"):
                href = (a.get("href") or "").strip()
                if not href:
                    continue
                nxt = urljoin(url, href)
                if not _is_http(nxt):
                    continue  # skip javascript:, mailto:, tel:, etc.
                if should_visit(nxt, allowed_domains, include, exclude) and nxt not in visited:
                    queue.append((nxt, d+1))
        except Exception:
            logger.warning("Failed fetching %s", url, exc_info=True)
    return pages

def crawl_sitemap(seed: str, *, max_pages: int, timeout: int, user_agent: str,
                  allowed_domains: List[str], include: List[str], exclude: List[str],
                  rate_per_host: float, force_https: bool) -> List[SimpleNamespace]:
    if collect_all_pages is None:
        raise RuntimeError("sitemap.py no disponible")
    try:
        if discover_sitemaps_from_robots:
            smaps = discover_sitemaps_from_robots(seed, user_agent=user_agent, timeout=timeout, force_https=force_https)
            log(f"Sitemaps discover: {len(smaps)} encontrado(s)")
    except Exception:
        log("Aviso: fallo al descubrir sitemaps (continuamos)")
    urls = collect_all_pages(
        seed,
        allowed_domains=allowed_domains,
        include_patterns=include,
        exclude_patterns=exclude,
        user_agent=user_agent,
        timeout=timeout,
        force_https=force_https,
        max_urls=max_pages,
    )
    log(f"URLs desde sitemap: {len(urls)}")
    pages: List[SimpleNamespace] = []
    for u in urls[:max_pages]:
        try:
            if not _is_http(u):
                continue
            rate_limit_sleep(rate_per_host)
            page = fetch_url(u, timeout=timeout, user_agent=user_agent)
            pages.append(page)
        except Exception:
            logger.warning("Failed sitemap fetch %s", u, exc_info=True)
    return pages

def crawl_selenium(seed: str, *, depth: int, max_pages: int, timeout: int, user_agent: str,
                   allowed_domains: List[str], include: List[str], exclude: List[str],
                   rate_per_host: float, driver: str, headless: bool, render_wait_ms: int,
                   window_size: str, wait_selector: str, scroll: Optional[List[str]]) -> List[SimpleNamespace]:
    if SeleniumScraper is None or SeleniumOptions is None:
        raise RuntimeError("selenium_fetcher no disponible")
    cfg = SimpleNamespace(
        seed=seed, depth=depth, max_pages=max_pages, timeout=timeout, user_agent=user_agent,
        allowed_domains=allowed_domains, include=include, exclude=exclude, rate_per_host=rate_per_host,
    )
    opts = SeleniumOptions(
        driver=driver, headless=headless, render_wait_ms=render_wait_ms,
        scroll=bool(scroll), scroll_steps=int(scroll[0]) if scroll else 4,
        scroll_wait_ms=int(scroll[1]) if scroll else 500, wait_selector=wait_selector, window_size=window_size,
    )
    scraper = SeleniumScraper(cfg, opts)
    return list(scraper.crawl())

def write_artifacts(run_dir: Path, pages: List[SimpleNamespace]) -> Dict:
    ensure_run_dirs(run_dir)
    fetch_index = []
    total_bytes = 0
    for i, p in enumerate(pages, 1):
        fn = f"{i:05d}.html"
        raw_path = run_dir / "raw" / fn
        try:
            html = p.content if isinstance(p.content, str) else str(p.content)
        except Exception:
            html = ""
        raw_path.write_text(html, encoding="utf-8", errors="ignore")
        size = raw_path.stat().st_size
        total_bytes += size
        fetch_index.append(PageArtifact(url=p.url, path=f"raw/{fn}", status=getattr(p, "status_code", 0), bytes=size).__dict__)
    (run_dir / "fetch_index.json").write_text(json.dumps(fetch_index, ensure_ascii=False, indent=2), encoding="utf-8")
    summary = {"seed": pages[0].url if pages else "", "n_pages": len(pages), "bytes": total_bytes,
               "totals": {"pages": len(pages), "bytes": total_bytes}}
    (run_dir / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    (run_dir / "stdout.txt").write_text(
        "\n".join([f"{i+1:05d} {asdict(PageArtifact(url=p.url, path=fetch_index[i]['path'], status=getattr(p,'status_code',0), bytes=fetch_index[i]['bytes']))}"
                    for i, p in enumerate(pages)]),
        encoding="utf-8"
    )
    return summary

def main():
    args = build_parser().parse_args()
    run_dir = Path(args.run_dir or f"data/processed/runs/web/run_{args.run_id}")
    ensure_run_dirs(run_dir)
    log(f"[RUN_DIR] {run_dir}")
    allowed_domains = split_csv(args.allowed_domains)
    include = split_csv(args.include)
    exclude = split_csv(args.exclude)
    log(f"Strategy: {args.strategy}")
    pages: List[SimpleNamespace] = []
    try:
        if args.strategy == "requests":
            pages = crawl_requests(args.seed, depth=args.depth, max_pages=args.max_pages, timeout=args.timeout,
                                   user_agent=args.user_agent, allowed_domains=allowed_domains,
                                   include=include, exclude=exclude, rate_per_host=args.rate_per_host)
        elif args.strategy == "sitemap":
            pages = crawl_sitemap(args.seed, max_pages=args.max_pages, timeout=args.timeout, user_agent=args.user_agent,
                                  allowed_domains=allowed_domains, include=include, exclude=exclude,
                                  rate_per_host=args.rate_per_host, force_https=bool(args.force_https))
        elif args.strategy == "selenium":
            pages = crawl_selenium(args.seed, depth=args.depth, max_pages=args.max_pages, timeout=args.timeout,
                                   user_agent=args.user_agent, allowed_domains=allowed_domains,
                                   include=include, exclude=exclude, rate_per_host=args.rate_per_host,
                                   driver=args.driver, headless=not args.no_headless,
                                   render_wait_ms=args.render_wait_ms, window_size=args.window_size,
                                   wait_selector=args.wait_selector, scroll=args.scroll)
        else:
            raise ValueError(f"Estrategia no soportada: {args.strategy}")
    except Exception as e:
        log(f"ERROR ejecutando estrategia {args.strategy}: {e}")
        traceback.print_exc()
        if args.strategy != "requests":
            log("Fallback -> requests")
            try:
                pages = crawl_requests(args.seed, depth=args.depth, max_pages=args.max_pages, timeout=args.timeout,
                                       user_agent=args.user_agent, allowed_domains=allowed_domains,
                                       include=include, exclude=exclude, rate_per_host=args.rate_per_host)
            except Exception:
                log("Fallback también falló.")
                pages = []
    summary = write_artifacts(run_dir, pages)
    log(f"FIN: páginas={summary['n_pages']} bytes={summary['bytes']}")
    return 0

if __name__ == "__main__":
    sys.exit(main())
