# File: scripts/ingest_web.py
# -*- coding: utf-8 -*-
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path
from typing import Iterable, List, Dict, Set
from urllib.parse import urljoin, urlparse

import requests

# --- Selenium opcional (solo si strategy=selenium) ---
try:
    from selenium import webdriver
    from selenium.webdriver.chrome.options import Options as ChromeOptions
    from selenium.webdriver.firefox.options import Options as FirefoxOptions
except Exception:
    webdriver = None  # lo detectamos en runtime

# ----------------- CLI ----------------- #

def parse_args():
    p = argparse.ArgumentParser("ingest_web")
    p.add_argument("--seed", required=True, help="URL base o sitemap")
    p.add_argument("--strategy", choices=["sitemap", "requests", "selenium"], default="sitemap")
    p.add_argument("--allowed-domains", default="", help="coma-separated hostnames permitidos")
    p.add_argument("--include", action="append", default=[], help="patrones substring a incluir (repetible)")
    p.add_argument("--exclude", action="append", default=[], help="patrones substring a excluir (repetible)")
    p.add_argument("--depth", type=int, default=1, help="Profundidad para strategy=requests")
    p.add_argument("--max-pages", type=int, default=100)
    p.add_argument("--timeout", type=int, default=15)
    p.add_argument("--rate", type=float, default=1.0, help="segundos entre hosts (no usado en simple)")
    p.add_argument("--user-agent", default="Mozilla/5.0")
    p.add_argument("--robots-policy", default="strict", choices=["strict", "list"])
    p.add_argument("--ignore-robots-for", default="", help="coma-separated hostnames para ignorar robots")
    p.add_argument("--no-robots", action="store_true", help="equivalente a ignorar robots")
    p.add_argument("--force-https", action="store_true")
    p.add_argument("--dump-html", action="store_true")
    p.add_argument("--preview", action="store_true")
    p.add_argument("--verbose", action="store_true")

    # Selenium CFG (si toca)
    p.add_argument("--driver", default="chrome")
    p.add_argument("--window-size", default="1366,900")
    p.add_argument("--render-wait-ms", type=int, default=3000)
    p.add_argument("--wait-selector", default="")
    p.add_argument("--no-headless", action="store_true")
    p.add_argument("--scroll", action="store_true")
    p.add_argument("--scroll-steps", type=int, default=4)
    p.add_argument("--scroll-wait-ms", type=int, default=500)

    return p.parse_args()


# ----------------- Helpers ----------------- #

def normalize_url(u: str, force_https: bool) -> str:
    if force_https:
        pr = urlparse(u)
        if pr.scheme == "http":
            return "https://" + u[len("http://"):]
    return u

def allowed(url: str, allowed_hosts: Set[str], includes: List[str], excludes: List[str]) -> bool:
    h = urlparse(url).netloc.lower()
    if allowed_hosts and h not in allowed_hosts:
        return False
    if includes and not any(s in url for s in includes):
        return False
    if excludes and any(s in url for s in excludes):
        return False
    return True

def ensure_run_dir() -> Path:
    runs_root = Path("data/processed/runs/web")
    runs_root.mkdir(parents=True, exist_ok=True)
    # run id derivado de epoch si se ejecuta fuera de la UI
    rid = int(time.time())
    d = runs_root / f"run_{rid}"
    d.mkdir(parents=True, exist_ok=True)
    print(f"[RUN_DIR] {d}", flush=True)
    return d

def session(user_agent: str, timeout: int) -> requests.Session:
    s = requests.Session()
    s.headers["User-Agent"] = user_agent
    s.timeout = timeout
    return s

def save_raw(run_dir: Path, i: int, url: str, content: bytes) -> str:
    raw_dir = run_dir / "raw"
    raw_dir.mkdir(parents=True, exist_ok=True)
    fname = f"{i:05d}.html"
    path = raw_dir / fname
    path.write_bytes(content)
    return str(path)

# ----------------- Sitemap crawler ----------------- #

