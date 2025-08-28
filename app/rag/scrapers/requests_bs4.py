# app/rag/scrapers/requests_bs4.py
# -----------------------------------------------------------------------------
# Scraper web basado en requests + BeautifulSoup (BS4).
# - Soporta múltiples "seeds" (URLs de inicio).
# - Respeta robots.txt (opcional).
# - Limita tasa por host (rate limit).
# - Filtra por dominios permitidos y por patrones include/exclude (glob o regex).
# - Normaliza URLs (canonicalización) y extrae enlaces para BFS.
#
# NOTAS:
# - Para ejecutar como módulo: `python -m app.rag.scrapers.requests_bs4 --seed ...`
#   Asegúrate de que el __init__.py del paquete NO importe ansiosamente este módulo.
# -----------------------------------------------------------------------------

from __future__ import annotations

import re
import time
import hashlib
import logging
import urllib.parse as up
from dataclasses import dataclass, field
from typing import Iterable, List, Optional, Set, Dict, Tuple

import requests
from bs4 import BeautifulSoup
from urllib import robotparser

# User-Agent por defecto (ajústalo a tu proyecto/organización)
DEFAULT_UA = "TFM-RAG/1.0 (+mailto:contacto@mi-ayuntamiento.es)"

logger = logging.getLogger("ingestion.web.requests_bs4")


# -----------------------------------------------------------------------------
# Configuración del scraper
# -----------------------------------------------------------------------------
@dataclass
class ScrapeConfig:
    # Ahora admite lista de seeds o una sola cadena.
    seeds: List[str] | str
    depth: int = 1
    allowed_domains: Optional[Set[str]] = None
    # Patrones de inclusión/exclusión:
    # - Si incluyen "*", se interpretan como "glob-like" y se convierten a regex.
    # - Si quieres regex crudo, pasa un patrón que ya empiece por ".*" u otro regex.
    include_url_patterns: Optional[List[str]] = None
    exclude_url_patterns: Optional[List[str]] = None
    respect_robots: bool = True
    user_agent: str = DEFAULT_UA
    rate_limit_per_host: float = 1.0  # peticiones/segundo (por host)
    timeout_seconds: int = 15
    max_pages: int = 200
    headers: Optional[Dict[str, str]] = None

    def normalized(self) -> "ScrapeConfig":
        """Normaliza la configuración (p. ej. dominios en minúsculas)."""
        if self.allowed_domains:
            self.allowed_domains = {d.lower() for d in self.allowed_domains}
        return self


# -----------------------------------------------------------------------------
# Utilidades internas
# -----------------------------------------------------------------------------
def _compile_patterns(patterns: Optional[List[str]]) -> List[re.Pattern]:
    """
    Compila una lista de patrones en regex.
    Acepta patrones "glob-like" (con '*') o regex ya formadas.
    """
    if not patterns:
        return []
    compiled = []
    for p in patterns:
        if "*" in p and not p.startswith(".*"):
            # glob → regex (escape de todo excepto '*', que pasa a '.*')
            p = re.escape(p).replace(r"\*", ".*")
        compiled.append(re.compile(p))
    return compiled


class RobotsCache:
    """Caché simple de robots.txt por dominio usando urllib.robotparser."""
    def __init__(self, user_agent: str) -> None:
        self._cache: Dict[str, robotparser.RobotFileParser] = {}
        self._ua = user_agent

    def allowed(self, url: str) -> bool:
        """Devuelve True si robots.txt permite acceder a `url` con este UA."""
        parsed = up.urlparse(url)
        base = f"{parsed.scheme}://{parsed.netloc}"
        rp = self._cache.get(base)
        if rp is None:
            robots_url = up.urljoin(base, "/robots.txt")
            rp = robotparser.RobotFileParser()
            try:
                rp.set_url(robots_url)
                rp.read()
            except Exception:
                # Si robots no es alcanzable, por simplicidad del MVP somos permisivos.
                logger.debug("robots.txt not reachable for %s", base)
            self._cache[base] = rp
        try:
            return rp.can_fetch(self._ua, url)
        except Exception:
            # Si falla el parser, pasa como permitido (comportamiento MVP).
            return True


class RateLimiter:
    """Limitador de tasa por host: garantiza un intervalo mínimo entre peticiones."""
    def __init__(self, rps: float) -> None:
        self.min_interval = 1.0 / max(rps, 0.01)
        self._last_ts: Dict[str, float] = {}

    def wait(self, netloc: str) -> None:
        last = self._last_ts.get(netloc, 0.0)
        now = time.time()
        delay = self.min_interval - (now - last)
        if delay > 0:
            time.sleep(delay)
        self._last_ts[netloc] = time.time()


