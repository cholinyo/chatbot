from __future__ import annotations

import fnmatch
import hashlib
import json
import logging
import re
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Dict, Any, Iterable, List, Tuple, Optional

log = logging.getLogger("ingestion")

from app.extensions.db import get_session
from app.models.source import Source
from app.models.document import Document
from app.models.chunk import Chunk
from app.models.ingestion_run import IngestionRun

# ============================
# IMPORTS CONDICIONALES
# ============================

# Procesadores de texto
try:
    from app.rag.processing.cleaners import clean_text, text_sha256
except ImportError as e:
    log.warning("RAG cleaners not available: %s", e)
    clean_text = None
    text_sha256 = None

try:
    from app.rag.processing.splitters import SplitOptions, split_text
except ImportError as e:
    log.warning("RAG splitters not available: %s", e)
    SplitOptions = None
    split_text = None

# Loaders por tipo de archivo
try:
    from app.rag.loaders.pdf_loader import load_pdf
except ImportError as e:
    log.warning("PDF loader not available - install PyPDF2: %s", e)
    load_pdf = None

try:
    from app.rag.loaders.docx_loader import load_docx
except ImportError as e:
    log.warning("DOCX loader not available - install python-docx: %s", e)
    load_docx = None

try:
    from app.rag.loaders.txt_loader import load_txt
except ImportError as e:
    log.warning("TXT loader not available: %s", e)
    load_txt = None

try:
    from app.rag.loaders.csv_loader import load_csv
except ImportError as e:
    log.warning("CSV loader not available - install pandas: %s", e)
    load_csv = None

# ============================
# FALLBACKS SIMPLES
# ============================

@dataclass
class FallbackSplitOptions:
    """Fallback para SplitOptions cuando no está disponible."""
    chunk_size: int = 512
    chunk_overlap: int = 64

@dataclass
class FallbackChunk:
    """Fallback para chunks cuando split_text no está disponible."""
    text: str
    position: int


def fallback_clean_text(text: str) -> str:
    """Limpieza básica de texto cuando clean_text no está disponible."""
    if not text:
        return ""
    
    # Normalizar espacios en blanco
    text = re.sub(r'\s+', ' ', text)
    text = text.strip()
    
    # Remover caracteres de control
    text = ''.join(char for char in text if ord(char) >= 32 or char in '\n\t')
    
    return text


def fallback_text_sha256(text: str) -> str:
    """Hash SHA256 básico cuando text_sha256 no está disponible."""
    return hashlib.sha256(text.encode('utf-8')).hexdigest()


def fallback_split_text(text: str, options: Any) -> List[FallbackChunk]:
    """División básica de texto cuando split_text no está disponible."""
    if not text:
        return []
    
    chunk_size = getattr(options, 'chunk_size', 512)
    chunk_overlap = getattr(options, 'chunk_overlap', 64)
    
    chunks = []
    start = 0
    position = 0
    
    while start < len(text):
        end = start + chunk_size
        chunk_text = text[start:end]
        
        if chunk_text.strip():
            chunks.append(FallbackChunk(text=chunk_text, position=position))
            position += 1
        
        start = end - chunk_overlap if chunk_overlap > 0 else end
    
    return chunks


def fallback_load_txt(file_path: Path, default_encoding: str = "utf-8") -> Tuple[str, Dict[str, Any]]:
    """Carga básica de archivos de texto."""
    try:
        content = file_path.read_text(encoding=default_encoding, errors='ignore')
        meta = {
            "title": file_path.stem,
            "loader": "fallback_txt",
            "encoding": default_encoding
        }
        return content, meta
    except Exception as e:
        log.error("Error loading text file %s: %s", file_path, e)
        return "", {"title": file_path.stem, "error": str(e)}


def fallback_load_csv(file_path: Path, delimiter: str = ",", encoding: str = "utf-8", **kwargs) -> Tuple[str, Dict[str, Any]]:
    """Carga básica de archivos CSV sin pandas."""
    try:
        content = file_path.read_text(encoding=encoding, errors='ignore')
        
        # Convertir CSV a texto plano
        lines = content.splitlines()
        if lines:
            # Primera línea como cabecera
            header = lines[0] if lines else ""
            text_content = f"CSV Data from {file_path.name}\n"
            text_content += f"Headers: {header}\n\n"
            
            # Algunas filas de muestra (máximo 20)
            sample_lines = lines[1:21] if len(lines) > 1 else []
            for i, line in enumerate(sample_lines, 1):
                text_content += f"Row {i}: {line}\n"
            
            if len(lines) > 21:
                text_content += f"\n... and {len(lines) - 21} more rows"
        else:
            text_content = f"Empty CSV file: {file_path.name}"
            
        meta = {
            "title": file_path.stem,
            "loader": "fallback_csv",
            "rows": len(lines),
            "delimiter": delimiter
        }
        return text_content, meta
        
    except Exception as e:
        log.error("Error loading CSV file %s: %s", file_path, e)
        return f"Error loading CSV: {e}", {"title": file_path.stem, "error": str(e)}


