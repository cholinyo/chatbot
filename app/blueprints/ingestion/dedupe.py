# app/ingest/dedupe.py
from __future__ import annotations
import hashlib
from typing import List, Tuple, Set

def _norm(s: str) -> str:
    return " ".join(s.lower().split())

def _hash(s: str) -> str:
    return hashlib.sha1(_norm(s).encode("utf-8")).hexdigest()

def _shingles(s: str, k: int = 8) -> Set[str]:
    s = _norm(s)
    return {s[i:i+k] for i in range(0, max(0, len(s)-k+1))}

def jaccard(a: Set[str], b: Set[str]) -> float:
    if not a or not b: return 0.0
    inter = len(a & b); uni = len(a | b)
    return inter / uni if uni else 0.0

def dedupe_chunks(chunks: List[str], near_threshold: float = 0.92) -> Tuple[List[str], int, int]:
    seen_hash, exact_removed, near_removed = set(), 0, 0
    out, fingerprints = [], []
    for c in chunks:
        h = _hash(c)
        if h in seen_hash:
            exact_removed += 1; continue
        fp = _shingles(c)
        if any(jaccard(fp, f) >= near_threshold for f in fingerprints):
            near_removed += 1; continue
        seen_hash.add(h)
        fingerprints.append(fp)
        out.append(c)
    return out, exact_removed, near_removed
