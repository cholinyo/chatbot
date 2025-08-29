TFM RAG — Estado del proyecto y guía de estructura (v2025-08-29)

Proyecto: Prototipo de Chatbot RAG para Administraciones Locales (Flask + Python)
Autor: Vicente Caruncho Ramos
Contexto: TFM UJI 2024–2025
Objetivo: Detallar qué hay implementado, cómo está organizado el repo, modelos de datos, pipelines de ingesta (docs + web), logging, artefactos y cómo ejecutar/validar. Punto de partida para las siguientes iteraciones (BBDD, APIs, vector stores, chat/benchmark).

1) Resumen ejecutivo

App factory (app/__init__.py) operativa. Carga .env, inicializa logging y DB, crea tablas en data/processed/tracking.sqlite, registra blueprints y expone /status/ping.

Se añadió gestión de SECRET_KEY:

Usa FLASK_SECRET_KEY/SECRET_KEY si existen.

Si no, genera una de desarrollo y la persiste en data/secret_key.txt (evita errores de flash()/sesión).

Logging centralizado (app/extensions/logging.py): consola + ficheros rotados en data/logs/.

ORM/DB (app/extensions/db.py + app/models/*): Source, Document, Chunk, IngestionRun. Relaciones y back_populates cuadradas.

Ingesta de Documentos:

Loaders: pdf, docx, txt, csv. Limpieza y split con solape configurables.

Política de re-ingesta por hash (por defecto) o mtime.

Artefactos JSONL y registros de ejecución en DB.

CLI: scripts/ingest_documents.py.

Ingesta Web:

Estrategias: sitemap y requests (BFS), con dominios permitidos, include/exclude, robots policy, force_https, rate/timeout.

CLI: scripts/ingest_web.py.

UI de administración (Flask + Jinja):

Fuentes de datos: listado simple (ID, tipo, nombre, URL/carpeta) y enlaces a “Ingesta Web / Documentos”.

Ingesta Documentos: subir ficheros, lanzar por carpeta (fuente docs, patrones, recursivo, “solo nuevos/modificados”, args extra).

Ingesta Web: configurar fuente y lanzar con captura de stdout, returncode, comando y (si existe) run_dir. Descarga de artefactos con saneado de rutas.

✅ Ahora mismo en curso: comprobación end-to-end de las ingestas (Documentos + Web) desde la aplicación web, asegurando que:

Se persisten correctamente las fuentes y runs.

Se captura salida/errores del proceso.

Los artefactos se generan y se pueden descargar.

No hay errores de sesión/SECRET_KEY ni de rutas/cwd.

2) Estructura del repositorio (actual)
<repo-root>/
├─ app/
│  ├─ __init__.py
│  ├─ extensions/
│  │  ├─ logging.py
│  │  └─ db.py
│  ├─ models/
│  │  ├─ source.py
│  │  ├─ ingestion_run.py
│  │  ├─ document.py
│  │  └─ chunk.py
│  ├─ blueprints/
│  │  ├─ admin/
│  │  │  ├─ routes_main.py
│  │  │  ├─ routes_data_sources.py
│  │  │  ├─ routes_ingesta_docs.py
│  │  │  └─ routes_ingesta_web.py
│  │  └─ ingestion/
│  │     └─ services.py
│  └─ templates/
│     └─ admin/
│        ├─ data_sources.html
│        ├─ ingesta_docs.html
│        └─ ingesta_web.html
├─ scripts/
│  ├─ ingest_documents.py
│  ├─ ingest_web.py
│  └─ run_server.py
├─ data/
│  ├─ raw/
│  │  └─ uploads/
│  └─ processed/
│     ├─ tracking.sqlite
│     ├─ runs/                  # artefactos de ingesta web
│     └─ documents/
│        ├─ docs.jsonl
│        ├─ chunks.jsonl
│        └─ runs/               # resúmenes por run de documentos
├─ config/
│  └─ settings.toml
└─ data/secret_key.txt          # generado si no hay SECRET_KEY en entorno

3) Modelos (resumen de campos clave)

Source: id (int), type ('web'|'docs'), url (str), name (str|None), config (JSON), rels: runs, documents, chunks.

IngestionRun: id, source_id, status ('running'|'done'|'error'), meta (JSON), created_at.

meta guarda stdout (tail), returncode, cmd, run_dir (web) y/o docs_config/web_config.

Document / Chunk: trazabilidad hacia Source.

4) Ejecución
Pre-requisitos
# Windows PowerShell
$env:FLASK_APP="app:create_app"
$env:FLASK_SECRET_KEY="lo_que_sea_bastante_largo_y_aleatorio"   # o deja que se genere en data/secret_key.txt
# opcional
$env:SQLALCHEMY_DATABASE_URI="sqlite:///data/processed/tracking.sqlite"


Instala dependencias (incluye PyPDF2, docx, bs4, etc.). Luego:

flask run --debug

Flujo UI

Fuentes de datos → crear fuente docs (con input_dir si procede) o web.

Ingesta de Documentos → seleccionar fuente, definir carpeta base/patrones si vas “ad-hoc”, lanzar.

Ingesta Web → seleccionar/guardar fuente, lanzar.

Revisar Ejecuciones recientes: estado, Preview (stdout), y artefactos (web).

5) Validación E2E (lo que se está comprobando ahora)

Documentos:

Fuente docs creada/guardada correctamente.

Al lanzar: se respetan input_dir, recursividad y patrones (*.pdf,*.docx,*.txt,*.csv).

Cambios detectados (hash/mtime) → solo nuevos/modificados.

Artefactos: data/processed/documents/docs.jsonl, chunks.jsonl y runs/*.json.

En la tabla IngestionRun.meta: stdout, returncode, cmd.

Web:

Fuente web guardada con config (strategy, robots_policy, allowed_domains…).

El botón “Lanzar ingesta” ejecuta scripts/ingest_web.py con cwd del proyecto, captura stdout/returncode y ubica run_dir.

Descarga de artefactos segura bajo data/processed/runs.

Errores típicos y solución

RuntimeError: The session is unavailable... → definir FLASK_SECRET_KEY o usar data/secret_key.txt autogenerado (ya soportado).

“(sin salida)” en runs → comprobar dependencias del script, rutas, y que el cwd sea la raíz del repo (ya forzado).

“No module named PyPDF2” → instalar dependencias.

6) Roadmap inmediato (2 sprints)

Sprint A (estabilidad UI ingestas)

✅ Captura robusta de stdout/cmd/returncode.

✅ Normalización y seguridad de rutas de artefactos.

⏳ Métricas visibles por fuente (docs/chunks totales, últimos runs).

⏳ Botón “ver artefactos” por run web y visor simple de JSON.

Sprint B (mejoras pipeline)

Web: sitemap_index, ignore_robots_for, selenium (opcional).

Documentos: política mtime completa + checksum incremental.

Endpoints REST de ingesta (para CI y jobs).

Siguientes

Vector store (FAISS/Chroma), embeddings.

Chat/Comparador.

Benchmark (Recall@k, coste, latencia) y dashboard.

7) Troubleshooting breve

Si una ingesta termina “muy rápida” y sin salida:

Abre la Preview del run → debe mostrar cmd y returncode.

Si returncode != 0, revisa librerías/paths/filtros.

Verifica que el script existe en scripts/ y que el blueprint lo localiza (ya se buscan candidatos y se informa si falta).

8) Decisiones tomadas

Mantener los scripts de ingesta en scripts/ y lanzarlos desde Flask (ventajas: ejecución también por CLI/cron).

Persistir SECRET_KEY de dev en data/secret_key.txt si no hay variable de entorno.

Guardar toda la traza útil del run en IngestionRun.meta.