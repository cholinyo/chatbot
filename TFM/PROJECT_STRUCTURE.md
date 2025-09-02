# TFM / PROJECT_STRUCTURE.md
> Última actualización: 2025-09-03 · TZ: Europe/Madrid  
> Repositorio: https://github.com/cholinyo/chatbot

Este documento describe en detalle la estructura del proyecto, los flujos de trabajo actuales y cómo ejecutar la **ingesta web** y los **scripts de control**. Incluye además pautas para mantener la documentación en `/docs` y un roadmap de las siguientes fases (indexación FAISS/Chroma y RAG completo).

---

## 1) Estructura del repositorio

```
chatbot/
├─ README.md
├─ .gitignore
├─ .env.example
├─ requirements.txt
├─ requirements-dev.txt
├─ wsgi.py
│
├─ config/
│  ├─ settings.example.toml      # Plantilla de configuración (sin secretos)
│  └─ logging.yaml               # Configuración de logging estructurado
│
├─ app/
│  ├─ __init__.py                # create_app(): carga config + extensiones
│  ├─ extensions/
│  │  ├─ db.py                   # SQLAlchemy engine + sesiones (scoped)
│  │  └─ logging.py              # Inicialización de logging estructurado
│  ├─ models/
│  │  ├─ source.py               # Source (docs/web) + config JSON
│  │  ├─ ingestion_run.py        # IngestionRun con status/meta
│  │  ├─ document.py             # Document (path/title/size/meta)
│  │  └─ chunk.py                # Chunk (document_id, index=ordinal, text, meta)
│  ├─ blueprints/
│  │  └─ admin/
│  │     ├─ routes_data_sources.py
│  │     ├─ routes_ingesta_docs.py
│  │     └─ routes_ingesta_web.py
│  ├─ templates/
│  │  └─ admin/
│  │     ├─ ingesta_docs.html
│  │     └─ ingesta_web.html
│  └─ static/
│     └─ css/custom.css
│
│  └─ rag/                        # *** Núcleo RAG ***
│     ├─ scrapers/
│     │  ├─ requests_bs4.py       # Crawler estático (BFS) + extracción de links
│     │  ├─ selenium_fetcher.py   # Renderizado dinámico (scroll, waits, viewport)
│     │  ├─ sitemap.py            # robots.txt + sitemap discovery & parsing
│     │  └─ web_normalizer.py     # Limpieza HTML → texto (boilerplate removal)
│     ├─ processing/
│     │  ├─ text_cleaning.py      # Normalizado (unicode, espacios, emojis, etc.)
│     │  ├─ chunking.py           # Políticas de splitting (len/overlap)
│     │  └─ dedup.py              # Detección y control de duplicados (futuro)
│     ├─ retrieval/               # (pendiente) BM25/Hybrid/Semantic
│     ├─ embeddings/              # (pendiente) wrappers de modelos de embeddings
│     ├─ generators/              # (pendiente) prompts RAG + invocación LLM
│     ├─ evaluation/              # (pendiente) métricas (Recall@k, MRR, etc.)
│     └─ pipeline/                # (pendiente) orquestación end-to-end
│
├─ scripts/
│  ├─ ingest_documents.py         # Ingesta de PDF/DOCX/TXT/CSV → Document/Chunk
│  ├─ ingest_web.py               # Ingesta Web (requests | selenium | sitemap)
│  ├─ check_sources.py            # Utilidad para consultar/crear fuentes
│  └─ index_chunks.py             # (pendiente) Indexación FAISS/Chroma
│
├─ data/
│  ├─ raw/                        # Ficheros originales (subidos directos)
│  └─ processed/
│     ├─ tracking.sqlite          # BD SQLite: Source, IngestionRun, Document, Chunk
│     └─ runs/
│        ├─ docs/
│        │  └─ run_<id>/
│        │     ├─ stdout.txt
│        │     └─ summary.json
│        └─ web/
│           └─ run_<id>/
│              ├─ stdout.txt
│              ├─ fetch_index.json
│              └─ raw/page_*.html
│
├─ models/
│  ├─ embeddings/                 # Caché de embeddings
│  ├─ faiss/                      # Índices FAISS (por integrar)
│  └─ chroma/                     # Colecciones ChromaDB (por integrar)
│
├─ logs/
│  └─ ingestion.log               # (opcional) log estructurado de ingestas
│
└─ tests/
   ├─ test_ingestion.py
   ├─ test_rag_pipeline.py        # (pendiente)
   ├─ test_retrievers.py          # (pendiente)
   ├─ test_generators.py          # (pendiente)
   └─ verify_ingestion_sqlite.py  # Verificación post-ingesta (BD + summary)
```

