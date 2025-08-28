from __future__ import annotations

import fnmatch
import hashlib
import json
import logging
log = logging.getLogger("ingestion")
from dataclasses import asdict
from datetime import datetime
from pathlib import Path
from typing import Dict, Any, Iterable, List, Tuple

from app.extensions.db import get_session
from app.models.source import Source
from app.models.document import Document
from app.models.chunk import Chunk
from app.models.ingestion_run import IngestionRun

from app.rag.processing.cleaners import clean_text, text_sha256
from app.rag.processing.splitters import SplitOptions, split_text
from app.rag.loaders.pdf_loader import load_pdf
from app.rag.loaders.docx_loader import load_docx
from app.rag.loaders.txt_loader import load_txt
from app.rag.loaders.csv_loader import load_csv


# ------------------------------
# Public API
# ------------------------------
def ingest_documents_by_source_id(source_id: str) -> IngestionRun:
    with get_session() as s:
        src = s.get(Source, source_id)
        if not src:
            raise ValueError(f"Source '{source_id}' not found")
        return _ingest_documents(s, src)


def ingest_documents(source: Source) -> IngestionRun:
    with get_session() as s:
        src = s.get(Source, source.id)
        if not src:
            raise ValueError(f"Source '{source.id}' not found")
        return _ingest_documents(s, src)


# ------------------------------
# Core implementation
# ------------------------------
def _ingest_documents(session, source: Source) -> IngestionRun:
    cfg = source.config or {}
    input_dir = Path(cfg.get("input_dir", "data/raw/documents")).resolve()
    recursive = bool(cfg.get("recursive", True))
    include_ext = [e.lower().lstrip(".") for e in cfg.get("include_ext", ["pdf", "docx", "txt", "csv"])]
    exclude_patterns = cfg.get("exclude_patterns", [])

    policy = (cfg.get("indexing", {}) or {}).get("policy", cfg.get("policy", "hash"))
    if policy not in {"hash", "mtime"}:
        policy = "hash"

    csv_cfg = cfg.get("csv", {}) or {}
    csv_delim = csv_cfg.get("delimiter", ",")
    csv_quote = csv_cfg.get("quotechar", '"')
    csv_header = bool(csv_cfg.get("header", True))
    csv_columns = csv_cfg.get("columns", None)
    encoding_default = cfg.get("encoding_default", "utf-8")

    # Start run
    run = IngestionRun(
        source_id=source.id,
        source_type=source.type,
        source_scope=str(input_dir),
        params={"policy": policy, "include_ext": include_ext, "recursive": recursive},
        status="running",
    )
    session.add(run)
    session.flush()  # ensure run_id

    # LOG: inicio del run
    log.info(
        "run.start source_id=%s scope=%s policy=%s include_ext=%s recursive=%s",
        source.id, str(input_dir), policy, include_ext, recursive
    )

    stats = {
        "scanned": 0,
        "skipped_unchanged": 0,
        "new_docs": 0,
        "updated_docs": 0,
        "failed": 0,
        "total_chunks": 0,
        "errors": [],
    }

    try:
        files = _enumerate_files(input_dir, recursive, include_ext, exclude_patterns)
        # LOG: resumen de enumeración
        log.debug("enumerate files=%d dir=%s", len(files), str(input_dir))

        for file_path in files:
            stats["scanned"] += 1
            # LOG: comienzo por fichero
            log.debug("scan file=%s", str(file_path))

            try:
                changed, doc_meta = _should_process(session, source, file_path, policy)
                if not changed:
                    stats["skipped_unchanged"] += 1
                    # LOG: skip por no cambiado
                    log.info("skip.unchanged file=%s", str(file_path))
                    continue

                # Extract text + metadata
                ext = file_path.suffix.lower().lstrip(".")
                if ext == "pdf":
                    raw_text, meta = load_pdf(file_path)
                    mime = "application/pdf"
                elif ext == "docx":
                    raw_text, meta = load_docx(file_path)
                    mime = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
                elif ext == "txt":
                    raw_text, meta = load_txt(file_path, default_encoding=encoding_default)
                    mime = "text/plain"
                elif ext == "csv":
                    raw_text, meta = load_csv(
                        file_path,
                        delimiter=csv_delim,
                        quotechar=csv_quote,
                        header=csv_header,
                        columns=csv_columns,
                        encoding=encoding_default,
                    )
                    mime = "text/csv"
                else:
                    raise ValueError(f"Unsupported extension: .{ext}")

                # LOG: tras cargar el fichero
                log.info(
                    "process file=%s ext=%s size=%s",
                    str(file_path), ext,
                    (file_path.stat().st_size if file_path.exists() else None)
                )

                # Clean & hash
                cleaned = clean_text(raw_text)
                normalized_hash = text_sha256(cleaned)

                # Build/refresh Document row
                doc_id = _stable_doc_id(source, input_dir, file_path)
                doc = session.get(Document, doc_id)
                is_new = doc is None
                if is_new:
                    doc = Document(
                        doc_id=doc_id,
                        source_id=source.id,
                        source_type=source.type,
                        uri=str(file_path),
                    )

                doc.title = meta.get("title") if isinstance(meta, dict) else None
                doc.lang = None  # optional: language detection later
                doc.mime = mime
                doc.version = None
                doc.collected_at = run.started_at
                doc.size_bytes = file_path.stat().st_size if file_path.exists() else None
                doc.origin_hash = doc_meta["origin_hash"]
                doc.normalized_hash = normalized_hash

                # Replace chunks
                if not is_new:
                    for c in list(doc.chunks):
                        session.delete(c)

                # Split into chunks
                rag_cfg = (source.config.get("rag", {}) if isinstance(source.config, dict) else {}) or {}
                split_opts = SplitOptions(
                    chunk_size=int(rag_cfg.get("chunk_size", 512)),
                    chunk_overlap=int(rag_cfg.get("chunk_overlap", 64)),
                )
                pieces = split_text(cleaned, split_opts)

                # LOG: tras split
                log.info("split file=%s chunks=%d", str(file_path), len(pieces))

                for ch in pieces:
                    chunk_id = f"{doc_id}:{ch.position:06d}"
                    session.add(
                        Chunk(
                            chunk_id=chunk_id,
                            doc_id=doc_id,
                            position=ch.position,
                            content=ch.text,
                            tokens=len(ch.text),
                            lang=doc.lang,
                            title=doc.title,
                            retrieval_tags={"source_id": source.id, "source_type": source.type},
                            provenance={
                                "run_id": run.run_id,
                                "loader": ext,
                                "cleaner": "default",
                                "split": asdict(split_opts),
                            },
                        )
                    )

                session.add(doc)
                # LOG: actualizar stats “en vivo” y flush (para poder ver progreso vía API)
                stats["total_chunks"] += len(pieces)
                if is_new:
                    stats["new_docs"] += 1
                else:
                    stats["updated_docs"] += 1
                run.stats = stats  # ← progreso en tiempo real
                session.flush()

                # Optional: write artifacts
                _append_jsonl(
                    Path("data/processed/documents/docs.jsonl"),
                    {
                        "doc_id": doc.doc_id,
                        "uri": doc.uri,
                        "title": doc.title,
                        "mime": doc.mime,
                        "origin_hash": doc.origin_hash,
                        "normalized_hash": doc.normalized_hash,
                    },
                )
                for ch in pieces:
                    _append_jsonl(
                        Path("data/processed/documents/chunks.jsonl"),
                        {
                            "chunk_id": f"{doc_id}:{ch.position:06d}",
                            "doc_id": doc_id,
                            "position": ch.position,
                            "len": len(ch.text),
                        },
                    )

            except Exception as e:
                stats["failed"] += 1
                stats["errors"].append({"file": str(file_path), "error": str(e)})
                # LOG: traza completa por fichero
                log.exception("process.error file=%s", str(file_path))
                session.flush()
                continue

        run.status = "success" if stats["failed"] == 0 else ("partial" if stats["new_docs"] + stats["updated_docs"] > 0 else "failed")
    except Exception:
        run.status = "failed"
        stats["errors"].append({"fatal": "See logs"})
        # LOG: traza fatal del run
        log.exception("run.fatal source_id=%s", source.id)
        raise
    finally:
        run.stats = stats
        run.ended_at = datetime.utcnow()
        session.flush()
        # LOG: fin del run
        log.info(
            "run.end source_id=%s status=%s scanned=%d new=%d updated=%d skipped=%d failed=%d chunks=%d",
            source.id, run.status, stats["scanned"], stats["new_docs"], stats["updated_docs"],
            stats["skipped_unchanged"], stats["failed"], stats["total_chunks"]
        )

    return run



