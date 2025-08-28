# app/rag/scrapers/requests_bs4.py
# -----------------------------------------------------------------------------
# Scraper web basado en requests + BeautifulSoup (BS4).
# - Múltiples "seeds" (URLs de inicio).
# - Respeta robots.txt (opcional) + Crawl-delay.
# - Rate limit por host (combinado con Crawl-delay).
# - Filtros por dominios permitidos y patrones include/exclude (glob o regex).
# - Canonicalización de URLs (+ uso de <link rel="canonical"> si existe).
# - Soporte force_https (útil cuando el sitemap devuelve http://).
# - Retries con backoff exponencial y jitter para 429/5xx.
# - NUEVO: Política granular de robots:
#       * robots_policy: 'strict' | 'ignore' | 'list'
#       * ignore_robots_for: conjunto de dominios a los que se ignora robots.txt
#
# NOTAS:
# - Ejecución directa (smoke): `python -m app.rag.scrapers.requests_bs4 --seed ...`
# -----------------------------------------------------------------------------

from __future__ import annotations

import random
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

DEFAULT_UA = "TFM-RAG/1.0 (+mailto:contacto@mi-ayuntamiento.es)"
logger = logging.getLogger("ingestion.web.requests_bs4")


# -----------------------------------------------------------------------------
# Configuración del scraper
# -----------------------------------------------------------------------------
@dataclass
class ScrapeConfig:
    # Seeds puede ser lista o string único
    seeds: List[str] | str

    # Estrategia de crawling
    depth: int = 1
    allowed_domains: Optional[Set[str]] = None

    # Patrones include/exclude (glob-like o regex). Se aplican sobre "ruta+query".
    include_url_patterns: Optional[List[str]] = None
    exclude_url_patterns: Optional[List[str]] = None

    # Políticas de acceso
    # (compatibilidad) si respect_robots=False => robots_policy pasa a "ignore"
    respect_robots: bool = True
    user_agent: str = DEFAULT_UA
    rate_limit_per_host: float = 1.0  # req/seg (mínimo base, puede aumentar por Crawl-delay)
    timeout_seconds: int = 15
    max_pages: int = 200
    headers: Optional[Dict[str, str]] = None

    # Robustez / red
    max_retries: int = 3                   # reintentos para 429/5xx
    backoff_factor: float = 0.8            # factor de backoff exponencial
    backoff_jitter_ms: Tuple[int, int] = (100, 400)  # jitter aleatorio (milisegundos)
    min_html_bytes: int = 50               # descarte si la respuesta HTML es minúscula

    # Canonicalización adicional
    force_https: bool = False              # reescribe http:// → https:// en seeds y enlaces

    # NUEVO — Política granular de robots
    #   - 'strict'  => respetar robots (por defecto)
    #   - 'ignore'  => ignorar robots en todos los dominios
    #   - 'list'    => respetar robots salvo en dominios indicados en ignore_robots_for
    robots_policy: str = "strict"
    ignore_robots_for: Optional[Set[str]] = None  # p. ej. {"onda.es", "www.onda.es"}

    def normalized(self) -> "ScrapeConfig":
        """Normaliza la configuración (dominios a minúsculas, etc.)."""
        if self.allowed_domains:
            self.allowed_domains = {d.lower() for d in self.allowed_domains}
        if self.ignore_robots_for:
            self.ignore_robots_for = {d.lower() for d in self.ignore_robots_for}
        # compat: si respect_robots == False, fuerza política ignore
        if not self.respect_robots:
            self.robots_policy = "ignore"
        # normaliza valor de robots_policy
        if self.robots_policy not in {"strict", "ignore", "list"}:
            self.robots_policy = "strict"
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
    """Caché de robots.txt por dominio con control explícito del status HTTP.
    - Si robots.txt no es 200 -> se asume 'allow all' y NO se consulta can_fetch.
    - Si es 200 -> se parsea y se respeta.
    - Soporta force_https para formar la URL base.
    """
    def __init__(self, user_agent: str, *, force_https: bool = False, session: Optional[requests.Session] = None) -> None:
        self._ua = user_agent
        self._force_https = force_https
        self._session = session or requests.Session()
        # base -> dict(parser=RobotFileParser, delay=Optional[float], status_ok=bool)
        self._cache: Dict[str, Dict[str, object]] = {}

    def _base_for(self, url: str) -> str:
        p = up.urlparse(url)
        scheme = "https" if self._force_https else p.scheme
        return f"{scheme}://{p.netloc}"

    def _load(self, base: str) -> Dict[str, object]:
        if base in self._cache:
            return self._cache[base]

        robots_url = up.urljoin(base, "/robots.txt")
        rp = robotparser.RobotFileParser()
        status_ok = False
        delay: Optional[float] = None

        try:
            resp = self._session.get(robots_url, timeout=10, allow_redirects=True)
            if resp.status_code == 200 and resp.content:
                text = resp.text or ""
                rp.set_url(robots_url)
                rp.parse(text.splitlines())
                status_ok = True
                try:
                    delay = rp.crawl_delay(self._ua)
                except Exception:
                    delay = None
                logger.debug("robots.load.ok: %s (delay=%s)", robots_url, delay)
            else:
                # Cualquier status !=200: tratamos como 'allow all'
                rp.set_url(robots_url)
                rp.parse([])  # sin reglas
                logger.info("robots.load.missing: %s (status=%s) -> allow all", robots_url, resp.status_code)
        except Exception as e:
            # Error de red: permitir
            rp.set_url(robots_url)
            rp.parse([])
            logger.info("robots.load.error: %s (%s) -> allow all", robots_url, e)

        entry = {"parser": rp, "delay": delay, "status_ok": status_ok}
        self._cache[base] = entry
        return entry

    def allowed(self, url: str) -> bool:
        try:
            base = self._base_for(url)
            entry = self._load(base)
            status_ok = bool(entry["status_ok"])
            rp: robotparser.RobotFileParser = entry["parser"]  # type: ignore

            # Si robots.txt NO es 200, **permitimos** sin consultar can_fetch
            if not status_ok:
                return True

            test_url = url
            if self._force_https and test_url.startswith("http://"):
                test_url = "https://" + test_url[len("http://"):]
            ok = rp.can_fetch(self._ua, test_url)
            if not ok:
                logger.info("robots.block.detail: base=%s test=%s ua=%s", base, test_url, self._ua)
            return ok
        except Exception:
            # Prudente: si algo falla, permitir
            return True

    def crawl_delay_or_none(self, url: str) -> Optional[float]:
        try:
            base = self._base_for(url)
            entry = self._load(base)
            return entry["delay"]  # type: ignore
        except Exception:
            return None


