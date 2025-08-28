# app/rag/processing/splitters.py
from dataclasses import dataclass
from typing import List

@dataclass(frozen=True)
class SplitOptions:
    chunk_size: int = 1000
    chunk_overlap: int = 100
    boundary_window: int = 50
    respect_paragraphs: bool = True

@dataclass(frozen=True)
class SplitChunk:
    position: int
    start: int
    end: int
    text: str

def _find_boundary(s: str, start: int, tentative_end: int, window: int, para_boundaries: set[int]) -> int:
    """Try to place the boundary on a nice spot (paragraph start or whitespace) near tentative_end."""
    n = len(s)
    # Prefer a paragraph boundary slightly to the right
    for off in range(0, window + 1):
        cand = tentative_end + off
        if cand in para_boundaries and cand <= n:
            return min(n, cand)
    # Then try whitespace scanning left
    left = max(start + 1, tentative_end - window)
    for i in range(tentative_end, left - 1, -1):
        if s[i - 1].isspace():
            return i
    # Then whitespace scanning right
    right = min(n, tentative_end + window)
    for i in range(tentative_end, right):
        if s[i].isspace():
            return i
    # Fallback: hard boundary
    return min(n, tentative_end)

def split_text(text: str, opts: SplitOptions | None = None) -> List[SplitChunk]:
    """Greedy splitter with overlap, ensuring forward progress to avoid infinite loops on short texts."""
    if not text:
        return []
    opts = opts or SplitOptions()
    chunk_size = max(1, int(opts.chunk_size))
    overlap = min(max(0, int(opts.chunk_overlap)), max(0, chunk_size - 1))

    # Rebuild text and track paragraph boundaries (at double newlines) if requested
    if opts.respect_paragraphs and "\n\n" in text:
        paragraphs = text.split("\n\n")
        rebuilt, para_starts, cursor = [], [], 0
        for i, p in enumerate(paragraphs):
            if i > 0:
                rebuilt.append("\n\n")
                cursor += 2
            para_starts.append(cursor)
            rebuilt.append(p)
            cursor += len(p)
        s = "".join(rebuilt)
        para_boundaries = set(para_starts)
    else:
        s = text
        para_boundaries = set()

    chunks: List[SplitChunk] = []
    pos = 0
    start = 0
    n = len(s)

    while start < n:
        tentative_end = min(n, start + chunk_size)
        end = _find_boundary(s, start, tentative_end, opts.boundary_window, para_boundaries)

        # --- Garantiza progreso estricto ---
        if end <= start:
            # Fallback al corte duro a chunk_size
            end = min(n, start + chunk_size)
            if end <= start:
                # No podemos avanzar: salimos
                break

        piece = s[start:end]
        chunks.append(SplitChunk(position=pos, start=start, end=end, text=piece))
        pos += 1

        if end >= n:
            break

        # Calcula siguiente start con solape, pero evitando retrocesos/estancamientos
        next_start = end - overlap
        if next_start <= start:
            next_start = end  # avanza al menos hasta 'end'
        start = min(next_start, n)

    return chunks