def fetch_sitemap(seed: str, sess: requests.Session, max_pages: int) -> List[str]:
    urls: List[str] = []
    try:
        r = sess.get(seed, timeout=sess.timeout)
        r.raise_for_status()
        text = r.text
    except Exception as e:
        print(f"[WARN] sitemap GET failed: {e}", flush=True)
        return urls

    import re
    locs = re.findall(r"<loc>\s*(.*?)\s*</loc>", text, flags=re.IGNORECASE)
    for u in locs:
        u = u.strip()
        if u.endswith(".xml") and "sitemap" in u.lower():
            # sitemap índice: seguir una capa
            try:
                r2 = sess.get(u, timeout=sess.timeout)
                r2.raise_for_status()
                locs2 = re.findall(r"<loc>\s*(.*?)\s*</loc>", r2.text, flags=re.IGNORECASE)
                for u2 in locs2:
                    u2 = u2.strip()
                    if not u2.endswith(".xml"):
                        urls.append(u2)
                        if len(urls) >= max_pages:
                            return urls
            except Exception:
                continue
        else:
            urls.append(u)
            if len(urls) >= max_pages:
                return urls
    return urls[:max_pages]

# ----------------- Requests BFS ----------------- #

from html.parser import HTMLParser

class LinkExtractor(HTMLParser):
    def __init__(self, base: str):
        super().__init__()
        self.base = base
        self.links: Set[str] = set()
    def handle_starttag(self, tag, attrs):
        if tag.lower() != "a":
            return
        href = None
        for k, v in attrs:
            if k.lower() == "href":
                href = v
                break
        if not href:
            return
        try:
            u = urljoin(self.base, href)
            self.links.add(u)
        except Exception:
            pass

def crawl_requests(seed: str, sess: requests.Session, allowed_hosts: Set[str],
                   includes: List[str], excludes: List[str], depth: int,
                   max_pages: int, run_dir: Path, dump_html: bool) -> List[Dict]:
    from collections import deque
    seen: Set[str] = set()
    q = deque([(seed, 0)])
    out: List[Dict] = []
    i = 0

    while q and len(out) < max_pages:
        url, d = q.popleft()
        if url in seen:
            continue
        seen.add(url)

        if not allowed(url, allowed_hosts, includes, excludes):
            print(f"[SKIP] domain/include/exclude: {url}", flush=True)
            continue

        try:
            resp = sess.get(url, timeout=sess.timeout)
            status = resp.status_code
            raw_path = None
            if dump_html and resp.ok and resp.content:
                raw_path = save_raw(run_dir, i, url, resp.content)
            out.append({"url": url, "status": status, "raw": raw_path, "title": None})
            i += 1
        except Exception as e:
            print(f"[ERR] GET {url}: {e}", flush=True)
            out.append({"url": url, "status": 0, "raw": None, "title": None})

        if d < depth:
            try:
                html = resp.text if 'resp' in locals() and resp is not None else ""
                parser = LinkExtractor(url)
                parser.feed(html or "")
                for u in parser.links:
                    if u not in seen and len(out) + len(q) < max_pages:
                        q.append((u, d + 1))
            except Exception:
                pass

    return out

# ----------------- Selenium ----------------- #

def make_driver(args) -> "webdriver.Remote":
    if webdriver is None:
        raise RuntimeError("selenium no está instalado. pip install selenium>=4.12")

    driver_name = (args.driver or "chrome").lower()
    w, h = (int(x) for x in (args.window_size or "1366,900").split(",")[:2])
    headless = not bool(args.no_headless)

    if driver_name == "firefox":
        opts = FirefoxOptions()
        if headless:
            opts.add_argument("-headless")
        drv = webdriver.Firefox(options=opts)  # Selenium Manager resuelve geckodriver
    else:
        opts = ChromeOptions()
        if headless:
            opts.add_argument("--headless=new")
        opts.add_argument(f"--window-size={w},{h}")
        opts.add_argument("--disable-gpu")
        opts.add_argument("--no-sandbox")
        drv = webdriver.Chrome(options=opts)   # Selenium Manager resuelve chromedriver

    try:
        drv.set_page_load_timeout(int(args.timeout or 15))
    except Exception:
        pass
    drv.set_window_size(w, h)
    return drv

