
# TFM/PROJECT_STRUCTURE.md — Memoria consolidada (v2025-09-08)

> **Repositorio**: `https://github.com/cholinyo/chatbot`  
> **Proyecto**: Prototipo de Chatbot Interno para Administraciones Locales (Flask + RAG + Comparador de LLMs)  
> **Autor**: Vicente Caruncho Ramos  
> **Zona horaria**: Europe/Madrid

---

## 1) Resumen ejecutivo

- **Objetivo**: Chat interno con arquitectura **RAG** que recupera conocimiento institucional y compara respuestas entre **modelos locales** y **OpenAI**, con **citaciones** a evidencias.
- **Estado**:
  - ✅ **Ingesta** (documentos + web) estable con artefactos de verificación.
  - ✅ **Vector stores**: **FAISS** + **Chroma** con **paridad** y **selector de backend** (faiss|chroma).
  - ✅ **Servicio de búsqueda unificado** (facade sobre adapters FAISS/Chroma).
  - 🚧 **Chat RAG** en integración: endpoints (`/api/chat`, `/api/chat/stream`), **SSE**, memoria de conversación, citaciones, guardrails **CCN/PII**.
  - 🔜 **Comparador** (local vs OpenAI) integrado con el flujo de chat, métricas y feedback.

---

## 2) Estructura de ficheros y directorios

> Mapa resumido (carpeta → propósito → estado).

| Carpeta | Propósito | Estado |
|---|---|---|
| `app/` | App Factory, blueprints, servicios, modelos y plantillas | ✅ base + Chat en curso |
| `app/routes/` | Endpoints (admin, ingest, **chat**) | 🚧 `chat.py` |
| `app/services/` | Lógica de negocio (ingesta, búsqueda, **chat_service**, **prompt_templates**) | ✅/🚧 |
| `app/models/` | ORM SQLAlchemy (Source, IngestionRun, Document, Chunk, **chat models**) | ✅/🚧 |
| `app/templates/` | Vistas Jinja (admin, verificación, **chat/index.html**) | ✅/🚧 |
| `app/static/` | Frontend estático (incl. **js/chat.js**) | ✅/🚧 |
| `config/` | `settings.toml`, `logging.yaml` | ✅ |
| `data/` | Datos y artefactos de ejecuciones | ✅ |
| `docs/` | Documentación del TFM y guías técnicas | ✅ (en expansión) |
| `models/` | Índices FAISS/Chroma persistidos | ✅ |
| `scripts/` | CLI de ingesta/indexado | ✅ |
| `tests/` | Pruebas (verificación y E2E) | ✅/🚧 |
| Raíz | `README.md`, `requirements.txt`, `.gitignore`, `wsgi.py.bak`, `TFM/` | ✅ |

**Árbol (alto nivel):**
```
app/
  routes/            # Blueprints: admin, ingest, chat (nuevo)
  services/          # search_service, chat_service, prompt_templates, adapters
  models/            # ORM: Source, IngestionRun, Document, Chunk, chat*
  templates/         # Jinja: admin, verificación, chat/
  static/            # JS/CSS: chat.js
config/
data/
docs/
models/              # FAISS/Chroma (persistencia)
scripts/             # ingest_documents.py, ingest_web.py, index_chunks.py
tests/               # verify_ingestion_sqlite.py, e2e chat*
README.md
requirements.txt
```

> Idiomas (GitHub): Python ~80%, HTML ~16%, CSS ~4%.

---

## 3) Arquitectura

**Capas:**
1. **Presentación**: Flask + Jinja + JS (UI admin/verificación/chat). **SSE** para streaming.
2. **Servicios**: `search_service` (unificado FAISS/Chroma), `chat_service` (orquestación RAG), `prompt_templates` (Jinja2).
3. **Persistencia**: SQLite (dev) para metadatos, conversaciones y feedback; FS para artefactos e índices.
4. **Vector stores (adapters)**: `faiss_adapter`, `chroma_adapter` con interfaz común (`index`, `query`, `persist`).
5. **LLM providers**: local (`/models`) y OpenAI (API v1.x; clave en `.env`).

**Flujo RAG (chat):**
Ingesta → Chunking → Indexado → **Búsqueda** (k, filtros) → (Re-ranking opc.) → **Templating** → **LLM** → **Respuesta + Citaciones** → **Registro** (tokens, latencias, evidencias, feedback).

---

## 4) Modelos de datos (clave)

