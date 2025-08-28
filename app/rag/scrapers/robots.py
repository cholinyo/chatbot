# app/rag/scrapers/robots.py
from __future__ import annotations

import time
import logging
from typing import Dict, Iterable, Optional
from urllib.parse import urlparse, urlunparse
from urllib import robotparser

import requests

try:
    # Tu logger centralizado
    from app.extensions.logging import get_logger
    logger = get_logger("ingest.web.robots")
except Exception:
    logging.basicConfig(level=logging.INFO)
    logger = logging.getLogger("ingest.web.robots")


class RobotsManager:
    """
    Gestor de robots.txt con caché por dominio + políticas de cumplimiento.
    Políticas:
      - 'strict': respeta robots.txt
      - 'ignore': ignora robots.txt para todos los dominios
      - lista de dominios a ignorar (equivalente a --ignore-robots-for)
    """
    def __init__(
        self,
        user_agent: str = "RAG-Ingest-Bot/1.0 (+https://example.local)",
        policy: "strict|ignore|list" = "strict",
        ignore_domains: Optional[Iterable[str]] = None,
        ttl_seconds: int = 3600,
        force_https: bool = False,
        timeout: float = 10.0,
    ):
        self.user_agent = user_agent
        self.policy = policy  # 'strict' | 'ignore' | 'list'
        self.ignore_domains = set((ignore_domains or []))
        self.ttl = ttl_seconds
        self.force_https = force_https
        self.timeout = timeout
        self._cache: Dict[str, dict] = {}  # domain -> {"ts": int, "rp": RobotFileParser|None}

    @staticmethod
    def _domain_from_url(url: str) -> str:
        return urlparse(url).netloc.lower()

    def _robots_url_for(self, any_url: str) -> str:
        p = urlparse(any_url)
        scheme = "https" if (self.force_https or p.scheme == "https") else (p.scheme or "http")
        robots = p._replace(scheme=scheme, path="/robots.txt", params="", query="", fragment="")
        return urlunparse(robots)

    def _fetch_robots(self, robots_url: str) -> Optional[str]:
        try:
            r = requests.get(robots_url, timeout=self.timeout, headers={"User-Agent": self.user_agent})
            if r.status_code == 200 and r.text:
                return r.text
            logger.info("robots.fetch.miss status=%s url=%s", r.status_code, robots_url)
        except Exception as e:
            logger.warning("robots.fetch.error url=%s err=%s", robots_url, e)
        return None

    def _get_parser(self, any_url: str) -> Optional[robotparser.RobotFileParser]:
        domain = self._domain_from_url(any_url)
        now = int(time.time())

        cached = self._cache.get(domain)
        if cached and (now - cached["ts"] < self.ttl):
            return cached["rp"]

        robots_url = self._robots_url_for(any_url)
        content = self._fetch_robots(robots_url)
        if not content:
            self._cache[domain] = {"ts": now, "rp": None}
            logger.info("robots.missing domain=%s url=%s", domain, robots_url)
            return None

        rp = robotparser.RobotFileParser()
        rp.parse(content.splitlines())
        self._cache[domain] = {"ts": now, "rp": rp}
        logger.info("robots.loaded domain=%s url=%s", domain, robots_url)
        return rp

    def is_allowed(self, url: str) -> bool:
        domain = self._domain_from_url(url)

        # Política global ignorar
        if self.policy == "ignore":
            logger.debug("robots.ignore policy=ignore domain=%s url=%s", domain, url)
            return True

        # Lista de dominios a ignorar
        if domain in self.ignore_domains:
            logger.debug("robots.ignore policy=list domain=%s url=%s", domain, url)
            return True

        # Política strict
        rp = self._get_parser(url)
        if rp is None:
            # Si no hay robots.txt accesible, por defecto permitimos pero lo registramos
            logger.info("robots.allow.missing domain=%s url=%s", domain, url)
            return True

        allowed = rp.can_fetch(self.user_agent, url)
        if not allowed:
            logger.info("robots.block domain=%s url=%s", domain, url)
        else:
            logger.debug("robots.allow domain=%s url=%s", domain, url)
        return allowed
