import os, sys, json, time, argparse, traceback
from pathlib import Path
from datetime import datetime, timezone
from collections import defaultdict
from types import SimpleNamespace
from urllib.parse import urlparse, urljoin

from app import create_app
from app.extensions.db import get_session
from app.models import Source, IngestionRun, Document, Chunk

# Scrapers
from app.rag.scrapers.requests_bs4 import ScrapeConfig, RequestsBS4Scraper
from app.rag.scrapers.selenium_fetcher import SeleniumScraper, SeleniumOptions
from app.rag.scrapers.sitemap import discover_sitemaps_from_robots, collect_all_pages
from app.rag.scrapers.web_normalizer import html_to_text

from bs4 import BeautifulSoup
import requests

NON_HTML_PREFIXES = (
    "application/pdf", "application/msword", "application/vnd",
    "image/", "audio/", "video/", "application/zip", "application/octet-stream"
)

def now_utc():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")

def build_parser():
    p = argparse.ArgumentParser()
    p.add_argument("--seed", required=True)
    p.add_argument("--strategy", choices=["requests", "selenium", "sitemap"], default="requests")
    p.add_argument("--source-id", type=int, required=True)
    p.add_argument("--run-id", type=int, required=True)

    # Crawling
    p.add_argument("--depth", type=int, default=1)
    p.add_argument("--max-pages", type=int, default=20)
    p.add_argument("--timeout", type=int, default=15)
    p.add_argument("--rate-per-host", type=float, default=1.0)
    p.add_argument("--user-agent", default="Mozilla/5.0")
    p.add_argument("--allowed-domains", default="", help="coma separated")
    p.add_argument("--include", nargs="*", default=[])
    p.add_argument("--exclude", nargs="*", default=[])
    p.add_argument("--robots-policy", default="strict")
    p.add_argument("--force-https", action="store_true")

    # Selenium
    p.add_argument("--driver", default="chrome")
    p.add_argument("--no-headless", action="store_true")
    p.add_argument("--render-wait-ms", type=int, default=3000)
    p.add_argument("--scroll", action="store_true")
    p.add_argument("--scroll-steps", type=int, default=4)
    p.add_argument("--scroll-wait-ms", type=int, default=500)
    p.add_argument("--wait-selector", default="")
    p.add_argument("--window-size", default="1366,900")

    # Advanced fallbacks
    p.add_argument("--iframe-max", type=int, default=2, help="máximo de iframes a seguir si no hay texto")
    return p

def domain_allowed(url: str, allowed_domains: list[str]):
    host = urlparse(url).netloc.lower()
    return any(host.endswith(d.lower()) for d in allowed_domains) if allowed_domains else True

def fetch_iframe_texts(html: str, base_url: str, cfg: ScrapeConfig, counters: dict, limit: int = 2) -> str:
    """
    Si la página no tiene texto útil, intenta seguir hasta `limit` iframes del mismo dominio permitido
    y concatena su texto (solo text/html).
    """
    soup = BeautifulSoup(html or "", "html.parser")
    frames = soup.find_all("iframe", src=True)[:limit]
    texts = []
    for fr in frames:
        src = fr.get("src")
        if not src:
            continue
        url = urljoin(base_url, src)
        if not domain_allowed(url, cfg.allowed_domains):
            counters["iframe_skipped_domain"] += 1
            continue
        try:
            r = requests.get(url, timeout=cfg.timeout_seconds, headers={"User-Agent": cfg.user_agent})
            if not r.ok:
                counters["iframe_fetch_error"] += 1
                continue
            ct = (r.headers.get("Content-Type", "") or "").lower()
            if any(ct.startswith(prefix) for prefix in NON_HTML_PREFIXES):
                counters["iframe_skipped_non_html"] += 1
                continue
            t = html_to_text(r.text or "")
            if t.strip():
                texts.append(t)
                counters["iframe_fetched"] += 1
        except Exception:
            counters["iframe_fetch_error"] += 1
            continue
    return "\n\n".join(texts).strip()

