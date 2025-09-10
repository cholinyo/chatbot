#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os, sys, json, time, argparse, logging, traceback
from pathlib import Path
from dataclasses import dataclass, asdict
from types import SimpleNamespace
from typing import List, Dict

# ---------------------------------------------------------------------
# Logging con protección cp1252 (Windows)
# ---------------------------------------------------------------------
logger = logging.getLogger("ingest_web")
logger.setLevel(logging.INFO)
handler = logging.StreamHandler(sys.stdout)

class SafeFormatter(logging.Formatter):
    def format(self, record):
        try:
            return super().format(record)
        except UnicodeEncodeError:
            record.msg = str(record.msg).encode("utf-8", "ignore").decode("utf-8", "ignore")
            return super().format(record)

handler.setFormatter(SafeFormatter("%(message)s"))
logger.addHandler(handler)

def log(msg: str):
    logger.info(str(msg).replace("→", "->"))

# ---------------------------------------------------------------------
# Import de estrategias especializadas (requests / selenium / sitemap)
# - Soporta distintos layouts de paquete con imports tolerantes.
# ---------------------------------------------------------------------
collect_requests = collect_selenium = collect_sitemap = None

def _try_import_strategies():
    global collect_requests, collect_selenium, collect_sitemap
    paths_tried = []

    def _try_one(stmt: str):
        nonlocal paths_tried
        try:
            ns = {}
            exec(stmt, ns, ns)
            return ns
        except Exception as e:
            paths_tried.append((stmt, repr(e)))
            return None

    # Intento 1: paquete absoluto 'scripts.ingest'
    ns = _try_one("from scripts.ingest.web_strategy_requests import collect_pages as CR; "
                  "from scripts.ingest.web_strategy_selenium import collect_pages as CS; "
                  "from scripts.ingest.web_strategy_sitemap import collect_pages as CM")
    if ns:
        collect_requests, collect_selenium, collect_sitemap = ns["CR"], ns["CS"], ns["CM"]
        return

    # Intento 2: relativo al directorio 'scripts' (cuando se ejecuta como script)
    sys.path.append(str(Path(__file__).resolve().parent.parent))  # add repo root
    ns = _try_one("from ingest.web_strategy_requests import collect_pages as CR; "
                  "from ingest.web_strategy_selenium import collect_pages as CS; "
                  "from ingest.web_strategy_sitemap import collect_pages as CM")
    if ns:
        collect_requests, collect_selenium, collect_sitemap = ns["CR"], ns["CS"], ns["CM"]
        return

    # Si seguimos aquí, dejamos constancia para debugging
    log(f"[WARN] No se pudieron importar estrategias: {paths_tried}")

# ---------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------
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
    # Selenium
    p.add_argument("--driver", default="chrome")
    p.add_argument("--no-headless", action="store_true")
    p.add_argument("--render-wait-ms", type=int, default=2500)
    p.add_argument("--window-size", default="1366,900")
    p.add_argument("--wait-selector", default="")
    p.add_argument("--scroll", nargs="*")
    # RUN_DIR (inyectado por la ruta)
    p.add_argument("--run-dir", default=os.environ.get("RUN_DIR", ""))
    return p

def split_csv(s: str):
    return [x.strip() for x in s.split(",") if x.strip()] if s else []

def ensure_run_dirs(run_dir: Path):
    (run_dir / "raw").mkdir(parents=True, exist_ok=True)

# ---------------------------------------------------------------------
# Artefactos (contrato con la plantilla/rutas)
# ---------------------------------------------------------------------
@dataclass
class PageArtifact:
    url: str
    path: str
    status: int
    bytes: int

