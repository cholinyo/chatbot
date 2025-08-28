# TFM RAG — Estado del proyecto y guía de estructura (v2025-08-28)

> **Proyecto**: Prototipo de Chatbot RAG para Administraciones Locales (Flask + Python)
> **Autor**: Vicente Caruncho Ramos
> **Contexto**: TFM UJI 2024–2025
> **Objetivo de este documento**: Dejar por escrito **qué hay implementado**, **cómo está estructurado** el repositorio, **modelos de datos**, **pipeline de ingesta de documentos**, **logging**, **artefactos generados** y **cómo ejecutar/monitorizar**. Sirve como handoff para continuar el desarrollo (siguientes fuentes: Web, BBDD, APIs; Vector Stores; Chat/Comparador; Benchmarking).

---

## 1) Resumen ejecutivo

* **App factory** (`app/__init__.py`) lista y validada. Crea tablas en `data/processed/tracking.sqlite`, registra (si existe) el blueprint de ingesta y expone `/status/ping`.
* **Sistema de logging** centralizado (`app/extensions/logging.py`), con **rotación** y doble salida: consola y ficheros en `data/logs/{app.log, ingestion.log}`. Nivel controlable por `LOG_LEVEL`.
* **Ingesta de Documentos** implementada (v1):

  * **Loaders** para `pdf`, `docx`, `txt`, `csv`.
  * **Cleaning** y **Splitters** con solape configurables (bug de avance resuelto para textos cortos).
  * **Deduplicación**/reprocesado por **política**: `hash` (por defecto) o `mtime` (placeholder).
  * **Persistencia** en SQLite vía ORM: `Source`, `Document`, `Chunk`, `IngestionRun`.
  * **Artefactos** JSONL: `data/processed/documents/{docs.jsonl, chunks.jsonl}` + resúmenes por run en `data/processed/documents/runs/`.
  * **CLI**: `scripts/ingest_documents.py` (argumentos para carpeta, extensiones, split, CSV, etc.).
* **Pruebas realizadas**: ingesta mínima sobre `data/raw/documents_test/hola.txt` → **1 documento / 2 chunks**. Logs y artefactos generados correctamente.

---

## 2) Estructura de directorios (actual)

```
<repo-root>/
├─ app/
│  ├─ __init__.py                # Application factory (carga config, logging, DB, tablas, blueprints)
│  ├─ extensions/
│  │  ├─ logging.py              # Config de logging (rotación, niveles, formato, ficheros)
│  │  └─ db.py                   # Engine, sessionmaker, create_all, get_session
│  ├─ models/
│  │  ├─ source.py               # Modelo Source (config de la fuente de ingesta)
│  │  ├─ ingestion_run.py        # Modelo IngestionRun (trazabilidad de ejecuciones)
│  │  ├─ document.py             # Modelo Document (metadatos del documento)
│  │  └─ chunk.py                # Modelo Chunk (segmentos troceados para RAG)
│  ├─ blueprints/
│  │  └─ ingestion/
│  │     ├─ routes.py            # (opcional/placeholder) endpoints REST de ingesta
│  │     └─ services.py          # Servicio de ingesta de Documentos (núcleo: _ingest_documents)
│  └─ rag/
│     ├─ processing/
│     │  ├─ cleaners.py          # Limpieza/normalización de texto; hashing de contenido
│     │  └─ splitters.py         # Split en chunks con solape; fix de progreso estricto
│     └─ loaders/
│        ├─ pdf_loader.py        # Extracción básica con PyPDF2 (pypdf)
│        ├─ docx_loader.py       # Extracción con python-docx
│        ├─ txt_loader.py        # Lectura con encoding configurable
│        └─ csv_loader.py        # Lectura CSV → texto unificado (configurable)
│
├─ scripts/
│  ├─ ingest_documents.py        # CLI de ingesta de documentos (Python)
│  └─ run_server.py              # Arranque del servidor Flask (modo dev)
│
├─ data/
│  ├─ raw/                       # Fuentes crudas (p.ej. documents_test/)
│  └─ processed/
│     ├─ tracking.sqlite         # SQLite de control/seguimiento de ingestas
│     ├─ documents/
│     │  ├─ docs.jsonl           # Artefacto: 1 línea por documento (metadatos clave)
│     │  ├─ chunks.jsonl         # Artefacto: 1 línea por chunk (id, doc_id, len, ...)
│     │  └─ runs/                # Resúmenes por ejecución (JSON)
│     └─ … (futuros: vectores, índices, métricas)
│
├─ config/
│  └─ settings.toml              # (opcional) ajustes no secretos
│
├─ docs/                         # Documentación del proyecto (este archivo puede residir aquí)
└─ TFM/                          # Entregables/memoria académica (a completar)
```