- **Source**: definición de origen y política de ingesta.
- **IngestionRun**: ejecución y artefactos (`stdout.txt`, `fetch_index.json`, `summary.json`).
- **Document**: metadatos del documento/URL.
- **Chunk**: fragmento normalizado con referencia a `Document`.
- **Conversation**: sesión de chat (backend, settings, timestamps).
- **Message**: mensajes user/assistant/system con métricas (tokens in/out, latencia).
- **MessageEvidence**: citaciones (chunk_id, score, source, url, rango de caracteres).
- **MessageFeedback**: valoración (up/down/1–5) y comentarios.

---

## 5) Procedimientos (CLI y endpoints)

### 5.1 Ingesta de documentos (FS)
- **Script**: `scripts/ingest_documents.py`
- **Parámetros**: `--source-id`, `--input-dir`, `--recursive`, `--include-ext`, `--exclude-patterns`, `--policy (hash|mtime)`, `--csv.*`

### 5.2 Ingesta web
- **Script**: `scripts/ingest_web.py`
- **Estrategias**: `requests | selenium | sitemap`
- **Banderas**: `--max-pages`, `--timeout`, `--allowed-domains`, `--depth`, `--force-https`, `--robots-policy`, `--dump-html`, `--preview`, `--user-agent`

### 5.3 Indexado y búsqueda
- **Script**: `scripts/index_chunks.py`
- **Args**: `--store faiss|chroma`, `--model`, `--collection`, `--smoke-query`, `--k`
- **Servicio**: `services/search_service.py` (filtros por fuente/fecha, re-ranking opc.)

### 5.4 Chat RAG (backend)
- **Blueprint**: `app/routes/chat.py` → `GET /chat`, `POST /api/chat` (JSON), `GET /api/chat/stream` (SSE)
- **Servicio**: `app/services/chat_service.py` (retrieval → templating → LLM → citaciones)
- **Plantillas**: `app/services/prompt_templates.py` (system + user templates con contexto citables)
- **UI**: `templates/chat/index.html` + `static/js/chat.js`

---

## 6) Artefactos por ejecución

- **Web run**: `data/processed/runs/web/run_<id>/`
  - `stdout.txt`, `fetch_index.json`, `summary.json`, `html/` (si `--dump-html`)
- **FAISS**: `models/faiss/<collection>/`
- **Chroma**: `models/chroma/<collection>/` (si se usa)

---

## 7) Pruebas y verificación

- **Smoke test**: `tests/verify_ingestion_sqlite.py` (persistencia y relaciones mínimas).
- **Paridad de búsqueda**: FAISS vs Chroma (misma colección, k).
- **E2E Chat**: `tests/e2e/test_chat_api.py` (contratos, citaciones presentes, latencias bajo umbral, fallback sin evidencia).

---

## 8) Seguridad, cumplimiento y observabilidad

- **CCN/PII**: mascarado de datos personales antes de proveedores externos; bloqueos explícitos de consultas sensibles.
- **Logging**: JSON con `request-id`, latencias por tramo (retrieval/LLM/total) y tokens in/out.
- **Rate limiting**: límites por IP/sesión.
- **.env**: claves seguras (OpenAI, etc.).

---

## 9) Riesgos y mitigaciones

- **Drift de embeddings** → versionar `embedding_model` por índice.
- **Ruido/duplicados web** → normalización + deduplicación por hash + filtros de boilerplate.
- **Alucinación** → plantillas con *strict citations* y fallback claro.
- **Privacidad** → anonimización previa y políticas de respuesta.

---

## 10) Plan de trabajo (2–4 semanas)

- **Semana 1**: endpoints chat (JSON/SSE), UI mínima, esquema BD de conversación.
- **Semana 2**: observabilidad (métricas), guardrails CCN/PII, tests E2E con *fixtures*.
- **Semana 3**: comparador lado a lado, registro de parámetros por backend.
- **Semana 4**: re-ranking opcional, caché de respuesta, bucle de feedback.

---

## 11) Checklist

- [x] Ingesta FS documentada y estable.
- [x] Ingesta web por CLI con QA y artefactos.
- [x] FAISS + Chroma con selector de backend.
- [x] Servicio de búsqueda unificado.
- [ ] Endpoints `/api/chat` y `/api/chat/stream` con citaciones.
- [ ] Persistencia de conversaciones y feedback.
- [ ] Guardrails CCN/PII activos y trazas completas.
- [ ] Comparador integrado.
- [ ] Documentación `/docs` actualizada.

---

> **Nota**: Este documento consolida la descripción de la **estructura del proyecto** y la **memoria de estado**. Para el detalle del **esqueleto de ficheros del Chat RAG** (código base), ver la sección correspondiente en el documento de revisión del proyecto (canvas) o incorporar aquí en un anexo si se prefiere.
