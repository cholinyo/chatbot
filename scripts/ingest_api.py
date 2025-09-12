#!/usr/bin/env python
# scripts/ingest_api.py - Ingesta de APIs REST adaptado a la arquitectura del proyecto
from __future__ import annotations
import os, sys, json, time, argparse
from typing import Dict, Any, List
import requests
from pathlib import Path

# Imports corregidos para la arquitectura del proyecto
from app.extensions.db import get_session
from app.models.source import Source
from app.models.document import Document
from app.models.chunk import Chunk
from app.models.ingestion_run import IngestionRun

# Procesamiento de texto usando la arquitectura existente
from app.rag.processing.cleaners import clean_text, text_sha256
from app.rag.processing.splitters import split_text, SplitOptions

# Funciones auxiliares específicas para APIs (las crearemos)
from app.blueprints.ingestion.api_utils import (
    dedupe_chunks,
    canonical_chunk_meta,
    normalize_api_path,
    stable_api_doc_id
)


def load_config(path: str) -> Dict[str, Any]:
    """Carga configuración desde YAML o JSON."""
    if path.endswith((".yml", ".yaml")):
        import yaml
        return yaml.safe_load(open(path, "r", encoding="utf-8"))
    return json.load(open(path, "r", encoding="utf-8"))


def build_session(auth_cfg: Dict[str, Any]) -> requests.Session:
    """Construye sesión HTTP con autenticación configurada."""
    s = requests.Session()
    if not auth_cfg: 
        return s
    
    auth_type = (auth_cfg.get("type") or "").lower()
    
    if auth_type == "bearer":
        token_env = auth_cfg.get("token_env", "")
        token = os.environ.get(token_env, "")
        if not token: 
            raise RuntimeError(f"API token missing in environment variable: {token_env}")
        s.headers["Authorization"] = f"Bearer {token}"
        
    elif auth_type == "apikey":
        header = auth_cfg.get("header", "X-API-Key")
        token_env = auth_cfg.get("token_env", "")
        token = os.environ.get(token_env, "")
        if not token:
            raise RuntimeError(f"API key missing in environment variable: {token_env}")
        s.headers[header] = token
        
    elif auth_type == "basic":
        username = os.environ.get(auth_cfg.get("username_env", ""), "")
        password = os.environ.get(auth_cfg.get("password_env", ""), "")
        if username and password:
            s.auth = (username, password)
            
    return s


def extract_field(obj: Dict[str, Any], path: str) -> str:
    """Extrae campo usando dot notation (ej: 'data.items.title')."""
    cur = obj
    for part in path.split("."):
        if isinstance(cur, dict) and part in cur: 
            cur = cur[part]
        else: 
            return ""
    return cur if isinstance(cur, str) else json.dumps(cur, ensure_ascii=False)


def render_template(template: str, obj: Dict[str, Any], source_name: str) -> str:
    """Renderiza template con datos del objeto API."""
    data = {"source_name": source_name, **obj}
    try:
        return template.format(**data)
    except KeyError as e:
        print(f"Template error: {e} - Template: {template}")
        return template


def iter_pages(session: requests.Session, cfg: Dict[str, Any]):
    """Generador que itera sobre páginas de la API con soporte para múltiples estrategias."""
    base = cfg["base_url"].rstrip("/")
    resource = cfg["resource"]
    pag = cfg.get("pagination", {}) or {}
    style = pag.get("style", "page_param")

    # Configuración de paginación
    page, size = 1, pag.get("size", 100)
    max_pages = pag.get("max_pages", 1000)
    
    # Rate limiting
    rate_limit = cfg.get("rate_limit", {})
    max_per_minute = rate_limit.get("max_per_minute", 60)
    delay_between_requests = 60.0 / max(1, max_per_minute)

    url = f"{base}{resource}"
    
    for page_num in range(max_pages):
        params = pag.get("extra_params", {}).copy()
        
        # Estrategias de paginación
        if style == "page_param":
            params[pag.get("page_param", "page")] = page
            params[pag.get("size_param", "size")] = size
            
        elif style == "offset_limit":
            offset = (page - 1) * size
            params[pag.get("offset_param", "offset")] = offset
            params[pag.get("limit_param", "limit")] = size
            
        try:
            print(f"[API] Fetching page {page} from {url} with params: {params}")
            r = session.get(url, params=params, timeout=30)
            r.raise_for_status()
            data = r.json()

            # Extraer items según configuración
            items_path = pag.get("items_path", "data")
            if items_path:
                items = extract_field(data, items_path)
                if isinstance(items, str):
                    try:
                        items = json.loads(items)
                    except:
                        items = []
            else:
                items = data if isinstance(data, list) else []
                
            if not isinstance(items, list):
                items = []

            yield items
            
            # Verificar si hay más páginas
            if style == "page_param":
                if not items or len(items) < size:
                    break
                page += 1
                
            elif style == "link_next":
                next_url = extract_field(data, pag.get("next_link_path", "links.next"))
                if not next_url:
                    break
                url = next_url
                
            elif style == "offset_limit":
                if not items or len(items) < size:
                    break
                page += 1
                
            elif style == "cursor":
                cursor_path = pag.get("cursor_path", "next_cursor")
                next_cursor = extract_field(data, cursor_path)
                if not next_cursor:
                    break
                params[pag.get("cursor_param", "cursor")] = next_cursor
                
            # Rate limiting
            if delay_between_requests > 0:
                time.sleep(delay_between_requests)
                
        except requests.RequestException as e:
            print(f"[API] Error fetching page {page}: {e}")
            break
        except Exception as e:
            print(f"[API] Unexpected error on page {page}: {e}")
            break