> Nota: en Windows, los paths usados por el código gestionan `\\`/`/` y se resuelven con `pathlib.Path`.

---

## 3) Modelos de datos (ORM, SQLite `data/processed/tracking.sqlite`)

### 3.1 `Source`

Representa una **fuente de ingesta** (documentos, web, BBDD, APIs). Para Documentos:

* `id: str` — PK lógica (`docs_general`, `docs_debug`, …)
* `type: str` — p.ej. `"document"` (futuro: `"web"`, `"db"`, `"api"`)
* `name: str` — nombre visible
* `enabled: bool` — activar/desactivar
* `config: JSON` — configuración específica de la fuente (ver §4.1)
* `created_at: datetime`, `updated_at: datetime`

> Índices recomendados: `(type)`, `(enabled)`. Campo `config` almacena parámetros de ingesta (carpeta, filtros, chunking, política, etc.).

### 3.2 `Document`

Un documento **normalizado** tras la extracción de texto.

* `doc_id: str` — PK estable = `sha256(f"{source.id}:{relative_path}")`
* `source_id: str` — FK → `Source.id`
* `source_type: str`
* `uri: str` — ruta absoluta al fichero
* `title: Optional[str]`
* `lang: Optional[str]`
* `mime: str` — `text/plain`, `application/pdf`, …
* `version: Optional[str]`
* `collected_at: datetime`
* `size_bytes: Optional[int]`
* `origin_hash: str` — hash del archivo crudo (para política `hash`)
* `normalized_hash: str` — hash del texto limpio (para cambios semánticos)
* `created_at`, `updated_at`

> Índices recomendados: `(source_id)`, `(normalized_hash)`, `(origin_hash)`.

### 3.3 `Chunk`

Segmento **troceado** de un `Document` para RAG.

* `chunk_id: str` — PK = `f"{doc_id}:{position:06d}"`
* `doc_id: str` — FK → `Document.doc_id`
* `position: int` — orden del chunk
* `content: str` — texto del chunk
* `tokens: int` — len aproximada (por ahora, caracteres)
* `lang: Optional[str]`
* `title: Optional[str]`
* `retrieval_tags: JSON` — ej. `{ "source_id": ..., "source_type": ... }`
* `provenance: JSON` — `run_id`, loader, cleaner, opciones de split, …
* `created_at`, `updated_at`

> Índices recomendados: `(doc_id, position)` y `(retrieval_tags)` (parcial/texto si aplica en el motor futuro).

### 3.4 `IngestionRun`

Trazabilidad de **cada ejecución** de ingesta.

* `run_id: str` — PK (UUID)
* `source_id: str`, `source_type: str`
* `source_scope: str` — p.ej. carpeta o URL base
* `params: JSON` — política, filtros efectivos, etc.
* `status: str` — `running | success | partial | failed`
* `stats: JSON` — `scanned, new_docs, updated_docs, skipped_unchanged, failed, total_chunks, errors[]`
* `started_at`, `ended_at`

> El servicio hace `session.flush()` periódicamente para que los contadores se puedan consultar durante la ejecución (si expones un endpoint `/latest`).

---

## 4) Pipeline de Ingesta de Documentos (v1)

### 4.1 Configuración por `Source.config` (ejemplo efectivo)

