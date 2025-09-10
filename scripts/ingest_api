#!/usr/bin/env python
# scripts/ingest_api.py
from __future__ import annotations
import os, sys, json, time, math, argparse
from typing import Dict, Any, List
import requests
from app.extensions.db import db, SessionLocal
from app.models import Source, Document, Chunk, IngestionRun
from app.ingest.textops import clean_text, chunk_text
from app.ingest.dedupe import dedupe_chunks
from app.ingest.canonical import canonical_chunk_meta, normalize_path

def load_config(path: str) -> Dict[str, Any]:
    if path.endswith((".yml", ".yaml")):
        import yaml
        return yaml.safe_load(open(path, "r", encoding="utf-8"))
    return json.load(open(path, "r", encoding="utf-8"))

def build_session(auth_cfg: Dict[str, Any]) -> requests.Session:
    s = requests.Session()
    if not auth_cfg: return s
    t = (auth_cfg.get("type") or "").lower()
    if t == "bearer":
        token = os.environ.get(auth_cfg["token_env"], "")
        if not token: raise RuntimeError("API token missing in env")
        s.headers["Authorization"] = f"Bearer {token}"
    elif t == "apikey":
        header = auth_cfg.get("header", "X-API-Key")
        token = os.environ.get(auth_cfg["token_env"], "")
        s.headers[header] = token
    return s

def extract_field(obj: Dict[str, Any], path: str) -> str:
    cur = obj
    for p in path.split("."):
        if isinstance(cur, dict) and p in cur: cur = cur[p]
        else: return ""
    return cur if isinstance(cur, str) else json.dumps(cur, ensure_ascii=False)

def render_template(tpl: str, obj: Dict[str, Any], source_name: str) -> str:
    data = {"source_name": source_name, **obj}
    try:
        return tpl.format(**data)
    except KeyError:
        return tpl

def iter_pages(session: requests.Session, cfg: Dict[str, Any]):
    base = cfg["base_url"].rstrip("/")
    resource = cfg["resource"]
    pag = cfg.get("pagination", {}) or {}
    style = pag.get("style", "page_param")

    page, size = 1, pag.get("size", 100)
    max_pages = pag.get("max_pages", 1000)

    url = f"{base}{resource}"
    for _ in range(max_pages):
        params = {}
        if style == "page_param":
            params[pag.get("page_param","page")] = page
            params[pag.get("size_param","size")] = size

        r = session.get(url, params=params, timeout=30)
        r.raise_for_status()
        data = r.json()

        # suponer lista en 'data' o raíz; ajustar si tu API usa otra clave
        items = data.get("data", data if isinstance(data, list) else [])
        yield items

        # avanzar
        if style == "page_param":
            if not items or len(items) < size: break
            page += 1
        elif style == "link_next":
            next_url = data.get("links", {}).get("next")
            if not next_url: break
            url = next_url
        elif style == "offset_limit":
            # implementar si lo necesitas
            raise NotImplementedError("offset_limit not implemented")
        elif style == "cursor":
            # implementar si lo necesitas
            raise NotImplementedError("cursor not implemented")
        time.sleep(60.0 / max(1, (cfg.get("rate_limit", {}) or {}).get("max_per_minute", 60)))

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--source-id", type=int, required=True)
    ap.add_argument("--config", required=True, help="Ruta a YAML/JSON de esta fuente (opcional si Source.config ya lo trae)")
    ap.add_argument("--run-id", type=int, default=None)
    ap.add_argument("--dump-html", action="store_true")  # compat artefactos
    ap.add_argument("--preview", action="store_true")
    ap.add_argument("--verbose", action="store_true")
    args = ap.parse_args()

    cfg = load_config(args.config)
    session = SessionLocal()
    source = session.query(Source).get(args.source_id)
    if not source or source.type != "api":
        raise SystemExit("Source no existe o no es de tipo 'api'")

    run = IngestionRun(source_id=source.id, status="running", meta={"config_schema_version": cfg.get("schema_version")})
    session.add(run); session.commit(); session.refresh(run)

    run_dir = os.getenv("RUN_DIR", f"data/processed/runs/api/run_{run.id:04d}")
    os.makedirs(run_dir, exist_ok=True)
    log_path = os.path.join(run_dir, "stdout.txt")
    summary_path = os.path.join(run_dir, "summary.json")

    s = build_session(cfg.get("auth", {}))
    totals = {"n_items": 0, "n_docs": 0, "n_chunks": 0, "dedupe_exact": 0, "dedupe_near": 0}
    try:
        for page_items in iter_pages(s, cfg):
            for item in page_items:
                totals["n_items"] += 1
                title = render_template(cfg["mapping"]["title_template"], item, source.name)
                path = render_template(cfg["mapping"]["path_template"], item, source.name)
                text_parts = [extract_field(item, f) for f in cfg["mapping"]["text_fields"]]
                raw_text = "\n\n".join([t for t in text_parts if t])

                cleaned = clean_text(raw_text)
                chunks = chunk_text(cleaned, target=cfg.get("normalize",{}).get("chunk_target",700),
                                    overlap=cfg.get("normalize",{}).get("chunk_overlap",0.12))
                chunks, ex_rm, nr_rm = dedupe_chunks(chunks, near_threshold=cfg.get("normalize",{}).get("neardup_threshold",0.92))
                totals["dedupe_exact"] += ex_rm; totals["dedupe_near"] += nr_rm

                doc = Document(source_id=source.id, path=path, title=title, ext="json",
                               size=len(raw_text.encode("utf-8")), mtime_ns=int(time.time()*1e9),
                               hash=None, meta={"path_normalized": normalize_path(path), "api_schema_version": cfg.get("schema_version")})
                session.add(doc); session.flush()
                totals["n_docs"] += 1

                for i, c in enumerate(chunks):
                    meta = canonical_chunk_meta(document_title=title, document_path=path,
                                                chunk_index=i, text=c, source_id=source.id, document_id=doc.id)
                    ch = Chunk(source_id=source.id, document_id=doc.id, ordinal=i, text=c, meta=meta)
                    session.add(ch); totals["n_chunks"] += 1
                session.commit()
        run.status = "success"
    except Exception as e:
        run.status = "error"
        run.meta = {**(run.meta or {}), "error": str(e)}
        session.commit()
        raise
    finally:
        open(summary_path, "w", encoding="utf-8").write(json.dumps({"summary_totals": totals}, indent=2, ensure_ascii=False))
        open(log_path, "a", encoding="utf-8").write(json.dumps({"event":"end","totals":totals}, ensure_ascii=False)+"\n")
        session.close()

if __name__ == "__main__":
    main()
