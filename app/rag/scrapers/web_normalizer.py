# app/rag/scrapers/web_normalizer.py
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Iterable, List, Optional

from bs4 import BeautifulSoup

# Selectors comunes a retirar si el caller no especifica otros
DEFAULT_DROP_SELECTORS = ["nav", "footer", "script", "style", "noscript", ".cookie-banner", ".cookies", ".banner", "header"]

_HEADING_TAGS = {"h1", "h2", "h3"}

@dataclass
class NormalizeConfig:
    keep_headings: bool = True
    drop_selectors: Optional[List[str]] = None
    collapse_whitespace: bool = True
    min_paragraph_len: int = 0  # 0 = no filtro

def _drop_nodes(soup: BeautifulSoup, selectors: Iterable[str]) -> None:
    for sel in selectors:
        # BeautifulSoup select supports simple CSS selectors
        for tag in soup.select(sel):
            tag.decompose()

def _text_from_node(node) -> str:
    # Preserve headings on their own line
    if node.name and node.name.lower() in _HEADING_TAGS:
        return f"\n{node.get_text(separator=' ', strip=True)}\n"
    return node.get_text(separator=" ", strip=True)

def _collapse_ws(text: str) -> str:
    # normalize whitespace but preserve newlines between paragraphs
    # replace multiple spaces with one
    text = re.sub(r"[ \t\u00A0]+", " ", text)
    # collapse >2 newlines into 2
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()

def html_to_text(html: str, cfg: Optional[NormalizeConfig] = None) -> str:
    cfg = cfg or NormalizeConfig()
    soup = BeautifulSoup(html or "", "html.parser")

    # Remove common boilerplate if not already specified
    drop = cfg.drop_selectors if cfg.drop_selectors is not None else DEFAULT_DROP_SELECTORS
    _drop_nodes(soup, drop)

    # Remove hidden nodes
    for tag in soup.find_all(style=True):
        if "display:none" in tag.get("style", "").replace(" ", "").lower():
            tag.decompose()

    # Keep title first
    pieces: List[str] = []
    if soup.title and soup.title.string:
        pieces.append(soup.title.string.strip())

    # Extract headings and paragraphs in reading order
    main = soup.body or soup  # fallback to full doc if no body
    for el in main.find_all(["h1", "h2", "h3", "p", "li"]):
        txt = _text_from_node(el)
        if not txt:
            continue
        if cfg.min_paragraph_len and el.name == "p" and len(txt) < cfg.min_paragraph_len:
            continue
        pieces.append(txt)

    text = "\n".join(pieces)
    if cfg.collapse_whitespace:
        text = _collapse_ws(text)
    return text