```json
{
  "input_dir": "data/raw/documents",
  "recursive": true,
  "include_ext": ["pdf", "docx", "txt", "csv"],
  "exclude_patterns": [],
  "encoding_default": "utf-8",
  "csv": { "delimiter": ",", "quotechar": "\"", "header": true, "columns": null },
  "indexing": { "policy": "hash" },
  "rag": { "chunk_size": 512, "chunk_overlap": 64 }
}
```

### 4.2 Flujo

1. **Enumeración** (`_enumerate_files`) con filtros de extensión, recursividad y exclusiones por patrón.
2. **Política de reprocesado** (`_should_process`):

   * `hash` (por defecto): compara `origin_hash` del fichero con el guardado en `Document`.
   * `mtime`: reservado para futura lógica (timestamp de modificación).
3. **Carga** (loader por extensión): `load_pdf`, `load_docx`, `load_txt`, `load_csv` → `raw_text` y metadatos.
4. **Limpieza** (`clean_text`) y **hash del normalizado** (`text_sha256`).
5. **Persistencia de `Document`** (crea/actualiza metadatos) y **reemplazo de `Chunk`s** del documento.
6. **Split** (`split_text`) con `SplitOptions(chunk_size, chunk_overlap)` → lista ordenada de trozos.
   *Nota*: se aplicó un **fix** para garantizar **progreso estricto** y evitar loops en textos cortos.
7. **Artefactos JSONL** (opcional pero activado por defecto):

   * `docs.jsonl` con campos clave (`doc_id, uri, title, mime, origin_hash, normalized_hash`).
   * `chunks.jsonl` con (`chunk_id, doc_id, position, len`).
8. **Fin de run**: `IngestionRun.status` → `success/partial/failed` y `stats` finales.

---

## 5) Logging

* **Módulo**: `app/extensions/logging.py` → inicializado en `create_app()` antes de registrar blueprints.
* **Handlers**: consola + `data/logs/app.log` (root) + `data/logs/ingestion.log` (logger `ingestion`).
* **Niveles**: control por `LOG_LEVEL` (`DEBUG/INFO/WARNING/ERROR/CRITICAL`).
* **Formato**: timestamp ISO + nivel + logger + mensaje (configurable por `LOG_FORMAT`).
* **En `services.py`**: logger `ingestion` emite eventos `run.start`, `skip.unchanged`, `process`, `split`, `process.error`, `run.end`.
  Se hace `session.flush()` tras cada documento para que el estado sea observable durante la ejecución.

Ejemplos de uso:

```powershell
# Nivel INFO y tail en vivo
$env:LOG_LEVEL="INFO"
python scripts/ingest_documents.py --input-dir "data/raw/documents_test" --include-ext txt --no-recursive
Get-Content .\data\logs\ingestion.log -Wait
```

---

## 6) Artefactos generados

* `data/processed/tracking.sqlite` — base de control con tablas ORM.
* `data/processed/documents/docs.jsonl` — 1 / documento.
* `data/processed/documents/chunks.jsonl` — 1 / chunk.
* `data/processed/documents/runs/run_<SOURCE>_<YYYYMMDD>_<HHMMSS>.json` — resumen del run (status, stats).

Ejemplo real observado:

```json
{"doc_id": "...c5c9", "uri": ".../documents_test/hola.txt", "mime": "text/plain", "origin_hash": "fe22...", "normalized_hash": "97b9..."}
{"chunk_id": "...:000000", "doc_id": "...", "position": 0, "len": 6}
{"chunk_id": "...:000001", "doc_id": "...", "position": 1, "len": 3}
```

---

## 7) Ejecución y validación

### 7.1 CLI (recomendado)

```powershell
# Ingesta mínima (TXT, sin recursividad)
python scripts/ingest_documents.py --input-dir "data/raw/documents_test" --include-ext txt --no-recursive --verbose-json

# Carpeta real (pasos): primero acotar, luego ampliar
python scripts/ingest_documents.py --input-dir "D:/carpeta" --include-ext pdf docx txt --no-recursive --verbose-json
python scripts/ingest_documents.py --input-dir "D:/carpeta" --include-ext pdf docx txt --recursive --verbose-json
```

### 7.2 App factory