# ============================
# FUNCIONES PÚBLICAS
# ============================

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


# ============================
# IMPLEMENTACIÓN PRINCIPAL
# ============================

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

    # Iniciar run
    run = IngestionRun(
        source_id=source.id,
        source_type=source.type,
        source_scope=str(input_dir),
        params={"policy": policy, "include_ext": include_ext, "recursive": recursive},
        status="running",
    )
    session.add(run)
    session.flush()  # asegurar run_id

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
        "warnings": [],
    }

    # Verificar disponibilidad de dependencias críticas
    missing_deps = []
    if clean_text is None:
        missing_deps.append("rag.processing.cleaners")
        stats["warnings"].append("Using fallback text cleaning - install rag processing modules")
    
    if split_text is None:
        missing_deps.append("rag.processing.splitters")
        stats["warnings"].append("Using fallback text splitting - install rag processing modules")

    if missing_deps:
        log.warning("Missing RAG dependencies: %s - using fallbacks", missing_deps)

    try:
        files = _enumerate_files(input_dir, recursive, include_ext, exclude_patterns)
        log.debug("enumerate files=%d dir=%s", len(files), str(input_dir))

        for file_path in files:
            stats["scanned"] += 1
            log.debug("scan file=%s", str(file_path))

            try:
                changed, doc_meta = _should_process(session, source, file_path, policy)
                if not changed:
                    stats["skipped_unchanged"] += 1
                    log.info("skip.unchanged file=%s", str(file_path))
                    continue

                # Extraer texto y metadatos
                ext = file_path.suffix.lower().lstrip(".")
                raw_text, meta = "", {}
                mime = "application/octet-stream"

                if ext == "pdf":
                    if load_pdf is None:
                        raise ValueError("PDF processing not available - install PyPDF2")
                    raw_text, meta = load_pdf(file_path)
                    mime = "application/pdf"
                    
                elif ext == "docx":
                    if load_docx is None:
                        raise ValueError("DOCX processing not available - install python-docx")
                    raw_text, meta = load_docx(file_path)
                    mime = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
                    
                elif ext == "txt":
                    if load_txt is not None:
                        raw_text, meta = load_txt(file_path, default_encoding=encoding_default)
                    else:
                        raw_text, meta = fallback_load_txt(file_path, encoding_default)
                    mime = "text/plain"
                    
                elif ext == "csv":
                    if load_csv is not None:
                        raw_text, meta = load_csv(
                            file_path,
                            delimiter=csv_delim,
                            quotechar=csv_quote,
                            header=csv_header,
                            columns=csv_columns,
                            encoding=encoding_default,
                        )
                    else:
                        raw_text, meta = fallback_load_csv(
                            file_path, 
                            delimiter=csv_delim, 
                            encoding=encoding_default
                        )
                    mime = "text/csv"
                    
                else:
                    raise ValueError(f"Unsupported extension: .{ext}")

                log.info(
                    "process file=%s ext=%s size=%s",
                    str(file_path), ext,
                    (file_path.stat().st_size if file_path.exists() else None)
                )

                # Limpiar y hashear
                if clean_text is not None:
                    cleaned = clean_text(raw_text)
                else:
                    cleaned = fallback_clean_text(raw_text)
                    
                if text_sha256 is not None:
                    normalized_hash = text_sha256(cleaned)
                else:
                    normalized_hash = fallback_text_sha256(cleaned)

                # Construir/actualizar Document
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
                doc.lang = None
                doc.mime = mime
                doc.version = None
                doc.collected_at = run.started_at
                doc.size_bytes = file_path.stat().st_size if file_path.exists() else None
                doc.origin_hash = doc_meta["origin_hash"]
                doc.normalized_hash = normalized_hash

                # Reemplazar chunks
                if not is_new:
                    for c in list(doc.chunks):
                        session.delete(c)

                # Dividir en chunks
                rag_cfg = (source.config.get("rag", {}) if isinstance(source.config, dict) else {}) or {}
                
                if SplitOptions is not None and split_text is not None:
                    split_opts = SplitOptions(
                        chunk_size=int(rag_cfg.get("chunk_size", 512)),
                        chunk_overlap=int(rag_cfg.get("chunk_overlap", 64)),
                    )
                    pieces = split_text(cleaned, split_opts)
                else:
                    # Usar fallback
                    split_opts = FallbackSplitOptions(
                        chunk_size=int(rag_cfg.get("chunk_size", 512)),
                        chunk_overlap=int(rag_cfg.get("chunk_overlap", 64)),
                    )
                    pieces = fallback_split_text(cleaned, split_opts)

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
                                "cleaner": "fallback" if clean_text is None else "default",
                                "split": asdict(split_opts) if hasattr(split_opts, '__dict__') else {
                                    "chunk_size": split_opts.chunk_size,
                                    "chunk_overlap": split_opts.chunk_overlap
                                },
                            },
                        )
                    )

                session.add(doc)
                stats["total_chunks"] += len(pieces)
                if is_new:
                    stats["new_docs"] += 1
                else:
                    stats["updated_docs"] += 1
                run.stats = stats  # progreso en tiempo real
                session.flush()

                # Escribir artefactos opcionales
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
                error_msg = f"file={str(file_path)} error={str(e)}"
                stats["errors"].append({"file": str(file_path), "error": str(e)})
                log.exception("process.error %s", error_msg)
                session.flush()
                continue

        run.status = "success" if stats["failed"] == 0 else ("partial" if stats["new_docs"] + stats["updated_docs"] > 0 else "failed")
        
    except Exception as e:
        run.status = "failed"
        stats["errors"].append({"fatal": str(e)})
        log.exception("run.fatal source_id=%s", source.id)
        raise
    finally:
        run.stats = stats
        run.ended_at = datetime.utcnow()
        session.flush()
        log.info(
            "run.end source_id=%s status=%s scanned=%d new=%d updated=%d skipped=%d failed=%d chunks=%d warnings=%d",
            source.id, run.status, stats["scanned"], stats["new_docs"], stats["updated_docs"],
            stats["skipped_unchanged"], stats["failed"], stats["total_chunks"], len(stats.get("warnings", []))
        )

    return run