def crawl_selenium(seed: str, args, allowed_hosts: Set[str],
                   includes: List[str], excludes: List[str],
                   max_pages: int, run_dir: Path, dump_html: bool) -> List[Dict]:
    from bs4 import BeautifulSoup  # opcional; si no lo tienes, usa parser nativo (ver nota abajo)
    drv = make_driver(args)
    out: List[Dict] = []
    i = 0

    try:
        to_visit = [seed]
        seen: Set[str] = set()

        while to_visit and len(out) < max_pages:
            url = to_visit.pop(0)
            if url in seen:
                continue
            seen.add(url)
            if not allowed(url, allowed_hosts, includes, excludes):
                print(f"[SKIP] domain/include/exclude: {url}", flush=True)
                continue

            try:
                drv.get(url)
                if args.wait_selector:
                    # simple espera activa
                    time.sleep(args.render_wait_ms / 1000.0)
                if args.scroll:
                    for _ in range(int(args.scroll_steps or 4)):
                        drv.execute_script("window.scrollBy(0, document.body.scrollHeight / 4);")
                        time.sleep(args.scroll_wait_ms / 1000.0)

                html = drv.page_source or ""
                if dump_html:
                    raw_path = save_raw(run_dir, i, url, html.encode("utf-8", errors="ignore"))
                else:
                    raw_path = None

                # extraer enlaces (con bs4 si está; si no, parser HTML nativo)
                try:
                    soup = BeautifulSoup(html, "html.parser")
                    for a in soup.find_all("a", href=True):
                        u = urljoin(url, a["href"])
                        if u not in seen and len(out) + len(to_visit) < max_pages:
                            to_visit.append(u)
                except Exception:
                    # fallback sin bs4
                    parser = LinkExtractor(url)
                    parser.feed(html)
                    for u in parser.links:
                        if u not in seen and len(out) + len(to_visit) < max_pages:
                            to_visit.append(u)

                out.append({"url": url, "status": 200, "raw": raw_path, "title": None})
                i += 1
            except Exception as e:
                print(f"[ERR] SELENIUM GET {url}: {e}", flush=True)
                out.append({"url": url, "status": 0, "raw": None, "title": None})
    finally:
        try:
            drv.quit()
        except Exception:
            pass

    return out

# ----------------- main ----------------- #

def main():
    args = parse_args()

    # Crear run_dir y anunciarlo para la UI
    run_dir = ensure_run_dir()

    # Normalizar parámetros
    seed = normalize_url(args.seed.strip(), args.force_https)
    allowed_hosts = {h.strip().lower() for h in (args.allowed_domains or "").split(",") if h.strip()}
    includes = list(args.include or [])
    excludes = list(args.exclude or [])
    ignore_robots_for = {h.strip().lower() for h in (args.ignore_robots_for or "").split(",") if h.strip()}
    sess = session(args.user_agent, args.timeout)

    print(f"[INFO] strategy={args.strategy} seed={seed}", flush=True)

    pages: List[Dict] = []

    if args.strategy == "sitemap":
        pages = [{"url": u, "status": 0, "raw": None, "title": None} for u in fetch_sitemap(seed, sess, args.max_pages)]
        # descargar cada URL (simple GET)
        i = 0
        for page in pages:
            url = page["url"]
            if not allowed(url, allowed_hosts, includes, excludes):
                continue
            try:
                r = sess.get(url, timeout=sess.timeout)
                page["status"] = r.status_code
                if args.dump_html and r.ok:
                    page["raw"] = save_raw(run_dir, i, url, r.content)
            except Exception as e:
                print(f"[ERR] GET {url}: {e}", flush=True)
            i += 1

    elif args.strategy == "requests":
        pages = crawl_requests(seed, sess, allowed_hosts, includes, excludes, args.depth, args.max_pages, run_dir, args.dump_html)

    elif args.strategy == "selenium":
        pages = crawl_selenium(seed, args, allowed_hosts, includes, excludes, args.max_pages, run_dir, args.dump_html)

    # Guardar fetch_index.json
    (run_dir / "fetch_index.json").write_text(json.dumps(pages, ensure_ascii=False, indent=2), encoding="utf-8")

    # Dump rápido para preview
    print(f"[INFO] fetched={len(pages)}", flush=True)
    if args.preview:
        for p in pages[:10]:
            print(f"[PAGE] {p['status']} {p['url']}", flush=True)