def _canonicalize(url: str) -> str:
    """
    Canonicaliza URLs:
    - Elimina fragmentos (#...).
    - Normaliza el netloc a minúsculas.
    - Conserva el querystring.
    """
    u = up.urlsplit(url)
    u = u._replace(fragment="")
    netloc = u.netloc.lower()
    return up.urlunsplit((u.scheme, netloc, u.path, u.query, ""))


def _same_or_subdomain(host: str, allowed: Set[str]) -> bool:
    """Devuelve True si `host` coincide con alguno de los dominios permitidos o es su subdominio."""
    host = host.lower()
    return any(host == d or host.endswith("." + d) for d in allowed)


def _content_is_html(resp: requests.Response) -> bool:
    """Comprueba que el Content-Type sea HTML."""
    ct = resp.headers.get("Content-Type", "")
    return "text/html" in ct or "application/xhtml" in ct


def sha256_hexdigest(data: str | bytes) -> str:
    """Hash SHA-256 hex de texto (utf-8) o bytes."""
    if isinstance(data, str):
        data = data.encode("utf-8", errors="ignore")
    return hashlib.sha256(data).hexdigest()


# -----------------------------------------------------------------------------
# Estructura de página descargada
# -----------------------------------------------------------------------------
@dataclass
class Page:
    url: str                   # URL final (tras redirects), canonicalizada
    base_url: str              # URL de respuesta (sin canonicalizar)
    html: str                  # HTML completo
    status_code: int
    headers: Dict[str, str]
    links: List[str] = field(default_factory=list)  # Enlaces extraídos y canonicalizados
    origin_hash: str = ""      # Hash del HTML original (para deduplicación/versionado)


# -----------------------------------------------------------------------------
# Scraper principal (requests + BS4)
# -----------------------------------------------------------------------------
class RequestsBS4Scraper:
    def __init__(self, cfg: ScrapeConfig) -> None:
        self.cfg = cfg.normalized()

        # Sesión HTTP con UA y cabeceras opcionales
        self._session = requests.Session()
        self._session.headers.update({"User-Agent": self.cfg.user_agent})
        if cfg.headers:
            self._session.headers.update(cfg.headers)

        # Robots y rate-limiter
        self._robots = RobotsCache(self.cfg.user_agent) if self.cfg.respect_robots else None
        self._ratelimiter = RateLimiter(self.cfg.rate_limit_per_host)

        # Compilación de patrones include/exclude
        self._include_re = _compile_patterns(self.cfg.include_url_patterns)
        self._exclude_re = _compile_patterns(self.cfg.exclude_url_patterns)

    # ----------------------------- API pública -----------------------------
    def crawl(self) -> Iterable[Page]:
        """
        Realiza un BFS a partir de las seeds, respetando:
        - Profundidad `depth`
        - Filtro de dominios y patrones
        - robots.txt (si está activado)
        - Límite de páginas `max_pages`
        Devuelve objetos Page con HTML y enlaces ya extraídos.
        """
        seeds = self.cfg.seeds if isinstance(self.cfg.seeds, list) else [self.cfg.seeds]
        q: List[Tuple[str, int]] = [(_canonicalize(s), 0) for s in seeds]
        seen: Set[str] = set()
        fetched = 0

        while q and fetched < self.cfg.max_pages:
            url, d = q.pop(0)
            if url in seen:
                continue
            seen.add(url)

            # Filtros previos (dominios, include/exclude, esquema, etc.)
            if not self._should_visit(url):
                logger.debug("skip.filters: %s", url)
                continue

            # robots.txt
            if self._robots and not self._robots.allowed(url):
                logger.info("robots.block: %s", url)
                continue

            # Descarga y parseo de enlaces
            page = self._fetch(url)
            if page is None:
                continue

            fetched += 1
            yield page

            # Si no hemos alcanzado la profundidad, encolamos enlaces
            if d < self.cfg.depth:
                for nxt in page.links:
                    if nxt not in seen:
                        q.append((nxt, d + 1))

    # --------------------------- Lógica interna ---------------------------
    def _should_visit(self, url: str) -> bool:
        """Aplica filtros de esquema, dominio, include/exclude sobre la URL."""
        try:
            url = _canonicalize(url)
            pu = up.urlparse(url)

            # Esquema soportado
            if pu.scheme not in ("http", "https"):
                return False

            # Filtro de dominio
            if self.cfg.allowed_domains:
                if not _same_or_subdomain(pu.netloc, self.cfg.allowed_domains):
                    return False

            # Include (si no hay include, se permite todo por defecto)
            if self._include_re:
                if not any(r.search(pu.path) for r in self._include_re):
                    return False

            # Exclude
            if self._exclude_re:
                if any(r.search(pu.path) for r in self._exclude_re):
                    return False

            return True
        except Exception:
            # Silencioso por robustez en crawling
            return False

    def _fetch(self, url: str) -> Optional[Page]:
        """Descarga una URL respetando rate limit y valida que sea HTML."""
        netloc = up.urlparse(url).netloc
        self._ratelimiter.wait(netloc)

        try:
            resp = self._session.get(url, timeout=self.cfg.timeout_seconds, allow_redirects=True)
        except Exception as e:
            logger.warning("fetch.fail: %s (%s)", url, e)
            return None

        if resp.status_code >= 400:
            logger.info("fetch.fail: %s (%s)", url, resp.status_code)
            return None

        if not _content_is_html(resp):
            logger.debug("skip.non_html: %s (%s)", url, resp.headers.get("Content-Type"))
            return None

        html = resp.text or ""
        base = str(resp.url)  # URL final tras redirecciones
        page = Page(
            url=_canonicalize(base),
            base_url=base,
            html=html,
            status_code=resp.status_code,
            headers=dict(resp.headers),
        )
        page.origin_hash = sha256_hexdigest(html)
        page.links = self._extract_links(html, base_url=base)
        logger.info("fetch.ok: %s (links=%d)", page.url, len(page.links))
        return page

    def fetch_url(self, url: str) -> Optional[Page]:
        """
        Descarga una URL individual aplicando filtros (_should_visit) y robots.
        Útil para estrategias que descubren URLs por fuera (p. ej., sitemap).
        """
        url = _canonicalize(url)

        if not self._should_visit(url):
            logger.debug("skip.filters: %s", url)
            return None

        if self._robots and not self._robots.allowed(url):
            logger.info("robots.block: %s", url)
            return None

        return self._fetch(url)

    @staticmethod
    def _extract_links(html: str, base_url: str) -> List[str]:
        """
        Extrae enlaces <a href="...">, los resuelve a absolutos respecto a base_url,
        y los canonicaliza. Devuelve una lista sin duplicados (preserva orden).
        """
        soup = BeautifulSoup(html, "html.parser")
        out: List[str] = []
        for a in soup.find_all("a", href=True):
            href = a["href"].strip()
            # Resolver relativos
            abs_url = up.urljoin(base_url, href)
            abs_url = _canonicalize(abs_url)
            out.append(abs_url)

        # De-duplicado preservando orden
        seen: Set[str] = set()
        uniq: List[str] = []
        for u in out:
            if u not in seen:
                seen.add(u)
                uniq.append(u)
        return uniq


