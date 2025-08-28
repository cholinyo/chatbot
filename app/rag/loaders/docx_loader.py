from __future__ import annotations
from pathlib import Path
from typing import Tuple, Dict, Any
from docx import Document as DocxDocument  # type: ignore

def load_docx(path: str | Path) -> Tuple[str, Dict[str, Any]]:
    p = Path(path)
    doc = DocxDocument(str(p))
    paragraphs = [para.text for para in doc.paragraphs]
    meta: Dict[str, Any] = {"paragraphs": len(paragraphs), "title": None}
    try:
        core = doc.core_properties
        if getattr(core, "title", None):
            meta["title"] = str(core.title)
    except Exception:
        pass
    return "\n".join(paragraphs), meta