# ------------------------------
# Helpers
# ------------------------------
def _enumerate_files(root: Path, recursive: bool, include_ext: List[str], exclude_patterns: Iterable[str]) -> List[Path]:
    if not root.exists():
        return []
    paths: List[Path] = []
    it = root.rglob("*") if recursive else root.glob("*")
    for p in it:
        if not p.is_file():
            continue
        if p.suffix.lower().lstrip(".") not in include_ext:
            continue
        rel = str(p.relative_to(root)).replace("\\", "/")
        if any(fnmatch.fnmatch(rel, pat) for pat in exclude_patterns):
            continue
        paths.append(p)
    return paths


def _stable_doc_id(source: Source, input_dir: Path, file_path: Path) -> str:
    # Use a stable id from source.id + relative path (lowercased)
    try:
        rel = file_path.relative_to(input_dir)
    except Exception:
        rel = file_path
    key = f"{source.id}:{str(rel).replace('\\','/').lower()}"
    return hashlib.sha256(key.encode("utf-8")).hexdigest()


def _origin_sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def _should_process(session, source: Source, path: Path, policy: str) -> Tuple[bool, Dict[str, Any]]:
    """Return (changed?, meta) for this path under the selected policy."""
    origin = _origin_sha256(path)
    base_dir = Path(source.config.get("input_dir", "data/raw/documents")).resolve() if isinstance(source.config, dict) else Path("data/raw/documents").resolve()
    doc_id = _stable_doc_id(source, base_dir, path)
    doc = session.get(Document, doc_id)

    if doc is None:
        return True, {"doc_id": doc_id, "origin_hash": origin}

    if policy == "hash":
        return (doc.origin_hash != origin), {"doc_id": doc_id, "origin_hash": origin}

    if policy == "mtime":
        # Placeholder for future mtime-based logic
        return True, {"doc_id": doc_id, "origin_hash": origin}

    return True, {"doc_id": doc_id, "origin_hash": origin}


def _append_jsonl(path: Path, row: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(row, ensure_ascii=False) + "\n")
