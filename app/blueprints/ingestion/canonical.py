# app/ingest/canonical.py
from __future__ import annotations
from dataclasses import dataclass
import re, unicodedata
from typing import Optional, Dict

def normalize_path(p: str) -> str:
    p = p.strip().replace("\\", "/")
    p = re.sub(r"\s+", " ", p)
    p = re.sub(r"/+", "/", p)
    return p.lower()

def has_table_like(text: str) -> bool:
    return ("\t" in text) or (text.count("|") > 8) or bool(re.search(r"\btabla\s+\d+\b", text, re.I))

def guess_lang(text: str) -> Optional[str]:
    try:
        from langdetect import detect  # opcional
        return detect(text[:2000])
    except Exception:
        return None

def canonical_chunk_meta(*, document_title: str, document_path: str,
                         chunk_index: int, text: str,
                         db_chunk_id: Optional[int]=None,
                         source_id: Optional[int]=None,
                         document_id: Optional[int]=None) -> Dict:
    return {
        "document_title": document_title or "",
        "document_path": normalize_path(document_path or ""),
        "chunk_index": chunk_index,
        "text": text,
        "db_chunk_id": db_chunk_id,
        "source_id": source_id,
        "document_id": document_id,
        "lang": guess_lang(text),
        "has_table": has_table_like(text),
    }