### 1.1 Convenciones
- Python 3.10+ · Flask · SQLAlchemy · requests · beautifulsoup4 · selenium.
- **Sin frameworks nuevos** (requisito TFM).
- **Trazabilidad**: `Source → IngestionRun → Document → Chunk` y artefactos por `run_id`.

---

## 2) Configuración y ejecución de la app

1. Crear `.env` a partir de `.env.example` y configurar:
   - `DATABASE_URL=sqlite:///data/processed/tracking.sqlite`
   - `LOG_CONFIG=config/logging.yaml`
   - `SETTINGS_TOML=config/settings.example.toml` (o `config/settings.toml`)

2. Instalar dependencias:
   ```bash
   pip install -r requirements.txt
   # para desarrollo
   pip install -r requirements-dev.txt
   ```

3. Ejecutar en desarrollo (si procede):
   ```bash
   flask --app app run --debug
   ```

---

## 3) Esquema de datos (SQLite)

- **Source**: id, type("docs"|"web"), name, config(json).
- **IngestionRun**: id, source_id, started_at, finished_at, status("running|done|error"), meta(json).
- **Document**: id, source_id, path, title, size, meta(json: {fetched_at, run_id, ...}).
- **Chunk**: id, source_id, document_id, index(ordinal), text, content, meta(json).

> Nota: `Chunk.ordinal` en Python mapea a columna SQL `"index"`.

---

## 4) Ingesta web (CLI)

**Script**: `scripts/ingest_web.py`  
**Estrategias**: `requests`, `selenium`, `sitemap`

### 4.1 Parámetros comunes
- `--seed`: URL inicial (o sitemap.xml).
- `--strategy`: estrategia (`requests|selenium|sitemap`).
- `--source-id`: ID de `Source` (type = "web").
- `--run-id`: identificador del run (entero).
- `--allowed-domains`: dominios permitidos (coma-separado).
- `--depth`: profundidad del crawler (requests/selenium).
- `--max-pages`: límite de páginas en el run.
- `--timeout`: timeout por request (segundos).
- `--user-agent`: UA HTTP.
- `--robots-policy`: `strict|tolerant` (cómo interpretar robots.txt).
- `--force-https`: fuerza HTTPS en normalización de URLs.
- `--include/--exclude`: patrones de URL a incluir/excluir (regex o substrings).

### 4.2 Parámetros Selenium
- `--driver`: `chrome` (u otro).
- `--no-headless`: ejecuta con ventana visible (debug).
- `--render-wait-ms`: espera tras carga inicial.
- `--scroll`: activa scroll incremental.
- `--scroll-steps`, `--scroll-wait-ms`.
- `--wait-selector`: CSS del contenedor real de contenido.
- `--window-size`: `ancho,alto` (por ejemplo `1366,900`).
- `--iframe-max`: (fallback) nº máx. de iframes a seguir si no hay texto (mismo dominio).

### 4.3 Filtros y fallbacks implementados
- **Sitemap**:
  - Filtrado por *Content-Type* no HTML (PDF, imagen, binarios).
  - Filtrado adicional por **extensión** (`.pdf`, `.jpg`, …).
  - Fallback **HTTP** cuando una URL `http://` redirige a `https` y termina en `404`.
- **Procesado**:
  - Conversión **HTML → texto** con normalización.
  - Fallback de **iframes** (mismo dominio) si la página principal no produce texto (`--iframe-max`).

### 4.4 Artefactos de salida
- `data/processed/runs/web/run_<id>/stdout.txt`
- `data/processed/runs/web/run_<id>/fetch_index.json`
- `data/processed/runs/web/run_<id>/raw/page_*.html`
- `data/processed/runs/web/run_<id>/summary.json` con `totals` y `counters`

### 4.5 Ejemplos (Windows PowerShell)
```powershell
# 1) sitemap
python -m scripts.ingest_web `
  --seed "https://www.onda.es/sitemap.xml" `
  --strategy sitemap `
  --source-id 103 `
  --run-id 20250902 `
  --allowed-domains "onda.es,www.onda.es" `
  --max-pages 50 `
  --timeout 15 `
  --user-agent "Mozilla/5.0" `
  --robots-policy tolerant

