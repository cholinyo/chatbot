from __future__ import annotations
from pathlib import Path
from typing import Tuple, Dict, Any, Sequence
import csv

def load_csv(
    path: str | Path,
    *,
    delimiter: str = ",",
    quotechar: str = '"',
    header: bool = True,
    columns: Sequence[str] | None = None,
    encoding: str = "utf-8",
) -> Tuple[str, Dict[str, Any]]:
    p = Path(path); rows_text: list[str] = []; total_rows = 0; used_columns: list[str] | None = None
    with p.open("r", encoding=encoding, newline="") as f:
        reader = csv.reader(f, delimiter=delimiter, quotechar=quotechar)
        if header:
            header_row = next(reader, None)
            if header_row is None: return "", {"rows": 0, "columns": []}
            header_names = [h.strip() for h in header_row]
            if columns:
                used_columns = [c for c in columns if c in header_names]
                idx = [header_names.index(c) for c in used_columns]
            else:
                used_columns = header_names; idx = list(range(len(header_names)))
        else:
            first = next(reader, None)
            if first is None: return "", {"rows": 0, "columns": []}
            n = len(first)
            if columns:
                idx = [int(c) for c in columns]; used_columns = [f"C{i+1}" for i in idx]
            else:
                idx = list(range(n)); used_columns = [f"C{i+1}" for i in idx]
            row_text = "; ".join(f"{used_columns[i]}: {first[idx[i]]}" for i in range(len(idx)))
            rows_text.append(row_text); total_rows += 1
        for row in reader:
            total_rows += 1; parts = []
            for i in range(len(idx)):
                val = row[idx[i]] if i < len(row) else ""
                parts.append(f"{used_columns[i]}: {val}")
            rows_text.append("; ".join(parts))
    return "\n".join(rows_text), {"rows": total_rows, "columns": used_columns or []}