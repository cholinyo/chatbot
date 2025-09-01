Este documento describe para qué sirve cada carpeta y archivo generado por el script de inicialización y las ampliaciones posteriores del proyecto (Flask + RAG + comparador de LLMs).

Nota: La lógica se ha ido implementando fase a fase.
Actualmente:

Ingesta de documentos: estable y validada.

Ingesta web: en pruebas (ya lanza runs y guarda stdout/artefactos).

Vector store: pendiente de integrar.

📂 Raíz del proyecto

README.md — Resumen del proyecto y cómo empezar.

.gitignore — Evita versionar artefactos (venv, datos, modelos, configuraciones sensibles).

.env.example — Plantilla de variables sensibles (claves, tokens). Copiar a .env (no versionar).

requirements.txt / requirements-dev.txt — Dependencias de runtime / desarrollo.

wsgi.py — Punto de entrada para despliegues WSGI (gunicorn/uwsgi).

⚙️ Configuración

config/ — Configuración externa (no incluye secretos).

settings.example.toml — Plantilla de configuración (copiar a settings.toml).

logging.yaml — Configuración de logging estructurado.

📑 Documentación

docs/ — Documentación del proyecto:

arquitectura.md, decisiones.md, evaluacion.md, despliegue.md.

📊 Datos

data/

raw/ — Fuentes originales intactas (PDFs, DOCX, HTML, JSON).

processed/ — Texto normalizado, chunks, datasets y logs de ingesta.

runs/ — Directorios por ejecución de ingestas.

docs/run_<id>/stdout.txt, summary.json.

web/run_<id>/stdout.txt, fetch_index.json, raw/*.html.

tracking.sqlite — Base de datos SQLite de seguimiento (Source, IngestionRun, Document, Chunk).

logs/

ingestion.log — Log global de ingestas (documentos y webs).

web/ — Snapshots HTML/JSON de crawling web.

📦 Modelos y vector store

models/ — Artefactos del vector store y cachés.

embeddings/ — Caché de embeddings.

faiss/ — Índices FAISS.

chroma/ — Colecciones ChromaDB.

🛠️ Scripts

scripts/

ingest_documents.py — Pipeline de ingesta de documentos (chunking, fingerprints, logging).

ingest_web.py — Pipeline de ingesta web (sitemap, BFS requests, Selenium).

check_sources.py — Utilidad para listar fuentes y runs en BD.

📂 App Flask

app/init.py — create_app(): carga config, logging y DB.

app/extensions/

db.py — SQLAlchemy + sesiones.

logging.py — Logging estructurado.

app/blueprints/

admin/

routes_data_sources.py — Lista de fuentes (docs/web).

routes_ingesta_docs.py — CRUD y ejecución de ingestas de documentos.

routes_ingesta_web.py — Configuración/ejecución de ingestas web (en progreso).

chat/ — Chat RAG y comparador (pendiente).

dashboard/ — KPIs de ingesta/retrieval/evaluación (pendiente).

app/models/

Source — Origen de datos (type=docs|web, url, config).

IngestionRun — Ejecución de ingesta (status, meta, stdout, cmd, duración).

Document — Documento ingerido (path, hash, size, mtime, metadata).

Chunk — Fragmentos de texto.

app/templates/admin/

ingesta_docs.html — UI de ingesta de documentos.

ingesta_web.html — UI de ingesta web (CRUD fuentes, runs, métricas).

app/static/css/custom.css — Estilos unificados.

🧪 Tests

tests/

test_ingestion.py — Verifica ingesta de documentos.

test_rag_pipeline.py — Flujo E2E RAG (pendiente).

test_retrievers.py, test_generators.py, etc.

✅ Flujos actuales

Ingesta de documentos

OK: crea fuentes, ejecuta ingestas, genera chunks y métricas.

Ingesta web

En progreso: ya se ejecutan runs (sitemap, requests, selenium).

Artefactos (stdout.txt, fetch_index.json) se guardan en runs/web/run_<id>.

Aún falta integrar chunking de HTML → Chunk en BD.

Vector store

Pendiente: conectar Chunk a FAISS/Chroma.

⚡ Super-prompt — Siguiente Paso

Rol: Tech lead e IC senior en un proyecto Flask+SQLAlchemy (TFM RAG).
Objetivo: Terminar de robustecer la ingesta web y comenzar la integración con el vector store.
Estado:

Ingesta de documentos: estable.

Ingesta web: runs ya ejecutan, pero falta persistir páginas y generar Chunks como en docs.

Vector store: aún sin poblar.

🎯 Prompt de continuación
    Rol: Actúa como tech lead para el proyecto Flask+SQLAlchemy de ingesta RAG.

    Objetivo inmediato:
    1. Revisar `scripts/ingest_web.py` y `routes_ingesta_web.py` para que:
    - Cada página descargada genere un `Document` y sus `Chunk`s en BD (igual que ingest_documents.py).
    - Se guarden en `data/processed/runs/web/run_<id>/summary.json` métricas claras (páginas, chunks, bytes).
    - La UI muestre las métricas en la tabla de ejecuciones.

    2. Validar que los `stdout.txt` y `summary.json` se actualizan bien en la UI (Ver salida / Artefactos).

    3. Preparar el pipeline para que los `Chunk`s de web sean después indexables en FAISS/Chroma (vector store).

    Restricciones:
    - Sin frameworks nuevos, solo Flask + SQLAlchemy + libs ya usadas (requests, selenium, bs4).
    - Código claro y trazable.

    Plan:
    - Pedir solo los ficheros a modificar (seguro: `scripts/ingest_web.py`, quizá `routes_ingesta_web.py`, y modelos si hace falta un `Page`).
    - Entregar archivos completos listos para pegar.
    - Incluir comandos de prueba (PowerShell) y cómo validar en la UI.

    Contexto actual:
    - BD tracking.sqlite con Source, IngestionRun, Document, Chunk.
    - Ingesta de docs ya validada (crea Source, Document, Chunk).
    - Ingesta web crea Source y Run, pero no aún Document/Chunk.