# ============================
# FUNCIONES DE UTILIDAD
# ============================

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
    """Generar ID estable desde source.id + ruta relativa."""
    try:
        rel = file_path.relative_to(input_dir)
    except Exception:
        rel = file_path
    key = f"{source.id}:{str(rel).replace('\\','/').lower()}"
    return hashlib.sha256(key.encode("utf-8")).hexdigest()


def _origin_sha256(path: Path) -> str:
    """Hash SHA256 del archivo original."""
    h = hashlib.sha256()
    try:
        with path.open("rb") as f:
            for chunk in iter(lambda: f.read(65536), b""):
                h.update(chunk)
        return h.hexdigest()
    except Exception as e:
        log.error("Error calculating hash for %s: %s", path, e)
        # Fallback: usar timestamp + tamaño
        stat = path.stat()
        fallback_data = f"{path.name}:{stat.st_size}:{stat.st_mtime}"
        return hashlib.sha256(fallback_data.encode()).hexdigest()


def _should_process(session, source: Source, path: Path, policy: str) -> Tuple[bool, Dict[str, Any]]:
    """Determinar si procesar este archivo bajo la política seleccionada."""
    origin = _origin_sha256(path)
    base_dir = Path(source.config.get("input_dir", "data/raw/documents")).resolve() if isinstance(source.config, dict) else Path("data/raw/documents").resolve()
    doc_id = _stable_doc_id(source, base_dir, path)
    doc = session.get(Document, doc_id)

    if doc is None:
        return True, {"doc_id": doc_id, "origin_hash": origin}

    if policy == "hash":
        return (doc.origin_hash != origin), {"doc_id": doc_id, "origin_hash": origin}

    if policy == "mtime":
        # Placeholder para lógica futura basada en mtime
        return True, {"doc_id": doc_id, "origin_hash": origin}

    return True, {"doc_id": doc_id, "origin_hash": origin}


def _append_jsonl(path: Path, row: Dict[str, Any]) -> None:
    """Append línea JSON a archivo."""
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
    except Exception as e:
        log.error("Error writing to JSONL %s: %s", path, e)