# -----------------------------------------------------------------------------
# CLI interno para pruebas rápidas del scraper (sin persistencia)
# -----------------------------------------------------------------------------
if __name__ == "__main__":
    import argparse
    import logging
    from .web_normalizer import html_to_text, NormalizeConfig

    parser = argparse.ArgumentParser(description="MVP crawl (requests+BS4) — múltiples seeds")
    # Ahora --seed es repetible: puedes pasar varias semillas
    parser.add_argument("--seed", action="append", required=True, help="URL semilla (repetible)")
    parser.add_argument("--depth", type=int, default=1, help="Profundidad BFS")
    parser.add_argument("--allowed-domains", default="", help="Lista separada por comas")
    parser.add_argument("--include", action="append", default=[], help="Patrón include (glob o regex). Repetible.")
    parser.add_argument("--exclude", action="append", default=[], help="Patrón exclude (glob o regex). Repetible.")
    parser.add_argument("--max-pages", type=int, default=10, help="Máximo de páginas a descargar")
    parser.add_argument("--timeout", type=int, default=15, help="Timeout HTTP (seg)")
    parser.add_argument("--rate", type=float, default=1.0, help="Rate-limit por host (req/seg)")
    parser.add_argument("--no-robots", action="store_true", help="No respetar robots.txt (MVP/tests)")
    parser.add_argument("--verbose", action="store_true", help="Logs INFO")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO if args.verbose else logging.WARNING,
        format="%(asctime)s %(levelname)s %(name)s :: %(message)s",
    )

    allowed = {d.strip().lower() for d in args.allowed_domains.split(",") if d.strip()} or None

    cfg = ScrapeConfig(
        seeds=args.seed,  # <-- ahora es una lista de semillas
        depth=args.depth,
        allowed_domains=allowed,
        include_url_patterns=args.include or None,
        exclude_url_patterns=args.exclude or None,
        respect_robots=not args.no_robots,
        timeout_seconds=args.timeout,
        rate_limit_per_host=args.rate,
        max_pages=args.max_pages,
    )

    scraper = RequestsBS4Scraper(cfg)

    count = 0
    for page in scraper.crawl():
        count += 1
        text = html_to_text(page.html, NormalizeConfig())
        print(f"\n=== [{count}] {page.url} ===")
        print(text[:800] + ("…" if len(text) > 800 else ""))
    print(f"\nCrawl finalizado. Páginas procesadas: {count} (seeds={len(args.seed)})")


        # ------------------------- API pública adicional -------------------------