class RateLimiter:
    """Limitador de tasa por host: garantiza un intervalo mínimo entre peticiones."""
    def __init__(self, rps: float) -> None:
        self.min_interval = 1.0 / max(rps, 0.01)
        self._last_ts: Dict[str, float] = {}

    def wait(self, netloc: str, extra_min_interval: Optional[float] = None) -> None:
        """Espera respetando el intervalo base y, si se aporta, un intervalo mínimo extra."""
        min_interval = max(self.min_interval, extra_min_interval or 0.0)
        last = self._last_ts.get(netloc, 0.0)
        now = time.time()
        delay = min_interval - (now - last)
        if delay > 0:
            time.sleep(delay)
        self._last_ts[netloc] = time.time()


def _force_https_if_needed(url: str, enabled: bool) -> str:
    if enabled and url.startswith("http://"):
        return "https://" + url[len("http://") :]
    return url


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
    """True si `host` coincide con alguno de los dominios permitidos o es su subdominio."""
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
    url: str                   # URL final canonicalizada (tras redirects)
    base_url: str              # URL de respuesta (sin canonicalizar)
    html: str                  # HTML completo
    status_code: int
    headers: Dict[str, str]
    links: List[str] = field(default_factory=list)  # Enlaces extraídos y canonicalizados
    origin_hash: str = ""      # Hash del HTML original (para deduplicación/versionado)
    title: Optional[str] = None  # <title> de la página (si se encontró)


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
        # Creamos siempre la caché; el uso depende de robots_policy
        self._robots_cache = RobotsCache(
            self.cfg.user_agent, force_https=self.cfg.force_https, session=self._session
        )
        self._ratelimiter = RateLimiter(self.cfg.rate_limit_per_host)

        # Compilación de patrones include/exclude
        self._include_re = _compile_patterns(self.cfg.include_url_patterns)
        self._exclude_re = _compile_patterns(self.cfg.exclude_url_patterns)

    # ----------------------------- API pública -----------------------------
    def crawl(self) -> Iterable[Page]:
        """
        BFS desde seeds, respetando:
        - Profundidad `depth`
        - Filtro de dominios y patrones
        - robots.txt (según robots_policy / ignore_robots_for)
        - Rate limit + Crawl-delay
        - Límite de páginas `max_pages`
        """
        seeds = self.cfg.seeds if isinstance(self.cfg.seeds, list) else [self.cfg.seeds]
        # force_https en seeds
        seeds = [_canonicalize(_force_https_if_needed(s, self.cfg.force_https)) for s in seeds]

        q: List[Tuple[str, int]] = [(s, 0) for s in seeds]
        seen: Set[str] = set()
        fetched = 0

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
            if page is None:
                continue

            fetched += 1
            yield page

            if d < self.cfg.depth:
                for nxt in page.links:
                    if nxt not in seen:
                        q.append((nxt, d + 1))

    # ------------------------- API pública adicional -------------------------
    def fetch_url(self, url: str) -> Optional[Page]:
        """
        Descarga una URL individual aplicando filtros (_should_visit) y robots.
        Útil para estrategias que descubren URLs por fuera (p. ej., sitemap).
        """
        url = _canonicalize(_force_https_if_needed(url, self.cfg.force_https))

        if not self._should_visit(url):
            logger.debug("skip.filters: %s", url)
            return None

        if not self._is_allowed_by_robots(url):
            logger.info("robots.block: %s", url)
            return None

        return self._fetch(url)

    # --------------------------- Lógica interna ---------------------------
    def _robots_ignored_for_domain(self, netloc: str) -> bool:
        """Determina si debemos ignorar robots para este dominio según la política 'list'."""
        if not self.cfg.ignore_robots_for:
            return False
        # Ignora en coincidencia exacta o subdominios
        return _same_or_subdomain(netloc, self.cfg.ignore_robots_for)

    def _is_allowed_by_robots(self, url: str) -> bool:
        """Evalúa robots.txt según robots_policy/ignore_robots_for."""
        # Política global 'ignore'
        if self.cfg.robots_policy == "ignore":
            return True

        netloc = up.urlparse(url).netloc.lower()

        # Política 'list': ignora robots si el dominio está en la lista
        if self.cfg.robots_policy == "list" and self._robots_ignored_for_domain(netloc):
            return True

        # Política 'strict' o 'list' fuera de lista: consulta robots.txt
        return self._robots_cache.allowed(url)

    def _crawl_delay_if_any(self, url: str) -> Optional[float]:
        """Obtiene crawl-delay si aplica (no se aplica si robots se ignora para este dominio)."""
        if self.cfg.robots_policy == "ignore":
            return None
        netloc = up.urlparse(url).netloc.lower()
        if self.cfg.robots_policy == "list" and self._robots_ignored_for_domain(netloc):
            return None
        return self._robots_cache.crawl_delay_or_none(url)

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

            # Evaluamos include/exclude sobre "ruta + query"
            pathq = pu.path + (f"?{pu.query}" if pu.query else "")

            # Include (si no hay include, se permite todo por defecto)
            if self._include_re:
                if not any(r.search(pathq) for r in self._include_re):
                    return False

            # Exclude
            if self._exclude_re:
                if any(r.search(pathq) for r in self._exclude_re):
                    return False

            return True
        except Exception:
            # Silencioso por robustez en crawling
            return False

    def _fetch(self, url: str) -> Optional[Page]:
        """
        Descarga con:
        - Rate limit + Crawl-delay (si robots lo define)
        - Retries con backoff exponencial y jitter para 429/5xx
        - Validación de HTML y tamaño mínimo
        - Extracción de enlaces y <title> (+ <link rel="canonical">)
        """
        netloc = up.urlparse(url).netloc

        # Considera Crawl-delay si existe (solo cuando robots aplica)
        crawl_delay = self._crawl_delay_if_any(url)
        self._ratelimiter.wait(netloc, extra_min_interval=crawl_delay)

        # Retries/backoff
        attempts = 0
        while True:
            attempts += 1
            try:
                resp = self._session.get(url, timeout=self.cfg.timeout_seconds, allow_redirects=True)
            except Exception as e:
                logger.warning("fetch.fail: %s (%s)", url, e)
                if attempts >= self.cfg.max_retries:
                    return None
                self._sleep_backoff(attempts)
                continue

            status = resp.status_code
            # Reintenta en 429 o 5xx
            if status == 429 or 500 <= status < 600:
                logger.info("fetch.retryable_status: %s (%d) attempt=%d", url, status, attempts)
                if attempts >= self.cfg.max_retries:
                    return None
                self._sleep_backoff(attempts)
                continue

            if status >= 400:
                logger.info("fetch.fail: %s (%s)", url, status)
                return None

            if not _content_is_html(resp):
                logger.debug("skip.non_html: %s (%s)", url, resp.headers.get("Content-Type"))
                return None

            html_bytes = resp.content or b""
            if len(html_bytes) < self.cfg.min_html_bytes:
                logger.debug("skip.too_small: %s (len=%d)", url, len(html_bytes))
                return None

            html = html_bytes.decode(resp.encoding or "utf-8", errors="ignore")
            base = str(resp.url)  # URL final tras redirecciones
            base = _force_https_if_needed(base, self.cfg.force_https)

            # Parse HTML una sola vez
            soup = BeautifulSoup(html, "html.parser")

            # Título
            title: Optional[str] = None
            t = soup.find("title")
            if t and t.text:
                title = t.text.strip()[:500]  # cap de seguridad

            # Canonical <link rel="canonical">
            canonical = soup.find("link", rel=lambda v: v and "canonical" in (v if isinstance(v, list) else [v]))
            if canonical and canonical.get("href"):
                can_url = up.urljoin(base, canonical["href"].strip())
                can_url = _canonicalize(_force_https_if_needed(can_url, self.cfg.force_https))
            else:
                can_url = _canonicalize(base)

            page = Page(
                url=can_url,
                base_url=base,
                html=html,
                status_code=status,
                headers=dict(resp.headers),
                title=title,
            )
            page.origin_hash = sha256_hexdigest(html)

            # Enlaces (resueltos respecto a base_url, luego canonicalizados y force_https si procede)
            page.links = self._extract_links(soup, base_url=base, force_https=self.cfg.force_https)

            logger.info("fetch.ok: %s (links=%d)", page.url, len(page.links))
            return page

    def _sleep_backoff(self, attempt: int) -> None:
        """Espera con backoff exponencial y jitter (ms) en reintentos."""
        base = (self.cfg.backoff_factor ** (attempt - 1))
        jitter = random.randint(*self.cfg.backoff_jitter_ms) / 1000.0
        time.sleep(base + jitter)

    @staticmethod
    def _extract_links(soup: BeautifulSoup, base_url: str, *, force_https: bool) -> List[str]:
        """
        Extrae enlaces <a href="..."> del soup, los resuelve a absolutos respecto a base_url,
        y los canonicaliza + aplica force_https si corresponde. Devuelve una lista sin duplicados.
        """
        out: List[str] = []
        for a in soup.find_all("a", href=True):
            href = a["href"].strip()
            abs_url = up.urljoin(base_url, href)
            abs_url = _force_https_if_needed(abs_url, force_https)
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
    parser.add_argument("--seed", action="append", required=True, help="URL semilla (repetible)")
    parser.add_argument("--depth", type=int, default=1, help="Profundidad BFS")
    parser.add_argument("--allowed-domains", default="", help="Lista separada por comas")
    parser.add_argument("--include", action="append", default=[], help="Patrón include (glob o regex). Repetible.")
    parser.add_argument("--exclude", action="append", default=[], help="Patrón exclude (glob o regex). Repetible.")
    parser.add_argument("--max-pages", type=int, default=10, help="Máximo de páginas a descargar")
    parser.add_argument("--timeout", type=int, default=15, help="Timeout HTTP (seg)")
    parser.add_argument("--rate", type=float, default=1.0, help="Rate-limit por host (req/seg)")

    # Compatibilidad previa:
    parser.add_argument("--no-robots", action="store_true", help="No respetar robots.txt (MVP/tests)")

    # NUEVO: controles granulares de robots
    parser.add_argument(
        "--robots-policy",
        choices=["strict", "ignore", "list"],
        default=None,
        help="Política de robots: strict=respeta; ignore=ignora global; list=ignora solo dominios indicados",
    )
    parser.add_argument(
        "--ignore-robots-for",
        default="",
        help="Lista de dominios (coma) a los que ignorar robots.txt cuando --robots-policy=list",
    )

    parser.add_argument("--force-https", action="store_true", help="Reescribe http:// → https:// en seeds y enlaces")
    parser.add_argument("--verbose", action="store_true", help="Logs INFO")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO if args.verbose else logging.WARNING,
        format="%(asctime)s %(levelname)s %(name)s :: %(message)s",
    )

    allowed = {d.strip().lower() for d in args.allowed_domains.split(",") if d.strip()} or None

    # Resolver política final de robots (compat con --no-robots)
    policy = args.robots_policy or ("ignore" if args.no_robots else "strict")
    ignore_set = {d.strip().lower() for d in args.ignore_robots_for.split(",") if d.strip()} or None

    cfg = ScrapeConfig(
        seeds=args.seed,
        depth=args.depth,
        allowed_domains=allowed,
        include_url_patterns=args.include or None,
        exclude_url_patterns=args.exclude or None,
        # Mantener compat: respect_robots solo influye si robots_policy no viene dado
        respect_robots=(policy != "ignore"),
        user_agent=DEFAULT_UA,
        timeout_seconds=args.timeout,
        rate_limit_per_host=args.rate,
        max_pages=args.max_pages,
        force_https=args.force_https,
        robots_policy=policy,
        ignore_robots_for=ignore_set,
    )

    scraper = RequestsBS4Scraper(cfg)

    count = 0
    for page in scraper.crawl():
        count += 1
        text = html_to_text(page.html, NormalizeConfig())
        print(f"\n=== [{count}] {page.url} ===")
        print((page.title or "").strip()[:200])
        print(text[:800] + ("…" if len(text) > 800 else ""))
    print(f"\nCrawl finalizado. Páginas procesadas: {count} (seeds={len(args.seed)})")
