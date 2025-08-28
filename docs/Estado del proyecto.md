# TFM RAG — Estado del proyecto y guía de estructura (v2025-08-28)

> **Proyecto**: Prototipo de Chatbot RAG para Administraciones Locales (Flask + Python)
> **Autor**: Vicente Caruncho Ramos
> **Contexto**: TFM UJI 2024–2025
> **Objetivo de este documento**: Dejar por escrito **qué hay implementado**, **cómo está estructurado** el repositorio, **modelos de datos**, **pipeline de ingesta de documentos y web**, **logging**, **artefactos generados** y **cómo ejecutar/monitorizar**. Sirve como handoff para continuar el desarrollo (siguientes fuentes: BBDD, APIs; Vector Stores; Chat/Comparador; Benchmarking).

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
* **Ingesta Web (fase inicial)** implementada:

  * Scraper `app/rag/scrapers/requests_bs4.py` con:

    * Seeds múltiples, BFS por profundidad.
    * Normalización de URLs y canonicalización.
    * Robots.txt opcional (activable con `--no-robots`).
    * Include/Exclude por regex o glob.
    * Rate limiter por host.
    * `fetch_url` para estrategias externas (ej. sitemap).
  * Estrategia `sitemap` integrada en `scripts/ingest_web.py`.
  * Nuevo flag `--force-https` para normalizar URLs descubiertas en sitemap.
  * Pruebas realizadas con **portal onda.es**: detección y transformación de URLs, ingestiones completas de hasta **10 páginas / 14 chunks**.
  * Logs detallados: `robots.block`, `robots.block.detail`, `fetch.ok`, `parse.ok`.

---

## 2) Estructura de directorios (actual)

```
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
│  │  └─ ingestion/
│  │     ├─ routes.py
│  │     └─ services.py
│  └─ rag/
│     ├─ processing/
│     │  ├─ cleaners.py
│     │  └─ splitters.py
│     ├─ loaders/
│     │  ├─ pdf_loader.py
│     │  ├─ docx_loader.py
│     │  ├─ txt_loader.py
│     │  └─ csv_loader.py
│     └─ scrapers/
│        └─ requests_bs4.py
│
├─ scripts/
│  ├─ ingest_documents.py
│  ├─ ingest_web.py
│  └─ run_server.py
│
├─ data/
│  ├─ raw/
│  └─ processed/
│     ├─ tracking.sqlite
│     ├─ documents/
│     │  ├─ docs.jsonl
│     │  ├─ chunks.jsonl
│     │  └─ runs/
│     └─ logs/
│        ├─ app.log
│        └─ ingestion.log
│
├─ config/
│  └─ settings.toml
├─ docs/
└─ TFM/
```

---

## 3) Modelos de datos (ORM, SQLite `data/processed/tracking.sqlite`)

*(Sin cambios respecto al documento anterior, salvo que `Source.type` ahora también podrá tomar valor `"web"`).*

---

## 4) Pipeline de Ingesta

### 4.1 Documentos (estable)

*(Idéntico al estado anterior)*

### 4.2 Web (fase inicial)

1. **Estrategia requests+BS4**: BFS con filtros de dominios, incluye/excluye, robots opcional.
2. **Estrategia sitemap**: descubrimiento de URLs desde `sitemap.xml` o índices de sitemap.
3. **Opción --force-https**: reescribe URLs descubiertas de `http://` a `https://`.
4. **CLI ingest\_web.py**: soporta `--seed`, `--strategy {requests_bs4,sitemap}`, `--force-https`, `--no-robots`.
5. **Artefactos**: cada run almacena chunks y metadatos en DB + JSONL.

Limitaciones actuales: bloqueo estricto por robots.txt en onda.es, incluso aunque no exista robots.txt (404). Se ha probado con `--no-robots` para forzar ingestión.

---

## 5) Logging

Idéntico a la fase Documentos, ampliado con:

* Logger `ingestion.web.requests_bs4` y `ingestion.web.cli`.
* Eventos nuevos: `robots.block`, `robots.block.detail`, `fetch.ok`, `parse.ok`.

---

## 6) Artefactos generados

* **DB**: SQLite con tablas `Source`, `Document`, `Chunk`, `IngestionRun`.
* **JSONL**: `docs.jsonl`, `chunks.jsonl`, resúmenes en `runs/`.
* **Logs**: en `data/logs/`.

Ejemplo real: ingesta web onda.es → **10 páginas procesadas, 14 chunks generados**.

---

## 7) Ejecución y validación

### Documentos

*(igual que antes)*

### Web

```powershell
# Sitemap con robots deshabilitado
ython scripts/ingest_web.py --source-id web_onda --strategy sitemap --seed https://www.onda.es/ \
  --allowed-domains www.onda.es --exclude ".*\\.(png|jpg|jpeg|gif|pdf|zip)$" \
  --max-pages 10 --no-robots --verbose
```

---

## 8) Próximos pasos

1. **Web**

   * Añadir soporte `sitemap_index.xml`.
   * Parámetro granular `--ignore-robots-for`.
   * Implementar estrategia **Selenium** para páginas dinámicas.
   * Guardar config web en `Source.config` (url, depth, strategy, robots, force\_https).

2. **Fuentes nuevas**

   * **BBDD** (ej: PostgreSQL, MySQL, SQLite).
   * **APIs** con auth, paginación, rate-limit.

3. **Vector Stores**

   * FAISS + Chroma.
   * Embeddings con `sentence-transformers`.

4. **Chat + Comparador**

   * RAG con trazabilidad de chunks usados.
   * Comparación OpenAI API vs modelos locales.

5. **Benchmarking**

   * Recall\@k, precisión, coste, latencia.

---

## 9) Checklist

* [x] App factory + logging + DB init
* [x] Modelos ORM creados y tablas generadas
* [x] Ingesta Documentos con split y artefactos
* [x] Logging por fichero de ingesta
* [x] Ingesta Web inicial con requests+BS4 y sitemap
* [ ] Endpoints REST de ingesta
* [ ] Configuración UI para fuentes
* [ ] Vector Stores + embeddings
* [ ] Chat/Comparador de modelos
* [ ] Benchmark + dashboard métricas
