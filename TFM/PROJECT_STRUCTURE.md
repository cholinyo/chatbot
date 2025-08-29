📄 Proyecto TFM — Estructura de Ficheros y Directorios (v2025-08-29)

Este documento describe para qué sirve cada carpeta y archivo generado por el script de inicialización y las ampliaciones posteriores del proyecto (Flask + RAG + comparador de LLMs).

Nota: Los archivos creados inicialmente estaban vacíos (o con texto mínimo). La lógica se ha ido implementando fase a fase. Actualmente la parte de ingesta de documentos ya está operativa, con persistencia en BD y visualización en UI.

Raíz del proyecto

README.md — Resumen del proyecto y cómo empezar.

.gitignore — Evita versionar artefactos (venv, datos, modelos, configuraciones sensibles).

.env.example — Plantilla de variables sensibles (claves, tokens). Copiar a .env (no versionar).

requirements.txt / requirements-dev.txt — Dependencias de runtime / desarrollo.

wsgi.py — Punto de entrada para despliegues WSGI (gunicorn/uwsgi).

config/ — Configuración externa (no incluye secretos).

settings.example.toml — Plantilla de configuración (copiar a settings.toml).

logging.yaml — Configuración de logging estructurado.

docs/ — Documentación del proyecto.

arquitectura.md, decisiones.md, evaluacion.md, despliegue.md.

data/ — Datos del sistema (no versionar en su mayoría).

raw/ — Fuentes originales intactas (PDFs, DOCX, HTML, JSON).

processed/ — Texto normalizado, chunks, datasets y logs de ingesta.

runs/ — Directorios por ejecución de ingestas (docs/run_<id> con stdout.txt, summary.json).

tracking.sqlite — Base de datos SQLite de seguimiento (Source, IngestionRun, Document, Chunk).

logs/ingestion.log — Log global de ingestas.

web/ — Snapshots HTML/JSON de crawling (para ingesta web).

models/ — Artefactos del vector store y cachés.

embeddings/ — Caché de embeddings.

faiss/ — Índices FAISS.

chroma/ — Colecciones ChromaDB.

scripts/ — Scripts de ingesta y utilidades.

ingest_documents.py — Pipeline de ingesta de documentos (con chunking, fingerprints, logging).

ingest_web.py — (en desarrollo) Pipeline de ingesta de webs (sitemap, crawling, Selenium).

TFM/ — Memoria y anexos académicos.

tests/ — Tests pytest.

app/ — Aplicación Flask

app/init.py — create_app(): carga config, registra extensiones, logging y DB.

app/extensions/ — Inicialización de librerías externas.

db.py — SQLAlchemy + sesiones.

logging.py — Logging estructurado.

app/blueprints/ — Rutas organizadas por dominio.

admin/

routes_data_sources.py — Lista de fuentes (docs/web).

routes_ingesta_docs.py — Gestión de ingesta de documentos:

CRUD de fuentes (crear/editar/eliminar).

Lanzar ingestas (recursivo, patrones, “solo nuevos”).

Mostrar métricas por fuente (documentos, chunks, último run).

Endpoints /stdout y /summary.json por run.

routes_ingesta_web.py — Configuración y ejecución de ingestas web (en progreso).

chat/ — Chat RAG y comparador (pendiente de completar).

dashboard/ — KPIs de ingesta/retrieval/evaluación (pendiente).

app/models/ — Modelos SQLAlchemy.

Source — Origen de datos (tipo docs | web, URL/carpeta, config).

IngestionRun — Ejecución de ingesta (status, meta, stdout, cmd, duración).

Document — Documento ingerido (path, hash, size, mtime, metadata).

Chunk — Trozos de texto de documentos (índice, contenido, metadata).

app/templates/admin/ — Vistas Jinja2.

ingesta_docs.html — UI con tabla de fuentes, métricas, ejecuciones recientes, botones CRUD.

app/static/css/custom.css — Estilos unificados.

