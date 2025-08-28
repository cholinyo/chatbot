from __future__ import annotations
from pathlib import Path
from typing import Tuple, Dict, Any
from PyPDF2 import PdfReader  # type: ignore

def load_pdf(path: str | Path) -> Tuple[str, Dict[str, Any]]:
    p = Path(path)
    meta: Dict[str, Any] = {"pages": 0, "title": None}
    text_parts: list[str] = []
    reader = PdfReader(str(p))
    meta["pages"] = len(reader.pages)
    try:
        info = reader.metadata
        if info and getattr(info, "title", None):
            meta["title"] = str(info.title)
    except Exception:
        pass
    for page in reader.pages:
        try:
            t = page.extract_text() or ""
        except Exception:
            t = ""
        text_parts.append(t)
    return "\n\n".join(text_parts), meta