# -*- coding: utf-8 -*-
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path
from typing import List, Dict, Set
from urllib.parse import urljoin, urlparse

import re
import requests

# --- Selenium opcional (solo si strategy=selenium) ---
try:
    from selenium import webdriver
    from selenium.webdriver.chrome.options import Options as ChromeOptions
    from selenium.webdriver.firefox.options import Options as FirefoxOptions
except Exception:
    webdriver = None  # lo detectamos en runtime


def parse_args():
    p = argparse.ArgumentParser("ingest_web")
    p.add_argument("--seed", required=True, help="URL base o sitemap")
    p.add_argument("--strategy", choices=["sitemap", "requests", "selenium"], default="sitemap")
    p.add_argument("--allowed-domains", default="", help="coma-separated hostnames permitidos")
    p.add_argument("--include", action="append", default=[], help="patrones substring a incluir (repetible)")
    p.add_argument("--exclude", action="append", default=[], help="patrones substring a excluir (repetible)")
    p.add_argument("--depth", type=int, default=1, help="Profundidad para strategy=requests")
    p.add_argument("--max-pages", type=int, default=100)
    p.add_argument("--timeout", type=int, default=15)
    p.add_argument("--rate", type=float, default=1.0, help="segundos entre hosts (no usado en simple)")
    p.add_argument("--user-agent", default="Mozilla/5.0")
    p.add_argument("--robots-policy", default="strict", choices=["strict", "list"])
    p.add_argument("--ignore-robots-for", default="", help="coma-separated hostnames para ignorar robots")
    p.add_argument("--no-robots", action="store_true", help="equivalente a ignorar robots")
    p.add_argument("--force-https", action="store_true")
    p.add_argument("--dump-html", action="store_true")
    p.add_argument("--preview", action="store_true")
    p.add_argument("--verbose", action="store_true")

    # Selenium CFG (si toca)
    p.add_argument("--driver", default="chrome")
    p.add_argument("--window-size", default="1366,900")
    p.add_argument("--render-wait-ms", type=int, default=3000)
    p.add_argument("--wait-selector", default="")
    p.add_argument("--no-headless", action="store_true")
    p.add_argument("--scroll", action="store_true")
    p.add_argument("--scroll-steps", type=int, default=4)
    p.add_argument("--scroll-wait-ms", type=int, default=500)
    return p.parse_args()


def normalize_url(u: str, force_https: bool) -> str:
    if force_https:
        pr = urlparse(u)
        if pr.scheme == "http":
            return "https://" + u[len("http://"):]
    return u


def allowed(url: str, allowed_hosts: Set[str], includes: List[str], excludes: List[str]) -> bool:
    h = urlparse(url).netloc.lower()
    if allowed_hosts and h not in allowed_hosts:
        return False
    if includes and not any(s in url for s in includes):
        return False
    if excludes and any(s in url for s in excludes):
        return False
    return True


def ensure_run_dir() -> Path:
    runs_root = Path("data/processed/runs/web")
    runs_root.mkdir(parents=True, exist_ok=True)
    rid = int(time.time())
    d = runs_root / f"run_{rid}"
    d.mkdir(parents=True, exist_ok=True)
    print(f"[RUN_DIR] {d}", flush=True)
    return d


def session(user_agent: str, timeout: int) -> requests.Session:
    s = requests.Session()
    s.headers["User-Agent"] = user_agent
    s.timeout = timeout  # atributo ad hoc que usamos en .get(...)
    return s


def save_raw(run_dir: Path, i: int, url: str, content: bytes) -> str:
    raw_dir = run_dir / "raw"
    raw_dir.mkdir(parents=True, exist_ok=True)
    fname = f"{i:05d}.html"
    path = raw_dir / fname
    path.write_bytes(content)
    return str(path)


