# Proyecto TFM — Estructura de Ficheros y Directorios

Este documento describe **para qué sirve cada carpeta y archivo** generado por el script de inicialización del proyecto (Flask + RAG + comparador de LLMs).

> Nota: Los archivos creados están vacíos (o con texto mínimo) a propósito. La lógica se implementará fase a fase con trazabilidad y pruebas.

---

## Raíz del proyecto

- **README.md** — Resumen del proyecto y cómo empezar.
- **.gitignore** — Evita versionar artefactos (venv, datos, modelos, configuraciones sensibles).
- **.env.example** — Plantilla de variables sensibles (claves, tokens). Copiar a `.env` (no versionar).
- **requirements.txt** / **requirements-dev.txt** — Dependencias de runtime / desarrollo (rellenar cuando toque).
- **wsgi.py** — Punto de entrada para despliegues WSGI (gunicorn/uwsgi).
- **config/** — Configuración externa (no incluye secretos).
  - **settings.example.toml** — Plantilla de configuración (copiar a `settings.toml`).
  - **logging.yaml** — Configuración de logging estructurado (niveles y formato).
- **docs/** — Documentación del proyecto.
  - **arquitectura.md** — Diagrama y decisiones de diseño del sistema.
  - **decisiones.md** — Registro de ADRs (Architecture Decision Records).
  - **evaluacion.md** — Metodología de evaluación/benchmark.
  - **despliegue.md** — Guía de despliegue.
- **data/** — Datos del sistema (no versionar en su mayoría).
  - **raw/** — Fuentes originales intactas (PDFs, DOCX, HTML, JSON).
  - **processed/** — Texto normalizado, chunks, datasets y logs de ingesta.
  - **web/** — Snapshots HTML/JSON de crawling si aplica.
- **models/** — Artefactos del vector store y cachés.
  - **embeddings/** — Caché de embeddings.
  - **faiss/** — Índices FAISS.
  - **chroma/** — Colecciones ChromaDB.
- **scripts/** — Utilidades PowerShell (ingestas, índices, evaluación). Se añaden más tarde.
- **TFM/** — Memoria y anexos académicos.
- **tests/** — Tests `pytest` (unitarios e integración).

---

## app/ — Aplicación Flask

- **app/__init__.py** — `create_app()`: carga config, registra extensiones y blueprints, errores globales.
- **app/extensions/** — Adapta librerías externas a la app.
  - **__init__.py** — Exposición limpia de extensiones.
  - **db.py** — Inicialización de SQLAlchemy (auditoría y trazabilidad).
  - **cache.py** — Caché (opcional) para respuestas o metadatos.
  - **logging.py** — Logging estructurado (JSON) desde `config/logging.yaml`.
  - **vectorstores.py** — Factoría y chequeo de FAISS/Chroma (rutas, colecciones).
  - **llm_clients.py** — Conectores OpenAI/Ollama con selección dinámica de modelo.
- **app/config/** — Configuraciones por entorno (sin secretos).
  - **base.py** — Valores comunes: parámetros RAG, flags, rutas.
  - **development.py** / **production.py** / **testing.py** — Diferencias mínimas respecto a `base`.
- **app/blueprints/** — Módulos funcionales (rutas + servicios).
  - **main/** — Inicio, Documentación, Acerca de.
    - **routes.py** — Vistas públicas informativas.
  - **chat/** — Chat RAG y comparador.
    - **routes.py** — Endpoints `/chat` y `/chat_comparador`.
    - **services.py** — Orquesta pipeline y LLM dual (Ollama/OpenAI).
  - **ingestion/** — Fuentes de datos y lanzador de ingestas.
    - **routes.py** — UI CRUD de fuentes y botones de ejecución.
    - **services.py** — Coordina loaders, processing, embeddings e indexación.
  - **admin/** — Configuración (Modelos IA, Stores, General).
    - **routes.py** — Pantallas de configuración administrativa.
    - **validators.py** — Validaciones de formularios/JSON.
  - **dashboard/** — KPIs de ingesta, retrieval y evaluación.
    - **routes.py** — Vistas y endpoints de métricas.
    - **services.py** — Agregadores de resultados y consultas.
  - **endpoints/** — Salud del sistema y lista de rutas.
    - **routes.py** — `/status` y `/routes`.
- **app/rag/** — Núcleo RAG (modular).
  - **loaders/** — Entrada por fuente (documentos, web, APIs, BBDD).
    - **pdf_loader.py / docx_loader.py / web_loader.py / api_loader.py / db_loader.py**
  - **processing/** — Limpieza y partición en chunks.
    - **cleaners.py / splitters.py**
  - **embeddings/** — Generación/caché de embeddings.
    - **manager.py**
  - **indexing/** — Creación/actualización de índices vectoriales.
    - **faiss_indexer.py / chroma_indexer.py**
  - **retrieval/** — Búsquedas semánticas con filtros.
    - **faiss_retriever.py / chroma_retriever.py**
  - **generation/** — Prompts y wrappers de LLMs.
    - **prompts.py / openai_generator.py / ollama_generator.py**
  - **pipeline/** — Orquestación end-to-end del RAG.
    - **rag_pipeline.py**
  - **eval/** — Evaluación y benchmarking.
    - **datasets.py / metrics.py / runner.py**
- **app/templates/** — Plantillas Jinja2.
  - **base.html** — Layout base (usa el unificado que ya preparaste).
  - **subcarpetas por blueprint** — Vistas específicas.
- **app/static/** — Activos estáticos (CSS/JS/IMG).
  - **css/custom.css** — ÚNICO punto de estilos corporativos.
  - **js/**, **img/** — Recursos de cliente.

---

## tests/ — Pruebas

- **conftest.py** — Fixtures comunes.
- **test_rag_pipeline.py** — Pipeline E2E.
- **test_retrievers.py** — Recuperadores FAISS/Chroma.
- **test_generators.py** — Generadores LLM (parámetros y trazabilidad).
- **test_ingestion.py** — Ingesta por fuente (incrementalidad).
- **test_eval_runner.py** — Métricas y reporting.

---

## Flujos y buenas prácticas

1. **Ingesta** → `data/processed` (texto + chunks + metadatos) → **Indexación** (FAISS/Chroma).
2. **Chat RAG** → retrieve → (rerank) → generate → **Trazabilidad** (citas, tiempos, coste).
3. **Comparador** → misma recuperación, dos modelos (Ollama/OpenAI) con parámetros controlados.
4. **Evaluación** → datasets + métricas → **Dashboard** para visualizar.

> **Estilos:** toda la apariencia en `app/static/css/custom.css`. No estilos en plantillas o JS.