```powershell
python -c "from app import create_app; create_app(); print('Factory OK')"
Get-Content .\data\logs\app.log -Wait
```

### 7.3 Inspección rápida de DB

```powershell
python -c "import sys, pathlib; sys.path.insert(0, str(pathlib.Path('.').resolve())); from app import create_app; from app.extensions.db import get_session; from app.models.document import Document; from app.models.chunk import Chunk; create_app();
from contextlib import closing; from app.extensions.db import get_session as gs;
with gs() as s: print('docs=', s.query(Document).count(), 'chunks=', s.query(Chunk).count())"
```

---

## 8) Dependencias mínimas (fase Documentos)

```
flask, sqlalchemy, python-dotenv (opcional)
pypdf (o PyPDF2), python-docx
```

> Observación: si ves un *DeprecationWarning* de **PyPDF2**, puedes migrar a `pypdf` (import drop-in) cuando abordemos PDFs más complejos.

---

## 9) Buenas prácticas y decisiones tomadas

* **IDs estables** de `Document` basados en `source.id + ruta relativa` → permiten reingestas idempotentes.
* **Dos hashes**: `origin_hash` (archivo crudo) y `normalized_hash` (texto limpio) → control de cambios reales.
* **Artefactos JSONL** para trazabilidad simple y verificaciones manuales.
* **Splitters** con **garantía de avance** (no loops) y solape configurable.
* **Logging** desde el inicio de `create_app()` para capturar todo.
* **SQLite** en `data/processed` para facilitar inspección/backup; listo para migrar a Postgres si fuese necesario.

---

## 10) Próximos pasos propuestos

1. **Fuentes adicionales**

   * **Web**: configuración `url`, `profundidad`, estrategias de scraping (requests+BS4 / Selenium / sitemap), `robots.txt`, normalización de HTML.
   * **BBDD**: conexión (DSN), lista de tablas/campos, *page size*, extracción incremental por `updated_at`.
   * **APIs**: auth (token/Bearer), paginación, esquemas, control de rate-limits.
   * Programación de reingestas (Windows Task Scheduler) y *status* de últimos runs por tipo de fuente.
2. **Vector Stores**: FAISS + ChromaDB (dual) + embeddings (`sentence-transformers: all-MiniLM-L6-v2`).
3. **Chat** + **Comparador de Modelos** (Ollama/OpenAI) con trazabilidad de contexto (chunks usados, score, coste/latencia).
4. **Benchmarking**: framework de evaluación, datasets, métricas y dashboard inicial.
5. **Panel de control**: UI para fuentes (alta/edición/baja), lanzar reingestas, ver *runs*, descargar artefactos.
6. **Seguridad/Gobernanza**: logs de auditoría, PII/GDPR (redacciones), versiones de documentos.

---

## 11) Glosario rápido

* **Source**: Configuración de una fuente de datos para ingesta.
* **Run**: Ejecución de ingesta asociada a una `Source` (con `status` y `stats`).
* **Document**: Representación normalizada del contenido textual extraído de un archivo.
* **Chunk**: Segmento del documento para recuperación semántica.
* **Artefacto JSONL**: Fichero de líneas JSON para depurar/verificar ingestas.

---

## 12) Checklist previo al siguiente sprint

* [x] `app/__init__.py` con logging y DB init (validado)
* [x] Modelos ORM creados y tablas generadas (validado)
* [x] Ingesta de Documentos (txt/pdf/docx/csv) con split y artefactos (validado)
* [x] Logging por fichero de ingesta
* [ ] Endpoints REST de ingesta (listar sources, lanzar run, ver último run)
* [ ] Configuración UI para fuentes y parámetros
* [ ] Vector Stores + embeddings
* [ ] Chat/Comparador de modelos
* [ ] Benchmark + dashboard de métricas

---

**Listo para continuar** con: *fuentes Web/BBDD/APIs*, *vector stores* y *chat/comparador*. Este documento sirve como base para el siguiente prompt: puedo derivarte una guía paso-a-paso para añadir la **Fuente Web** con scraping (requests/BS4 + Selenium), su modelo de configuración y su programador de reingestas en Windows (Task Scheduler).
