from __future__ import annotations
from dataclasses import dataclass
import re, unicodedata, hashlib
from typing import Iterable

@dataclass(frozen=True)
class CleanOptions:
    normalize_unicode: bool = True
    standardize_newlines: bool = True
    collapse_whitespace: bool = True
    strip_edges: bool = True
    strip_control: bool = True
    max_consecutive_blank_lines: int = 1

_WS_RE = re.compile(r"\s+", re.UNICODE)

def _collapse_ws(s: str) -> str: return _WS_RE.sub(" ", s)

def _strip_controls(s: str) -> str:
    return "".join(ch for ch in s if (ch == "\t" or ch == "\n" or (31 < ord(ch) < 127) or (ord(ch) >= 127)))

def _limit_blank_lines(lines: Iterable[str], max_blank: int) -> list[str]:
    out, blanks = [], 0
    for ll in lines:
        if ll.strip() == "":
            blanks += 1
            if blanks <= max_blank: out.append("")
        else:
            blanks = 0; out.append(ll)
    return out

def clean_text(text: str, opts: CleanOptions | None = None) -> str:
    if not text: return ""
    opts = opts or CleanOptions(); s = text
    if opts.normalize_unicode: s = unicodedata.normalize("NFKC", s)
    if opts.standardize_newlines: s = s.replace("\r\n","\n").replace("\r","\n")
    if opts.strip_control: s = _strip_controls(s)
    lines = s.split("\n"); new_lines = []
    for line in lines:
        ll = _collapse_ws(line) if opts.collapse_whitespace else line
        ll = ll.strip() if opts.strip_edges else ll
        new_lines.append(ll)
    lines = _limit_blank_lines(new_lines, opts.max_consecutive_blank_lines)
    s = "\n".join(lines)
    return s.strip() if opts.strip_edges else s

def text_sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()