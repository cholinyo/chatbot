Estructura del Proyecto
Raíz del proyecto

README.md — Resumen del proyecto y guía de inicio.

.gitignore — Exclusión de archivos no versionables (venv, datos, modelos).

.env.example — Plantilla de variables sensibles (claves, tokens).

requirements.txt / requirements-dev.txt — Dependencias principales y de desarrollo.

wsgi.py — Punto de entrada WSGI para producción.

⚙️ Configuración

config/ — Configuración general (sin secretos).

settings.example.toml — Plantilla para settings.toml.

logging.yaml — Configuración de logging estructurado.

📑 Documentación

docs/ — Documentación técnica del proyecto.

arquitectura.md, decisiones.md, evaluacion.md, despliegue.md.

📊 Datos

data/

raw/ — Archivos originales (PDF, HTML, DOCX…).

processed/ — Texto procesado, chunks y logs.

docs/run_<id>/stdout.txt, summary.json.

web/run_<id>/stdout.txt, fetch_index.json, raw/*.html.

logs/

ingestion.log — Log estructurado de ingestas.

tracking.sqlite — SQLite con las tablas:

Source, IngestionRun, Document, Chunk.

📦 Vector Store

models/

embeddings/ — Caché de embeddings.

faiss/ — Índices FAISS (pendiente de integración).

chroma/ — Colecciones ChromaDB (pendiente).

🛠️ Scripts

scripts/

ingest_documents.py — Ingesta de documentos (chunking + persistencia).

ingest_web.py — Ingesta web con 3 estrategias:

requests — BFS simple hasta profundidad --depth.

selenium — Renderizado dinámico (scroll, wait).

sitemap — Extrae URLs de robots.txt y sitemap.xml.

check_sources.py — Consulta de fuentes registradas.

📂 App Flask

app/init.py — create_app(): Carga config y extensiones.

📦 Extensiones

app/extensions/db.py — SQLAlchemy y sesiones.

app/extensions/logging.py — Logging estructurado.

🧠 Modelos

app/models/

Source — Fuente (docs/web), con config.

IngestionRun — Ejecución de ingesta.

Document — Documento ingerido.

Chunk — Fragmentos de texto.

🧩 Blueprints

app/blueprints/admin/

routes_data_sources.py — Gestión de fuentes tipo documentos.

routes_ingesta_docs.py — Ingesta de documentos.

routes_ingesta_web.py — Ingesta web (sitemap, requests, selenium).

chat/ — Chat RAG y comparador (pendiente).

dashboard/ — Métricas de ingesta/retrieval (pendiente).

🎨 UI

app/templates/admin/

ingesta_docs.html — UI para ingesta de documentos.

ingesta_web.html — UI para ingesta web (configuración + ejecución).

app/static/css/custom.css — Estilos comunes.

🧪 Tests

tests/

test_ingestion.py — Ingesta de documentos.

test_rag_pipeline.py — Flujo completo RAG (pendiente).

test_retrievers.py, test_generators.py, etc.

✅ Flujos actuales
✅ Ingesta de documentos

Estado: estable y validado.

Crea Source, IngestionRun, Document, Chunk.

🧪 Ingesta web

Estado: funcional en pruebas.

Implementa las 3 estrategias (requests, selenium, sitemap).

Genera:

Documentos (Document)

Chunks (Chunk)

Artefactos (stdout.txt, fetch_index.json, summary.json)

UI: muestra métricas en tabla (pages, chunks, bytes).

Configurable por UI (scroll, no-headless, selector, etc).

📌 Vector Store

Estado: pendiente de integración.

Chunks ya persistidos → listos para ser indexados (FAISS/Chroma).

⚡ Super-prompt — Reanudación del proyecto

Usa este prompt si deseas continuar el trabajo desde otro hilo/chat.

🎯 Prompt completo:
Rol: Actúa como tech lead para un proyecto Flask+SQLAlchemy con pipeline RAG (TFM académico).

Estado actual:
- Ingesta de documentos: ✅ funcionando (Source → Document → Chunk).
- Ingesta web: 🧪 funcional vía UI (estrategias sitemap, requests, selenium). Guarda `Document`, `Chunk`, `stdout.txt`, `summary.json`.
- Vector store (FAISS / ChromaDB): ❌ aún no integrado.

Objetivo inmediato:
✅ Validar que las tres estrategias web están funcionando con:
    - Guardado de páginas (`Document`)
    - Chunking y persistencia en BD
    - Artefactos de salida (`fetch_index.json`, `summary.json`)
    - UI actualizada con métricas

➡️ Siguiente paso tras validación:
Preparar el pipeline de indexación: `index_chunks.py`
- Selección de chunks no indexados
- Persistencia en `models/faiss/` y `models/chroma/`

Restricciones:
- Sin frameworks nuevos (solo Flask, SQLAlchemy, requests, bs4, selenium).
- Código claro, trazable, con logs y artefactos.