tests/

test_ingestion.py — Prueba de ingesta de documentos (solo nuevos vs. recursivo).

test_rag_pipeline.py — Flujo E2E RAG (pendiente de completar).

test_retrievers.py, test_generators.py, etc.

Flujos actuales y verificados

Ingesta de documentos (OK)

Desde la UI → seleccionar carpeta fuente.

Ejecuta scripts/ingest_documents.py con cwd en raíz del repo.

Guarda stdout.txt, summary.json en data/processed/runs/docs/run_<id>.

Actualiza BD (Source, IngestionRun, Document, Chunk).

Vista UI muestra métricas (docs, chunks, último estado).

“Ver salida” y summary.json funcionales.

Ingesta web (en preparación)

Blueprint y script existen (routes_ingesta_web.py, ingest_web.py) pero aún sin las mejoras aplicadas a docs.

Pendiente de validar strategy: sitemap, allowed_domains, max_pages.

Pendiente de exponer métricas (páginas escaneadas, chunks generados).

Vector store (pendiente)

Próxima fase: conectar resultados de ingesta a FAISS/Chroma.

⚡ Super-prompt — Siguiente Paso: Ingesta Web

Rol: Actúa como tech lead y IC senior para un proyecto Flask+SQLAlchemy que implementa pipelines de ingesta (documentos y web) para un Chatbot RAG.

Objetivo: Robustecer la ingesta web desde la UI con la misma calidad que la de documentos: runs trazables, métricas, summary, stdout, errores claros.

Contexto:

App factory en app/__init__.py con logging y DB (SQLite en data/processed/tracking.sqlite).

Modelos: Source(type: 'docs'|'web', url, name, config), IngestionRun(status, meta), Document, Chunk.

Blueprints admin:

routes_data_sources.py — listado simple de fuentes y enlaces.

routes_ingesta_docs.py — completado: CRUD, ejecución, métricas, stdout, summary.

routes_ingesta_web.py — pendiente de mejora: configurar fuente web y lanzar run.

Scripts:

scripts/ingest_documents.py — ya robusto.

scripts/ingest_web.py — necesita mejoras.

SECRET_KEY: FLASK_SECRET_KEY o generado en data/secret_key.txt.

Definition of Done (para web):

Desde la UI se crea una fuente web con url, strategy=sitemap, filtros (allowed_domains, max_pages).

Al ejecutar se lanza scripts/ingest_web.py con parámetros correctos.

Se guardan stdout.txt y summary.json en data/processed/runs/web/run_<id>.

La UI muestra métricas por fuente (páginas, chunks).

“Ver salida” y summary.json funcionan igual que en docs.

Errores claros cuando faltan dependencias (ej. Selenium) o el script.

Restricciones:

Sin frameworks adicionales; solo Flask + SQLAlchemy 2.x.

Código claro, probado, trazable.

Cómo trabajar:

Pedir solo los ficheros que necesites ver/modificar (por ruta).

Proponer cambios con el archivo completo, listo para pegar.

Incluir comandos de prueba (PowerShell) y cómo verificar en la UI.

Áreas prioritarias:

app/blueprints/admin/routes_ingesta_web.py (refactor a nivel docs: CRUD, run, métricas, stdout, summary).

scripts/ingest_web.py (captura stdout, returncode, cmd, run_dir).

app/templates/admin/ingesta_web.html (tabla de fuentes, métricas, ejecuciones).

app/models/* si faltan campos (Page?).

Plan de test mínimo:

Crear fuente web con url=https://ejemplo.com/sitemap.xml, max_pages=10.

Lanzar ingesta → validar runs, stdout.txt, summary.json, métricas en UI.

Forzar error (renombrar script) → mensaje claro en UI y meta.exception.

Entregables:

Ficheros modificados completos.

Lista rápida de pruebas manuales.

Notas de despliegue (ej. instalar Selenium/requests-html si son necesarias).