def write_artifacts(run_dir: Path, pages: List[SimpleNamespace]) -> Dict:
    ensure_run_dirs(run_dir)
    fetch_index = []
    total_bytes = 0

    for i, p in enumerate(pages, 1):
        fn = f"{i:05d}.html"
        raw_path = run_dir / "raw" / fn
        try:
            html = p.html if hasattr(p, "html") else (p.content if hasattr(p, "content") else "")
            if not isinstance(html, str):
                html = str(html or "")
        except Exception:
            html = ""
        raw_path.write_text(html, encoding="utf-8", errors="ignore")
        size = raw_path.stat().st_size
        total_bytes += size
        fetch_index.append(
            PageArtifact(
                url=getattr(p, "url", ""),
                path=f"raw/{fn}",
                status=int(getattr(p, "status_code", 0) or 0),
                bytes=size,
            ).__dict__
        )

    (run_dir / "fetch_index.json").write_text(
        json.dumps(fetch_index, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    summary = {
        "seed": getattr(pages[0], "url", "") if pages else "",
        "n_pages": len(pages),
        "bytes": total_bytes,
        "totals": {"pages": len(pages), "bytes": total_bytes},
    }
    (run_dir / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    # stdout.txt con una línea por página (ayuda a tu Preview)
    lines = []
    for i, _p in enumerate(pages):
        rec = PageArtifact(
            url=getattr(_p, "url", ""),
            path=fetch_index[i]["path"],
            status=int(getattr(_p, "status_code", 0) or 0),
            bytes=fetch_index[i]["bytes"],
        )
        lines.append(f"{i+1:05d} {asdict(rec)}")
    (run_dir / "stdout.txt").write_text("\n".join(lines), encoding="utf-8")

    return summary

# ---------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------
def main():
    _try_import_strategies()
    args = build_parser().parse_args()

    run_dir = Path(args.run_dir or f"data/processed/runs/web/run_{args.run_id}")
    ensure_run_dirs(run_dir)
    log(f"[RUN_DIR] {run_dir}")

    allowed_domains = split_csv(args.allowed_domains)
    include = split_csv(args.include)
    exclude = split_csv(args.exclude)

    # Config compacta que los especialistas pueden usar
    cfg = SimpleNamespace(
        seed=args.seed,
        allowed_domains=allowed_domains,
        include=include,
        exclude=exclude,
        user_agent=args.user_agent,
        timeout=args.timeout,
        max_pages=args.max_pages,
        depth=args.depth,
        force_https=bool(args.force_https),
        rate_per_host=float(args.rate_per_host),
        robots_policy=args.robots_policy,
        # Selenium (por si lo usa el especialista)
        driver=args.driver,
        headless=not args.no_headless,
        render_wait_ms=args.render_wait_ms,
        window_size=args.window_size,
        wait_selector=args.wait_selector,
        scroll=args.scroll,
    )

    log(f"Strategy: {args.strategy}")

    counters: Dict[str, int] = {}
    pages: List[SimpleNamespace] = []

    try:
        if args.strategy == "requests":
            if collect_requests is None:
                raise RuntimeError("Especialista 'requests' no disponible")
            pages = collect_requests(cfg, args, log, counters)
        elif args.strategy == "sitemap":
            if collect_sitemap is None:
                raise RuntimeError("Especialista 'sitemap' no disponible")
            pages = collect_sitemap(cfg, args, log, counters)
        elif args.strategy == "selenium":
            if collect_selenium is None:
                raise RuntimeError("Especialista 'selenium' no disponible")
            pages = collect_selenium(cfg, args, log, counters)
        else:
            raise ValueError(f"Estrategia no soportada: {args.strategy}")
    except Exception as e:
        log(f"ERROR ejecutando estrategia {args.strategy}: {e}")
        traceback.print_exc()
        # Fallback → requests
        if args.strategy != "requests":
            log("Fallback -> requests")
            try:
                if collect_requests is None:
                    raise RuntimeError("Especialista 'requests' no disponible")
                pages = collect_requests(cfg, args, log, counters)
            except Exception as e2:
                log(f"Fallback también falló: {e2}")
                pages = []

    summary = write_artifacts(run_dir, pages)
    log(f"FIN: páginas={summary['n_pages']} bytes={summary['bytes']}")
    return 0

if __name__ == "__main__":
    sys.exit(main())