def fetch_sitemap(seed: str, sess: requests.Session, max_pages: int) -> List[str]:
    urls: List[str] = []
    try:
        r = sess.get(seed, timeout=sess.timeout)
        r.raise_for_status()
        text = r.text
    except Exception as e:
        print(f"[WARN] sitemap GET failed: {e}", flush=True)
        return urls

    locs = re.findall(r"<loc>\s*(.*?)\s*</loc>", text, flags=re.IGNORECASE)
    for u in locs:
        u = u.strip()
        if u.endswith(".xml") and "sitemap" in u.lower():
            try:
                r2 = sess.get(u, timeout=sess.timeout)
                r2.raise_for_status()
                locs2 = re.findall(r"<loc>\s*(.*?)\s*</loc>", r2.text, flags=re.IGNORECASE)
                for u2 in locs2:
                    u2 = u2.strip()
                    if not u2.endswith(".xml"):
                        urls.append(u2)
                        if len(urls) >= max_pages:
                            return urls
            except Exception:
                continue
        else:
            urls.append(u)
            if len(urls) >= max_pages:
                return urls
    return urls[:max_pages]


from html.parser import HTMLParser
class LinkExtractor(HTMLParser):
    def __init__(self, base: str):
        super().__init__()
        self.base = base
        self.links: Set[str] = set()
    def handle_starttag(self, tag, attrs):
        if tag.lower() != "a":
            return
        href = None
        for k, v in attrs:
            if k.lower() == "href":
                href = v
                break
        if not href:
            return
        try:
            u = urljoin(self.base, href)
            self.links.add(u)
        except Exception:
            pass


def crawl_requests(seed: str, sess: requests.Session, allowed_hosts: Set[str],
                   includes: List[str], excludes: List[str], depth: int,
                   max_pages: int, run_dir: Path, dump_html: bool) -> List[Dict]:
    from collections import deque
    seen: Set[str] = set()
    q = deque([(seed, 0)])
    out: List[Dict] = []
    i = 0

    while q and len(out) < max_pages:
        url, d = q.popleft()
        if url in seen:
            continue
        seen.add(url)

        if not allowed(url, allowed_hosts, includes, excludes):
            print(f"[SKIP] domain/include/exclude: {url}", flush=True)
            continue

        resp = None
        try:
            resp = sess.get(url, timeout=sess.timeout)
            status = resp.status_code
            raw_path = None
            if dump_html and resp.ok and resp.content:
                raw_path = save_raw(run_dir, i, url, resp.content)
            out.append({"url": url, "status": status, "raw": raw_path, "title": None})
            i += 1
        except Exception as e:
            print(f"[ERR] GET {url}: {e}", flush=True)
            out.append({"url": url, "status": 0, "raw": None, "title": None})

        if d < depth:
            try:
                html = resp.text if resp is not None else ""
                parser = LinkExtractor(url)
                parser.feed(html or "")
                for u in parser.links:
                    if u not in seen and len(out) + len(q) < max_pages:
                        q.append((u, d + 1))
            except Exception:
                pass

    return out


def make_driver(args):
    if webdriver is None:
        raise RuntimeError("selenium no está instalado. pip install selenium>=4.12")

    driver_name = (args.driver or "chrome").lower()
    w, h = (int(x) for x in (args.window_size or "1366,900").split(",")[:2])
    headless = not bool(args.no_headless)

    if driver_name == "firefox":
        opts = FirefoxOptions()
        if headless:
            opts.add_argument("-headless")
        drv = webdriver.Firefox(options=opts)
    else:
        opts = ChromeOptions()
        if headless:
            opts.add_argument("--headless=new")
        opts.add_argument(f"--window-size={w},{h}")
        opts.add_argument("--disable-gpu")
        opts.add_argument("--no-sandbox")
        drv = webdriver.Chrome(options=opts)

    try:
        drv.set_page_load_timeout(int(args.timeout or 15))
    except Exception:
        pass
    drv.set_window_size(w, h)
    return drv


