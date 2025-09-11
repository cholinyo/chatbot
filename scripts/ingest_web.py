#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os, sys, json, argparse, logging, traceback, importlib.util
from pathlib import Path
from dataclasses import dataclass
from types import SimpleNamespace
from typing import List, Dict
import io

# Extracción de texto en PDFs (para generar raw/NNNNN.pdf.txt)
try:
    from pypdf import PdfReader
except Exception:
    PdfReader = None


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
# - Robusto: intenta paquete y, si falla, carga por ruta de archivo.
# ---------------------------------------------------------------------
collect_requests = collect_selenium = collect_sitemap = None

def _load_from_file(mod_name: str, file_path: Path, attr: str = "collect_pages"):
    spec = importlib.util.spec_from_file_location(mod_name, str(file_path))
    if not spec or not spec.loader:
        raise ImportError(f"No se pudo crear spec para {file_path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[mod_name] = module
    spec.loader.exec_module(module)  # type: ignore[attr-defined]
    fn = getattr(module, attr, None)
    if not callable(fn):
        raise ImportError(f"{attr} no encontrado en {file_path.name}")
    return fn

def _try_import_strategies():
    """
    Carga collect_pages de:
      - scripts.ingest.web_strategy_requests
      - scripts.ingest.web_strategy_selenium
      - scripts.ingest.web_strategy_sitemap
    Si falla el import por paquete, carga por ruta de archivo.
    """
    global collect_requests, collect_selenium, collect_sitemap

    repo_root = Path(__file__).resolve().parent.parent  # .../ (raíz del repo)
    if str(repo_root) not in sys.path:
        sys.path.insert(0, str(repo_root))

    # Intento 1: paquete absoluto 'scripts.ingest'
    try:
        from scripts.ingest.web_strategy_requests import collect_pages as CR  # type: ignore
        from scripts.ingest.web_strategy_selenium import collect_pages as CS  # type: ignore
        from scripts.ingest.web_strategy_sitemap import collect_pages as CM   # type: ignore
        collect_requests, collect_selenium, collect_sitemap = CR, CS, CM
        return
    except Exception as e_pkg:
        # Intento 2: carga directa por ruta de archivo
        try:
            ingest_dir = Path(__file__).resolve().parent / "ingest"
            CR = _load_from_file("ingest_web_strategy_requests", ingest_dir / "web_strategy_requests.py")
            CS = _load_from_file("ingest_web_strategy_selenium", ingest_dir / "web_strategy_selenium.py")
            CM = _load_from_file("ingest_web_strategy_sitemap", ingest_dir / "web_strategy_sitemap.py")
            collect_requests, collect_selenium, collect_sitemap = CR, CS, CM
            return
        except Exception as e_file:
            log(f"[WARN] No se pudieron importar estrategias (paquete y archivo): {e_pkg!r} ; {e_file!r}")

def _debug_log_loaded_strategies():
    try:
        if collect_sitemap:
            log(f"[strategies] sitemap module: {getattr(collect_sitemap, '__module__', '?')}")
            log(f"[strategies] sitemap file  : {collect_sitemap.__code__.co_filename}")
        if collect_requests:
            log(f"[strategies] requests file : {collect_requests.__code__.co_filename}")
        if collect_selenium:
            log(f"[strategies] selenium file : {collect_selenium.__code__.co_filename}")
    except Exception as e:
        log(f"[strategies] debug failed: {e}")

def main():
    _try_import_strategies()
    _debug_log_loaded_strategies()
    args = build_parser().parse_args()
    ...

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
    p.add_argument("--include-pdfs", action="store_true", help="Permitir PDF (recomendado solo en sitemap)")
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
    lines_stdout = []

    def _guess_ext_and_binary(p: SimpleNamespace) -> tuple[str, bool]:
        # Import local: evita NameError si no está en la cabecera
        from urllib.parse import urlparse as _urlparse
        # prioridad: marca explícita -> cabecera -> URL
        if getattr(p, "ext", None):
            ext = str(p.ext).lower()
            return (ext if ext.startswith(".") else f".{ext}"), bool(getattr(p, "is_binary", False))
        ct = str(getattr(p, "headers", {}).get("Content-Type", "")).lower()
        if "application/pdf" in ct:
            return (".pdf", True)
        path = _urlparse(getattr(p, "url", "")).path.lower()
        if path.endswith(".pdf"):
            return (".pdf", True)
        return (".html", False)

    def _extract_pdf_text_bytes(pdf_bytes: bytes) -> str:
        if PdfReader is None or not pdf_bytes:
            return ""
        try:
            reader = PdfReader(io.BytesIO(pdf_bytes))
            parts = []
            for pg in reader.pages:
                try:
                    parts.append(pg.extract_text() or "")
                except Exception:
                    continue
            return "\n".join(parts).strip()
        except Exception:
            return ""

    for i, p in enumerate(pages, 1):
        ext, is_bin = _guess_ext_and_binary(p)
        fn = f"{i:05d}{ext}"
        raw_path = run_dir / "raw" / fn

        try:
            if is_bin:
                data = getattr(p, "content_bytes", None)
                if data is None:
                    content = getattr(p, "content", b"")
                    data = content if isinstance(content, (bytes, bytearray)) else bytes(str(content), "utf-8", errors="ignore")
                raw_path.write_bytes(data)

                # Si es PDF: solo intentamos extraer si parece PDF real (%PDF-)
                if ext == ".pdf":
                    if isinstance(data, (bytes, bytearray)) and data[:5] == b"%PDF-":
                        txt = _extract_pdf_text_bytes(data)
                        if txt:
                            (run_dir / "raw" / f"{i:05d}.pdf.txt").write_text(txt, encoding="utf-8", errors="ignore")
                    else:
                        # Evita warnings/errores de pypdf con falsos PDF
                        pass
            else:
                html = getattr(p, "content", "")
                if not isinstance(html, str):
                    try:
                        html = str(html)
                    except Exception:
                        html = ""
                raw_path.write_text(html, encoding="utf-8", errors="ignore")
        except Exception:
            continue  # sigue con la siguiente página aunque falle esta

        try:
            size = raw_path.stat().st_size
        except Exception:
            size = 0

        total_bytes += size
        rec = PageArtifact(
            url=getattr(p, "url", ""),
            path=f"raw/{fn}",
            status=getattr(p, "status_code", 0),
            bytes=size,
        ).__dict__
        fetch_index.append(rec)
        lines_stdout.append(f"{i:05d} {rec}")

    # Índice y sumario
    (run_dir / "fetch_index.json").write_text(json.dumps(fetch_index, ensure_ascii=False, indent=2), encoding="utf-8")
    summary = {
        "seed": pages[0].url if pages else "",
        "n_pages": len(pages),
        "bytes": total_bytes,
        "totals": {"pages": len(pages), "bytes": total_bytes},  # 'chunks' los calculará la UI leyendo los raw
        "run_dir": str(run_dir),
    }
    (run_dir / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    (run_dir / "stdout.txt").write_text("\n".join(lines_stdout), encoding="utf-8")

    return summary



# ---------------------------------------------------------------------
# Helpers para PDFs en sitemap: limpiar 'pdf' del exclude si se pide
# ---------------------------------------------------------------------
def _strip_pdf_from_exclude(patterns: List[str]) -> List[str]:
    """
    Elimina 'pdf' de patrones típicos de exclusión (regexs) p.ej.:
      r"\.(png|jpg|jpeg|gif|css|js|pdf)$" -> r"\.(png|jpg|jpeg|gif|css|js)$"
    Conserva otros patrones tal cual.
    """
    import re
    out: List[str] = []
    for pat in patterns:
        p2 = pat
        # eliminaciones directas de 'pdf' en alternancias
        p2 = p2.replace('|pdf', '').replace('pdf|', '')
        # limpia paréntesis vacíos o dobles pipes
        p2 = p2.replace('(|', '(').replace('|)', ')').replace('||', '|')
        p2 = re.sub(r'\(\)', '', p2)
        p2 = p2.replace('.()', '.')
        out.append(p2)
    return out


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

    # PDFs: solo los permitimos explícitamente en sitemap
    if args.include_pdfs and args.strategy == "sitemap":
        exclude = _strip_pdf_from_exclude(exclude)
        log(f"[sitemap] include_pdfs=ON -> exclude ajustado: {exclude}")

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
        include_pdfs=bool(args.include_pdfs),
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
