# app/ingest/textops.py
from __future__ import annotations
import re
from typing import List

def clean_text(t: str) -> str:
    t = t.replace("\r\n", "\n").replace("\r", "\n")
    # quitar cabeceras/pies repetidos (líneas cortas que se repiten 5+ veces)
    lines = [l.strip() for l in t.split("\n")]
    freq = {}
    for l in lines:
        if 0 < len(l) <= 80: freq[l] = freq.get(l, 0) + 1
    blacklist = {l for l,c in freq.items() if c >= 5}
    lines = [l for l in lines if l not in blacklist]
    t = "\n".join(lines)
    # arreglar guionado de fin de línea: "pala-\nbra" -> "palabra"
    t = re.sub(r"(\w)-\n(\w)", r"\1\2", t)
    # compactar espacios
    t = re.sub(r"[ \t]+", " ", t)
    t = re.sub(r"\n{3,}", "\n\n", t)
    return t.strip()

def _split_candidates(t: str) -> List[int]:
    idxs = [m.end() for m in re.finditer(r"[\.!\?]\s", t)]
    return idxs or []

def chunk_text(t: str, target: int = 700, overlap: float = 0.12) -> List[str]:
    t = t.strip()
    if not t: return []
    idxs = _split_candidates(t)
    if not idxs: idxs = list(range(target, len(t), target))
    chunks, start = [], 0
    while start < len(t):
        end = min(start + target, len(t))
        # empuja end hasta un límite de frase si existe
        near = [i for i in idxs if start < i <= end + int(target*0.2)]
        if near: end = max(near)
        chunks.append(t[start:end].strip())
        # solape
        step = target - int(target * overlap)
        start += max(1, step)
    return [c for c in chunks if c]