def crawl_selenium(seed: str, args, allowed_hosts: Set[str],
                   includes: List[str], excludes: List[str],
                   max_pages: int, run_dir: Path, dump_html: bool) -> List[Dict]:
    try:
        from bs4 import BeautifulSoup  # opcional
    except Exception:
        BeautifulSoup = None

    drv = make_driver(args)
    out: List[Dict] = []
    i = 0

    try:
        to_visit = [seed]
        seen: Set[str] = set()

        while to_visit and len(out) < max_pages:
            url = to_visit.pop(0)
            if url in seen:
                continue
            seen.add(url
            )
            if not allowed(url, allowed_hosts, includes, excludes):
                print(f"[SKIP] domain/include/exclude: {url}", flush=True)
                continue

            try:
                drv.get(url)
                # espera simple
                if args.render_wait_ms:
                    time.sleep(args.render_wait_ms / 1000.0)
                if args.scroll:
                    for _ in range(int(args.scroll_steps or 4)):
                        drv.execute_script("window.scrollBy(0, document.body.scrollHeight / 4);")
                        time.sleep(args.scroll_wait_ms / 1000.0)

                html = drv.page_source or ""
                if dump_html:
                    raw_path = save_raw(run_dir, i, url, html.encode("utf-8", errors="ignore"))
                else:
                    raw_path = None

                # enlaces
                if BeautifulSoup:
                    try:
                        soup = BeautifulSoup(html, "html.parser")
                        for a in soup.find_all("a", href=True):
                            u = urljoin(url, a["href"])
                            if u not in seen and len(out) + len(to_visit) < max_pages:
                                to_visit.append(u)
                    except Exception:
                        pass
                else:
                    parser = LinkExtractor(url)
                    parser.feed(html)
                    for u in parser.links:
                        if u not in seen and len(out) + len(to_visit) < max_pages:
                            to_visit.append(u)

                out.append({"url": url, "status": 200, "raw": raw_path, "title": None})
                i += 1
            except Exception as e:
                print(f"[ERR] SELENIUM GET {url}: {e}", flush=True)
                out.append({"url": url, "status": 0, "raw": None, "title": None})
    finally:
        try:
            drv.quit()
        except Exception:
            pass

    return out


def main():
    args = parse_args()
    run_dir = ensure_run_dir()

    seed = normalize_url(args.seed.strip(), args.force_https)
    allowed_hosts = {h.strip().lower() for h in (args.allowed_domains or "").split(",") if h.strip()}
    includes = list(args.include or [])
    excludes = list(args.exclude or [])
    # Nota: robots no se aplica en este crawler simple

    sess = session(args.user_agent, args.timeout)

    print(f"[INFO] strategy={args.strategy} seed={seed}", flush=True)
    pages: List[Dict] = []

    if args.strategy == "sitemap":
        pages = [{"url": u, "status": 0, "raw": None, "title": None}
                 for u in fetch_sitemap(seed, sess, args.max_pages)]
        i = 0
        for page in pages:
            url = page["url"]
            if not allowed(url, allowed_hosts, includes, excludes):
                continue
            try:
                r = sess.get(url, timeout=sess.timeout)
                page["status"] = r.status_code
                if args.dump_html and r.ok:
                    page["raw"] = save_raw(run_dir, i, url, r.content)
            except Exception as e:
                print(f"[ERR] GET {url}: {e}", flush=True)
            i += 1

    elif args.strategy == "requests":
        pages = crawl_requests(seed, sess, allowed_hosts, includes, excludes,
                               args.depth, args.max_pages, run_dir, args.dump_html)

    elif args.strategy == "selenium":
        pages = crawl_selenium(seed, args, allowed_hosts, includes, excludes,
                               args.max_pages, run_dir, args.dump_html)

    # Artefactos base
    (run_dir / "fetch_index.json").write_text(
        json.dumps(pages, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    print(f"[INFO] fetched={len(pages)}", flush=True)
    if args.preview:
        for p in pages[:10]:
            print(f"[PAGE] {p['status']} {p['url']}", flush=True)


if __name__ == "__main__":
    try:
        main()
        sys.exit(0)
    except Exception as e:
        print(f"[FATAL] {type(e).__name__}: {e}", flush=True)
        sys.exit(1)

if __name__ == "__main__":
    try:
        main()
        sys.exit(0)
    except Exception as e:
        print(f"[FATAL] {type(e).__name__}: {e}", flush=True)
        sys.exit(1)