def main():
    """Función principal del script de ingesta de APIs."""
    ap = argparse.ArgumentParser(description="Ingesta de datos desde APIs REST")
    ap.add_argument("--source-id", type=int, required=True, help="ID de la fuente API")
    ap.add_argument("--config", required=True, help="Ruta al archivo de configuración YAML/JSON")
    ap.add_argument("--run-id", type=int, default=None, help="ID del run (opcional)")
    ap.add_argument("--preview", action="store_true", help="Solo preview, no guardar en BD")
    ap.add_argument("--verbose", action="store_true", help="Output verboso")
    args = ap.parse_args()

    # Cargar configuración
    cfg = load_config(args.config)
    
    # Obtener fuente de la BD
    with get_session() as session:
        source = session.get(Source, args.source_id)
        if not source or source.type != "api":
            raise SystemExit(f"Source {args.source_id} no existe o no es de tipo 'api'")

        # Crear run de ingesta
        run = IngestionRun(
            source_id=source.id,
            source_type=source.type,
            source_scope=f"{cfg.get('base_url', '')}{cfg.get('resource', '')}",
            status="running",
            params={
                "config_schema_version": cfg.get("schema_version", "1.0"),
                "base_url": cfg.get("base_url"),
                "resource": cfg.get("resource"),
                "pagination_style": cfg.get("pagination", {}).get("style", "page_param")
            }
        )
        session.add(run)
        session.flush()  # Asegurar run_id

        # Directorio de artefactos
        run_dir = Path(os.getenv("RUN_DIR", f"data/processed/runs/api/run_{run.id:04d}"))
        run_dir.mkdir(parents=True, exist_ok=True)
        
        log_path = run_dir / "stdout.txt"
        summary_path = run_dir / "summary.json"

        # Crear sesión HTTP con autenticación
        http_session = build_session(cfg.get("auth", {}))
        
        # Estadísticas de la ingesta
        totals = {
            "n_items": 0,
            "n_docs": 0, 
            "n_chunks": 0,
            "dedupe_exact": 0,
            "dedupe_near": 0,
            "errors": []
        }

        try:
            # Configuración de chunking
            chunk_config = cfg.get("chunking", {})
            chunk_size = chunk_config.get("size", 700)
            chunk_overlap = chunk_config.get("overlap", 0.12)
            
            # Si overlap es ratio (0-1), convertir a caracteres
            if isinstance(chunk_overlap, float) and 0 <= chunk_overlap <= 1:
                chunk_overlap = int(chunk_size * chunk_overlap)
                
            split_opts = SplitOptions(
                chunk_size=chunk_size,
                chunk_overlap=chunk_overlap
            )
            
            # Configuración de deduplicación
            dedupe_config = cfg.get("deduplication", {})
            near_threshold = dedupe_config.get("near_threshold", 0.92)

            # Procesar páginas de la API
            for page_items in iter_pages(http_session, cfg):
                for item in page_items:
                    totals["n_items"] += 1
                    
                    try:
                        # Extraer campos usando mapping de configuración
                        mapping = cfg["mapping"]
                        title = render_template(mapping["title_template"], item, source.name)
                        path = render_template(mapping["path_template"], item, source.name)
                        
                        # Extraer y combinar campos de texto
                        text_parts = []
                        for field_path in mapping["text_fields"]:
                            text_value = extract_field(item, field_path)
                            if text_value:
                                text_parts.append(text_value)
                        
                        raw_text = "\n\n".join(text_parts)
                        
                        if not raw_text.strip():
                            print(f"[SKIP] Item sin contenido de texto: {path}")
                            continue

                        # Limpiar texto
                        cleaned_text = clean_text(raw_text)
                        normalized_hash = text_sha256(cleaned_text)
                        
                        # Crear documento
                        doc_id = stable_api_doc_id(source, path)
                        doc = session.get(Document, doc_id)
                        is_new = doc is None
                        
                        if is_new:
                            doc = Document(
                                doc_id=doc_id,
                                source_id=source.id,
                                source_type=source.type,
                                uri=path,
                            )
                            
                        # Actualizar metadatos del documento
                        doc.title = title
                        doc.mime = "application/json"
                        doc.collected_at = run.started_at
                        doc.size_bytes = len(raw_text.encode("utf-8"))
                        doc.origin_hash = text_sha256(raw_text)  # Hash del contenido original
                        doc.normalized_hash = normalized_hash
                        
                        # Limpiar chunks existentes si es actualización
                        if not is_new:
                            for chunk in list(doc.chunks):
                                session.delete(chunk)

                        # Dividir en chunks
                        chunk_pieces = split_text(cleaned_text, split_opts)
                        
                        # Deduplicar chunks
                        deduplicated_chunks, exact_removed, near_removed = dedupe_chunks(
                            [piece.text for piece in chunk_pieces], 
                            near_threshold=near_threshold
                        )
                        
                        totals["dedupe_exact"] += exact_removed
                        totals["dedupe_near"] += near_removed

                        # Crear chunks en la BD
                        for i, chunk_text in enumerate(deduplicated_chunks):
                            chunk_id = f"{doc_id}:{i:06d}"
                            
                            # Metadatos canónicos del chunk
                            chunk_meta = canonical_chunk_meta(
                                document_title=title,
                                document_path=path,
                                chunk_index=i,
                                text=chunk_text,
                                source_id=source.id,
                                document_id=doc_id,
                                api_endpoint=f"{cfg.get('base_url', '')}{cfg.get('resource', '')}",
                                original_item=item  # Mantener referencia al item original
                            )
                            
                            chunk = Chunk(
                                chunk_id=chunk_id,
                                doc_id=doc_id,
                                position=i,
                                content=chunk_text,
                                tokens=len(chunk_text.split()),  # Estimación simple
                                lang=None,  # TODO: detección de idioma
                                title=title,
                                retrieval_tags={
                                    "source_id": source.id,
                                    "source_type": source.type,
                                    "api_endpoint": cfg.get("resource", "")
                                },
                                provenance={
                                    "run_id": run.run_id,
                                    "loader": "api_rest",
                                    "api_config": {
                                        "base_url": cfg.get("base_url"),
                                        "resource": cfg.get("resource"),
                                        "pagination_style": cfg.get("pagination", {}).get("style")
                                    },
                                    "cleaner": "default",
                                    "split": {
                                        "chunk_size": split_opts.chunk_size,
                                        "chunk_overlap": split_opts.chunk_overlap
                                    }
                                }
                            )
                            session.add(chunk)
                            
                        totals["n_chunks"] += len(deduplicated_chunks)
                        session.add(doc)
                        totals["n_docs"] += 1
                        
                        # Flush periódico para evitar timeouts
                        if totals["n_docs"] % 10 == 0:
                            session.flush()
                            
                        if args.verbose:
                            print(f"[DOC] {doc_id} | {title} | {len(deduplicated_chunks)} chunks")
                            
                    except Exception as e:
                        error_msg = f"Error procesando item: {str(e)}"
                        totals["errors"].append(error_msg)
                        print(f"[ERROR] {error_msg}")
                        continue

            # Commit final
            session.commit()
            run.status = "success"
            
        except Exception as e:
            run.status = "error"
            error_msg = f"Error en ingesta API: {str(e)}"
            run.params = {**(run.params or {}), "error": error_msg}
            totals["errors"].append(error_msg)
            session.commit()
            raise
            
        finally:
            # Guardar artefactos
            summary_data = {
                "summary_totals": totals,
                "config": cfg,
                "run_info": {
                    "run_id": run.run_id,
                    "source_id": source.id,
                    "status": run.status,
                    "started_at": str(run.started_at),
                    "ended_at": str(run.ended_at) if run.ended_at else None
                }
            }
            
            summary_path.write_text(
                json.dumps(summary_data, indent=2, ensure_ascii=False),
                encoding="utf-8"
            )
            
            log_entry = json.dumps({
                "event": "end",
                "totals": totals,
                "timestamp": time.time()
            }, ensure_ascii=False)
            
            with log_path.open("a", encoding="utf-8") as f:
                f.write(log_entry + "\n")
                
            print(f"\n[SUMMARY] Ingesta completada:")
            print(f"  - Items procesados: {totals['n_items']}")
            print(f"  - Documentos: {totals['n_docs']}")
            print(f"  - Chunks: {totals['n_chunks']}")
            print(f"  - Dedupe exact: {totals['dedupe_exact']}")
            print(f"  - Dedupe near: {totals['dedupe_near']}")
            print(f"  - Errores: {len(totals['errors'])}")
            print(f"  - Artefactos en: {run_dir}")


if __name__ == "__main__":
    main()