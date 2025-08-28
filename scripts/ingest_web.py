# scripts/ingest_web.py
from __future__ import annotations

# --- make project root importable ---
import sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
# ------------------------------------

import argparse
import logging
from pathlib import Path
from datetime import datetime, timezone
from typing import List, Set, Optional
import json

from app.rag.scrapers.requests_bs4 import ScrapeConfig, RequestsBS4Scraper
from app.rag.scrapers.web_normalizer import html_to_text, NormalizeConfig
from app.rag.scrapers.sitemap import discover_sitemaps_from_robots, collect_all_pages

logger = logging.getLogger("ingestion.web.cli")


def _setup_logging(verbose: bool) -> None:
    logging.basicConfig(
        level=logging.INFO if verbose else logging.WARNING,
        format="%(asctime)s %(levelname)s %(name)s :: %(message)s",
    )


def _parse_domains_csv(s: str) -> Optional[Set[str]]:
    items = {d.strip().lower() for d in (s or "").split(",") if d.strip()}
    return items or None


def _ensure_run_dirs(root: Path) -> None:
    (root / "raw").mkdir(parents=True, exist_ok=True)


def main(argv: List[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Ingesta Web (requests+BS4) con strategy 'requests' o 'sitemap'")
    p.add_argument("--seed", action="append", required=True, help="URL semilla base (repetible). Para sitemap, usa la home del dominio.")
    p.add_argument("--strategy", choices=["requests", "sitemap"], default="requests", help="Estrategia de descubrimiento")
    p.add_argument("--depth", type=int, default=1, help="Profundidad BFS (solo strategy=requests)")
    p.add_argument("--allowed-domains", default="", help="Dominios permitidos (coma)")
    p.add_argument("--include", action="append", default=[], help="Patrón include (glob o regex). Repetible.")
    p.add_argument("--exclude", action="append", default=[], help="Patrón exclude (glob o regex). Repetible.")
    p.add_argument("--max-pages", type=int, default=50, help="Máximo de páginas a descargar")
    p.add_argument("--timeout", type=int, default=15, help="Timeout HTTP (seg)")
    p.add_argument("--rate", type=float, default=1.0, help="Rate-limit por host (req/seg)")
    p.add_argument("--force-https", action="store_true", help="Reescribe http:// → https:// en seeds y enlaces")

    # Compatibilidad previa
    p.add_argument("--no-robots", action="store_true", help="No respetar robots.txt (modo simple)")

    # Política granular de robots
    p.add_argument("--robots-policy", choices=["strict", "ignore", "list"], default=None,
                   help="strict=respeta; ignore=ignora global; list=ignora solo dominios indicados")
    p.add_argument("--ignore-robots-for", default="",
                   help="Dominios (coma) a los que ignorar robots cuando --robots-policy=list (e.g. 'onda.es,www.onda.es')")

    # User-Agent
    p.add_argument("--user-agent",
                   default="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36",
                   help="User-Agent para las peticiones HTTP")

    # Artefactos
    p.add_argument("--outdir", default="data/processed/runs", help="Carpeta raíz de artefactos")
    p.add_argument("--dump-html", action="store_true", help="Guardar HTML crudo en /raw y un índice JSON")
    p.add_argument("--preview", action="store_true", help="Imprime título y extracto de texto por consola")
    p.add_argument("--verbose", action="store_true", help="Logs INFO")

    args = p.parse_args(argv)
    _setup_logging(args.verbose)

    allowed = _parse_domains_csv(args.allowed_domains)
    ignore_set = _parse_domains_csv(args.ignore_robots_for)

    # Resolver política final (compat con --no-robots)
    robots_policy = args.robots_policy or ("ignore" if args.no_robots else "strict")

    # Directorio de ejecución
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    run_dir = Path(args.outdir) / f"web_{args.strategy}_{ts}"
    if args.dump_html:
        _ensure_run_dirs(run_dir)

    # <<< EMITIR run_dir PARA EL FRONT >>>
    print(f"[RUN_DIR]{run_dir}", flush=True)  # [RUN_DIR]

    count = 0
    index = []

    if args.strategy == "sitemap":
        # 1) Descubrir sitemaps (por cada seed)
        all_sitemaps = []
        for base in args.seed:
            seeds_sm = discover_sitemaps_from_robots(
                base_url=base,
                force_https=args.force_https,
                user_agent=args.user_agent,
            )
            all_sitemaps.extend(seeds_sm)

        # 2) Recoger todas las páginas (con soporte a sitemapindex recursivo)
        pages, visited_sitemaps = collect_all_pages(
            all_sitemaps,
            force_https=args.force_https,
            user_agent=args.user_agent,
        )

        # Guardar artefactos de descubrimiento
        (run_dir / "sitemap_index.json").write_text(
            json.dumps({"seeds": all_sitemaps, "visited": visited_sitemaps}, ensure_ascii=False, indent=2),
            encoding="utf-8"
        )
        (run_dir / "sitemap_pages.json").write_text(
            json.dumps({"pages": pages}, ensure_ascii=False, indent=2),
            encoding="utf-8"
        )

        # 3) Descargar HTML de esas páginas (sin seguir enlaces; depth 0)
        cfg = ScrapeConfig(
            seeds=[],
            depth=0,
            allowed_domains=allowed,
            include_url_patterns=args.include or None,
            exclude_url_patterns=args.exclude or None,
            respect_robots=(robots_policy != "ignore"),
            user_agent=args.user_agent,
            timeout_seconds=args.timeout,
            rate_limit_per_host=args.rate,
            max_pages=len(pages) if args.max_pages >= len(pages) else args.max_pages,
            force_https=args.force_https,
            robots_policy=robots_policy,
            ignore_robots_for=ignore_set,
        )
        scraper = RequestsBS4Scraper(cfg)

        for url in pages:
            if count >= args.max_pages:
                break
            page = scraper.fetch_url(url)
            if not page:
                continue
            count += 1

            if args.preview:
                try:
                    text = html_to_text(page.html, NormalizeConfig())
                except Exception:
                    text = ""
                print(f"\n=== [{count}] {page.url} ===")
                print((page.title or "").strip()[:200])
                if text:
                    print(text[:800] + ("…" if len(text) > 800 else ""))

            if args.dump_html:
                raw_path = run_dir / "raw" / f"page_{count:04d}.html"
                raw_path.write_text(page.html, encoding="utf-8")
                index.append(
                    {
                        "i": count,
                        "url": page.url,
                        "base_url": page.base_url,
                        "status": page.status_code,
                        "title": page.title,
                        "raw": str(raw_path),
                        "hash": page.origin_hash,
                        "links": page.links[:100],
                    }
                )

    else:
        # strategy == requests (BFS por enlaces)
        cfg = ScrapeConfig(
            seeds=args.seed,
            depth=args.depth,
            allowed_domains=allowed,
            include_url_patterns=args.include or None,
            exclude_url_patterns=args.exclude or None,
            respect_robots=(robots_policy != "ignore"),
            user_agent=args.user_agent,
            timeout_seconds=args.timeout,
            rate_limit_per_host=args.rate,
            max_pages=args.max_pages,
            force_https=args.force_https,
            robots_policy=robots_policy,
            ignore_robots_for=ignore_set,
        )
        scraper = RequestsBS4Scraper(cfg)

        for page in scraper.crawl():
            count += 1

            if args.preview:
                try:
                    text = html_to_text(page.html, NormalizeConfig())
                except Exception:
                    text = ""
                print(f"\n=== [{count}] {page.url} ===")
                print((page.title or "").strip()[:200])
                if text:
                    print(text[:800] + ("…" if len(text) > 800 else ""))

            if args.dump_html:
                raw_path = run_dir / "raw" / f"page_{count:04d}.html"
                raw_path.write_text(page.html, encoding="utf-8")
                index.append(
                    {
                        "i": count,
                        "url": page.url,
                        "base_url": page.base_url,
                        "status": page.status_code,
                        "title": page.title,
                        "raw": str(raw_path),
                        "hash": page.origin_hash,
                        "links": page.links[:100],
                    }
                )

    if args.dump_html:
        (run_dir / "fetch_index.json").write_text(
            json.dumps(index, ensure_ascii=False, indent=2), encoding="utf-8"
        )

    print(f"\nIngesta web finalizada. Páginas procesadas: {count}")
    # --- imprimir otra vez por si el front solo lee la última línea útil ---
    print(f"[RUN_DIR]{run_dir}", flush=True)  # [RUN_DIR]
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
