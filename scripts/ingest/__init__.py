# -*- coding: utf-8 -*-
"""
Paquete 'scripts.ingest' y utilidades de carga perezosa de estrategias.

Uso opcional desde otros módulos:
    from scripts.ingest import load_strategies, get_strategy

    collect_requests, collect_selenium, collect_sitemap = load_strategies()
    collect_fn = get_strategy("requests")
"""

from importlib import import_module
from typing import Callable, Tuple, Dict


def load_strategies() -> Tuple[Callable, Callable, Callable]:
    """
    Devuelve las funciones 'collect_pages' de:
      - requests, selenium, sitemap
    Lanza ImportError si alguna no está disponible.
    """
    CR = import_module("scripts.ingest.web_strategy_requests").collect_pages
    CS = import_module("scripts.ingest.web_strategy_selenium").collect_pages
    CM = import_module("scripts.ingest.web_strategy_sitemap").collect_pages
    return CR, CS, CM


def get_strategy(name: str) -> Callable:
    """
    Obtiene la función 'collect_pages' de la estrategia indicada.
    Estrategias válidas: 'requests', 'selenium', 'sitemap'.
    """
    name = (name or "").lower().strip()
    if name not in {"requests", "selenium", "sitemap"}:
        raise KeyError(f"Estrategia no soportada: {name}")
    cr, cs, cm = load_strategies()
    mapping: Dict[str, Callable] = {
        "requests": cr,
        "selenium": cs,
        "sitemap": cm,
    }
    return mapping[name]


__all__ = ["load_strategies", "get_strategy"]