def main():
    args = build_parser().parse_args()
    app = create_app()

    run_dir = Path(f"data/processed/runs/web/run_{args.run_id}")
    raw_dir = run_dir / "raw"
    raw_dir.mkdir(parents=True, exist_ok=True)
    stdout_path = run_dir / "stdout.txt"
    summary_path = run_dir / "summary.json"
    fetch_index_path = run_dir / "fetch_index.json"

    def log(msg):
        line = f"[{now_utc()}] {msg}"
        print(line)
        with open(stdout_path, "a", encoding="utf-8") as f:
            f.write(line + "\n")

    cfg = ScrapeConfig(
        seeds=[args.seed],
        allowed_domains=[d.strip() for d in args.allowed_domains.split(",") if d.strip()] if args.allowed_domains else [],
        include_url_patterns=args.include,
        exclude_url_patterns=args.exclude,
        force_https=args.force_https,
        user_agent=args.user_agent,
        timeout_seconds=args.timeout,
        max_pages=args.max_pages,
        depth=args.depth,
        rate_limit_per_host=args.rate_per_host,
        robots_policy=args.robots_policy
    )

    counters = defaultdict(int)
    app_created = False
    try:
        with app.app_context():
            with get_session() as s:
                run = s.get(IngestionRun, args.run_id)
                if run:
                    run.status = "running"
                    s.add(run)
                    s.commit()

            log(f"[RUN_DIR] {run_dir}")
            log(f"App creada — run_id={args.run_id} source_id={args.source_id}")
            app_created = True

            # Ejecutar estrategia
            if args.strategy == "requests":
                pages = RequestsBS4Scraper(cfg).crawl()

            elif args.strategy == "selenium":
                options = SeleniumOptions(
                    driver=args.driver,
                    headless=not args.no_headless,
                    render_wait_ms=args.render_wait_ms,
                    scroll=args.scroll,
                    scroll_steps=args.scroll_steps,
                    scroll_wait_ms=args.scroll_wait_ms,
                    wait_selector=args.wait_selector,
                    window_size=args.window_size
                )
                pages = SeleniumScraper(cfg, options).crawl()

            elif args.strategy == "sitemap":
                # Descubre los sitemaps a partir de la seed y colecciona URLs
                try:
                    sitemaps = discover_sitemaps_from_robots(
                        args.seed,
                        force_https=cfg.force_https,
                        user_agent=cfg.user_agent
                    )
                    urls, _ = collect_all_pages(
                        sitemaps,
                        force_https=cfg.force_https,
                        user_agent=cfg.user_agent
                    )
                except Exception as e:
                    log(f"[error] sitemap.discovery: {e}")
                    urls = []

                pages = []
                for url in urls[: cfg.max_pages]:
                    try:
                        resp = requests.get(
                            url,
                            timeout=cfg.timeout_seconds,
                            headers={"User-Agent": cfg.user_agent}
                        )
                        resp.raise_for_status()
                        ct = (resp.headers.get("Content-Type", "") or "").lower()
                        # Salta contenido no HTML (pdf, imagen, binarios, etc.)
                        if any(ct.startswith(prefix) for prefix in NON_HTML_PREFIXES):
                            counters["non_html_skipped"] += 1
                            log(f"[skip] sitemap.non_html url={url} ct={ct}")
                            continue
                        pages.append(SimpleNamespace(
                            url=url,
                            html=(resp.text or ""),
                            status_code=resp.status_code
                        ))
                    except requests.HTTPError as e:
                        status = getattr(e.response, "status_code", None)
                        # Fallback: si la URL es http:// y terminamos en 404 tras redirección, probamos sin redirigir (HTTP plano)
                        if status == 404 and url.startswith("http://"):
                            try:
                                r2 = requests.get(
                                    url,
                                    timeout=cfg.timeout_seconds,
                                    headers={"User-Agent": cfg.user_agent},
                                    allow_redirects=False
                                )
                                if r2.ok:
                                    ct2 = (r2.headers.get("Content-Type", "") or "").lower()
                                    if not any(ct2.startswith(prefix) for prefix in NON_HTML_PREFIXES):
                                        pages.append(SimpleNamespace(
                                            url=url,
                                            html=(r2.text or ""),
                                            status_code=r2.status_code
                                        ))
                                        counters["http_fallback_ok"] += 1
                                        log(f"[info] sitemap.http_fallback.ok url={url} ct={ct2}")
                                        continue
                            except Exception as e2:
                                log(f"[warn] sitemap.http_fallback.error url={url} err={e2}")
                        counters["fetch_404" if status == 404 else "fetch_http_error"] += 1
                        log(f"[warn] sitemap.fetch.error url={url} err={e}")
                        continue
                    except Exception as e:
                        counters["fetch_error"] += 1
                        log(f"[warn] sitemap.fetch.error url={url} err={e}")
                        continue
            else:
                raise ValueError(f"Estrategia desconocida: {args.strategy}")

            # Procesar páginas → Document + Chunk
            total_chunks, total_bytes, total_pages = 0, 0, 0
            fetch_info = []

            with get_session() as s:
                for i, p in enumerate(pages, 1):
                    url = getattr(p, "url", None)
                    html = getattr(p, "html", "") or ""

                    if not url:
                        log(f"[skip] Página sin URL en índice {i}")
                        continue

                    if not html.strip():
                        log(f"[skip] Página vacía: {url}")
                        counters["empty_html"] += 1
                        continue

                    try:
                        raw_path = raw_dir / f"page_{i}.html"
                        raw_path.write_text(html, encoding="utf-8")
                    except Exception as e:
                        log(f"[error] No se pudo guardar raw: {e}")
                        counters["raw_write_error"] += 1
                        continue

                    try:
                        b = len(html.encode("utf-8"))
                        doc = Document(
                            source_id=args.source_id,
                            path=url,
                            title=url,
                            size=b,
                            meta={
                                "fetched_at": now_utc(),
                                "run_id": args.run_id
                            }
                        )
                        s.add(doc)
                        s.flush()

                        text = html_to_text(html)

                        # Fallback: si no hay texto, intenta seguir iframes del mismo dominio permitido
                        if not text.strip():
                            iframe_text = fetch_iframe_texts(html, url, cfg, counters, limit=args.iframe_max)
                            if iframe_text:
                                text = iframe_text
                                counters["used_iframe_text"] += 1

                        if not text.strip():
                            log(f"[skip] Sin texto extraíble: {url}")
                            counters["no_text"] += 1
                            s.rollback()
                            continue

                        chunks = [text[i:i+1000] for i in range(0, len(text), 800)]

                        for j, chunk in enumerate(chunks, 1):
                            c = Chunk(
                                source_id=args.source_id,
                                document_id=doc.id,
                                ordinal=j,
                                text=chunk,
                                content=chunk,
                                meta={"from": args.strategy, "run_id": args.run_id}
                            )
                            s.add(c)

                        s.commit()

                        total_chunks += len(chunks)
                        total_bytes += b
                        total_pages += 1

                        fetch_info.append({
                            "url": url,
                            "status": getattr(p, "status_code", None),
                            "bytes": b,
                            "chunks": len(chunks),
                            "raw": str(raw_path)
                        })

                    except Exception as e:
                        log(f"[error] procesando {url}: {e}")
                        counters["process_error"] += 1
                        s.rollback()

                # Guardar summary
                summary = {
                    "run_dir": str(run_dir),
                    "totals": {
                        "pages": total_pages,
                        "chunks": total_chunks,
                        "bytes": total_bytes
                    },
                    "counters": dict(counters),
                    "pages": fetch_info,
                    "finished_at": now_utc()
                }

                fetch_index_path.write_text(json.dumps(fetch_info, indent=2, ensure_ascii=False), encoding="utf-8")
                summary_path.write_text(json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8")

                run = s.get(IngestionRun, args.run_id)
                if run:
                    run.status = "done" if total_pages > 0 else "error"
                    run.meta = run.meta or {}
                    run.meta.update({
                        "run_dir": str(run_dir),
                        "summary_totals": summary["totals"],
                        "summary_counters": summary["counters"]
                    })
                    s.add(run)
                    s.commit()

                log(f"✅ OK pages={total_pages} chunks={total_chunks} bytes={total_bytes}")
                return 0 if total_pages > 0 else 2

    except Exception as e:
        log(f"[fatal] {e}")
        if app_created:
            log(traceback.format_exc())
        return 1

if __name__ == "__main__":
    raise SystemExit(main())