# 2) requests
python -m scripts.ingest_web `
  --seed "https://www.onda.es/" `
  --strategy requests `
  --source-id 101 `
  --run-id 20250902 `
  --allowed-domains "onda.es,www.onda.es" `
  --depth 2 `
  --max-pages 50 `
  --timeout 15 `
  --user-agent "Mozilla/5.0" `
  --force-https `
  --robots-policy strict

# 3) selenium
python -m scripts.ingest_web `
  --seed "https://www.onda.es/agenda/" `
  --strategy selenium `
  --source-id 102 `
  --run-id 20250902 `
  --allowed-domains "onda.es,www.onda.es" `
  --depth 1 `
  --max-pages 15 `
  --timeout 30 `
  --user-agent "Mozilla/5.0" `
  --force-https `
  --robots-policy strict `
  --driver chrome `
  --scroll --scroll-steps 6 --render-wait-ms 1500 --wait-selector body `
  --iframe-max 3
```

### 4.6 Troubleshooting
- `unrecognized arguments: --iframe-max` → borrar `scripts/__pycache__` y asegurar que se ejecuta **el** `scripts/ingest_web.py` del repo (y no otro paquete llamado `scripts` en tu Python).
- `Sin texto extraíble` (dinámico/iframes) → usar `--wait-selector` más específico, y/o aumentar `--iframe-max`. Añadir dominios embebidos a `--allowed-domains`.
- PDFs/Imágenes como texto basura → ya se filtra por extensión + *Content-Type*. Ingerir PDFs por `scripts/ingest_documents.py` si procede.

---

## 5) Ingesta de documentos (CLI)

**Script**: `scripts/ingest_documents.py`  
Procesa PDF, DOCX, TXT, CSV… generando `Document` y `Chunk`.  
Artefactos en `data/processed/runs/docs/run_<id>/` (`stdout.txt`, `summary.json`).

> Mantener separadas las ingestas *web* (HTML) y *docs* (binarios) mejora la calidad y el diagnóstico.

---

## 6) Verificación post-ingesta

**Script**: `tests/verify_ingestion_sqlite.py`  
Ejemplos:
```powershell
python tests/verify_ingestion_sqlite.py --db data/processed/tracking.sqlite --run-id 20250902
python tests/verify_ingestion_sqlite.py --db data/processed/tracking.sqlite --run-id 20250902 --source-id 103
```

**Comprueba**: totales (docs/chunks/bytes), top por nº de chunks, sanidad (`text NULL/vacío`, existencia columna `"index"`), sample de chunks y comparación con `summary.json`.

---

## 7) UI administrativa

- **Vistas**: `/admin/ingesta_docs`, `/admin/ingesta_web`
- **Acciones**: configurar estrategia/flags, lanzar ingesta, ver métricas y enlaces a artefactos.
- **Próximo ajuste**: exponer `--iframe-max` y validar flags antes de invocar el CLI.

---

## 8) Vector Store (Roadmap inmediato)

- **Entrada**: `Chunk` persistidos (texto + ordinal + meta).
- **Salida**: índices FAISS (`models/faiss/`) o colecciones Chroma (`models/chroma/`).
- **CLI**: `scripts/index_chunks.py --store {faiss|chroma} --model all-MiniLM-L6-v2 --limit N --rebuild`
- **Artefactos**: `index_meta.json` (n_chunks, dim, tiempo, checksum), logs de indexación.
- **Criterios**: consultas de humo (top-k), tamaño en disco, tiempo de construcción.

---

## 9) Estándares y criterios de aceptación

- Logs **estructurados** con contexto (`run_id`, `source_id`, `url`, `status`, `bytes`, `chunks`).
- Artefactos y BD coherentes por `run_id`.
- Sin `chunks` nulos/vacíos.
- Filtros de binarios aplicados en web.
- `IngestionRun.status` y `run.meta` actualizados (`run_dir`, `summary_totals`, `summary_counters`).

---

## 10) Mantenimiento y limpieza

- Rotación de `data/processed/runs/*` antiguos.
- Backups de `tracking.sqlite` previos a limpiezas masivas.
- Deduplicación lógica (futura): índice único por (`source_id`, `path`, `run_id`) si se añade el campo a `Document`.
