from __future__ import annotations
from pathlib import Path
from typing import Tuple, Dict, Any

def load_txt(path: str | Path, *, default_encoding: str = "utf-8") -> Tuple[str, Dict[str, Any]]:
    p = Path(path)
    encodings = [default_encoding, "utf-8-sig", "latin-1"]
    last_err: Exception | None = None
    for enc in encodings:
        try:
            data = p.read_text(encoding=enc)
            return data, {"encoding": enc}
        except Exception as e:
            last_err = e; continue
    raise last_err or RuntimeError("Failed to read text file")