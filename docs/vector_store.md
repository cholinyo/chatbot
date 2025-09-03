# Capítulo — Almacenamiento vectorial en el TFM (FAISS y ChromaDB)
**Fecha:** 2025-09-03

Este capítulo documenta la integración, operación y evaluación de dos _vector stores_ en la app Flask + SQLAlchemy con pipeline RAG del TFM:

- **FAISS** (búsqueda exacta con `IndexFlatIP` + normalización L2).
- **ChromaDB** (búsqueda aproximada ANN con HNSW y métrica coseno).

La documentación cubre: arquitectura común, artefactos, CLI y UI, guion de implementación, particularidades, comparativa, propuesta de analítica y bibliografía.

---

## 0) Contexto y alcance

El proyecto implementa un sistema RAG que:
1. **Ingiere** contenido (documentos y web) en SQLite (tablas: `Source`, `IngestionRun`, `Document`, `Chunk`).
2. **Genera** embeddings con Sentence-Transformers (por defecto `sentence-transformers/all-MiniLM-L6-v2`, D=384).
3. **Indexa** los embeddings en un _vector store_ seleccionable: **FAISS** o **ChromaDB**.
4. **Recupera** top-k chunks para construir prompts del generador.

El objetivo es disponer de **dos backends** intercambiables con la **misma interfaz** operativa (scripts + UI) y **trazabilidad** homogénea (meta, manifest, logs).

---

## 1) Arquitectura común (FAISS/Chroma)

### 1.1 Flujo de indexación (`scripts/index_chunks.py`)

1. **Selección de chunks** desde SQLite con SQLAlchemy  
   - Filtros: `--run-id`, `--source-id`, `--limit` (sin depender de JSON1, el filtrado por `run_id` se hace en Python).
2. **Plan de reindexado** con `index_manifest.json`  
   - Se calcula `sha256` del texto del chunk; si cambia o `--rebuild`, se re-embebe y re-indexa.
3. **Embeddings** por lotes (batch configurable) con ST.
4. **Persistencia** en el store elegido (`--store faiss|chroma`), con el mismo contrato de:
   - `index_meta.json` (métricas de construcción y estado del índice)
   - `index_manifest.json` (control incremental)
   - Logs estructurados a stdout (JSONL).
5. **Smoke query** opcional: `--smoke-query "<texto>" --k <int>` devuelve un registro `smoke.results` con top-k.

### 1.2 Artefactos en disco

```
models/
  faiss/<collection>/
    index.faiss
    ids.npy
    index_meta.json
    index_manifest.json
    eval/<timestamp>/{stdout.jsonl, results.json}

  chroma/<collection>/
    chroma.sqlite3 + ficheros internos
    index_meta.json
    index_manifest.json
    eval/<timestamp>/{stdout.jsonl, results.json}
```

**`index_meta.json` (contrato):**
```json
{
  "collection": "<str>",
  "store": "faiss|chroma",
  "model": "sentence-transformers/all-MiniLM-L6-v2",
  "dim": 384,
  "n_chunks": <int>,
  "built_at": "<ISO8601>",
  "duration_sec": <float>,
  "run_ids": [<int?>],
  "source_ids": [<int?>],
  "checksum": "sha256:<...>",
  "notes": "batched=..., ..."
}
```

**`index_manifest.json` (control incremental):**
```json
{
  "chunk_ids": [<int>, ...],
  "hash_by_chunk_id": {"<cid>": "<sha256>", "...": "..."}
}
```

### 1.3 Logs estructurados (JSONL)

- `index.start`, `select.done`, `plan`, `emb.batch`, `index.persist`, `smoke.results`, `index.end`  
Ejemplo:
```json
{"event":"index.start","store":"chroma","collection":"chunks_default","batch_size":256,...}
{"event":"select.done","n_input_chunks":31359}
{"event":"plan","n_reindex":31359,"n_skipped":0,"rebuild":true}
...
{"event":"index.persist","n_input":31359,"n_reindex":31359,"n_skipped":0,"out_dir":"models/chroma/chunks_default"}
{"event":"smoke.results","k":5,"query":"empadronamiento","results":[...] }
{"event":"index.end","duration_ms":957547,"n_chunks":31359,"dim":384,"out_dir":"models/chroma/chunks_default"}
```

---

## 2) FAISS — Implementación y operación

### 2.1 Decisiones técnicas
- Índice: **`IndexFlatIP`** (producto interno).  
- Embeddings **normalizados L2** (también la query) ⇒ el IP equivale a **similitud coseno**.
- Recuperación **exacta** (recall 100%), coste lineal con el tamaño del índice.

### 2.2 Persistencia y artefactos
- `index.faiss`: binario del índice.
- `ids.npy`: array paralelo (posición → `chunk_id`).
- `index_meta.json` / `index_manifest.json`: mismo contrato que Chroma.
- `eval/<timestamp>/`: resultados de smoke/evaluaciones.

### 2.3 Smoke (FAISS)
- Embebe y normaliza la consulta, llama `index.search()`, mapea `I`→`chunk_id` con `ids.npy`, y **enriquece** con título/snippet desde la BD para loggear `smoke.results`.

### 2.4 CLI (FAISS)
```bash
# Build / rebuild
python -m scripts.index_chunks --store faiss   --model sentence-transformers/all-MiniLM-L6-v2   --collection chunks_default   --batch-size 256   --rebuild

# Smoke
python -m scripts.index_chunks --store faiss   --model sentence-transformers/all-MiniLM-L6-v2   --collection chunks_default   --smoke-query "empadronamiento" --k 5
```

### 2.5 Ventajas / limitaciones (FAISS)
- ✅ **Recall 100%**, simplicidad y reproducibilidad.
- ✅ Artefactos portables (`index.faiss`, `ids.npy`).
- ⚠️ Latencia **O(N·D)** (lineal). Para grandes N, considerar sharding o índices ANN de FAISS (fuera de alcance por mantener paridad con Chroma).

---

## 3) ChromaDB — Implementación y operación (nuevo)

### 3.1 Decisiones técnicas
- Cliente **persistente** por colección: `PersistentClient(path="models/chroma/<collection>")`.
- Colección: `get_or_create_collection(name, metadata={"hnsw:space":"cosine"}, embedding_function=None)`; se envían **embeddings precomputados**.
- Índice **HNSW** (ANN) con métrica coseno → **baja latencia** y **recall** configurable.
- Particularidad de versión: **Chroma ≥ 0.5** exige `metadatas` **no vacíos** en `add()`.

### 3.2 Metadatos enriquecidos (por chunk) — **añadido en esta iteración**
```json
{
  "chunk_id": "<str>",
  "document_id": "<str>",
  "source_id": "<str>",
  "run_id": "<str|null>",
  "title": "<str>",
  "url": "<str|null>"
}
```
**Usos:**
- Filtros server-side: `where={"source_id":"1","run_id":"392"}` para limitar candidatos antes del scoring.
- Portabilidad: evaluaciones/diagnóstico sin necesidad de la BD SQLite.
- Inspección: trazabilidad 1:1 al `chunk_id`.

### 3.3 Smoke (Chroma)
- Se embebe la consulta y se llama `collection.query(query_embeddings=[...], n_results=k, include=["distances","metadatas"])`.
- Se homogeneiza `score = 1 − distance_cosine` y se complementa con título/snippet desde la BD (mismo formato de salida que FAISS).

### 3.4 CLI (Chroma)
```bash
# Build / rebuild
python -m scripts.index_chunks --store chroma   --model sentence-transformers/all-MiniLM-L6-v2   --collection chunks_default   --batch-size 256   --rebuild

# Smoke
python -m scripts.index_chunks --store chroma   --model sentence-transformers/all-MiniLM-L6-v2   --collection chunks_default   --smoke-query "empadronamiento" --k 5
```

### 3.5 Ventajas / limitaciones (Chroma)
- ✅ **Baja latencia** y buena escalabilidad (HNSW).
- ✅ **Filtros nativos** por metadatos (`where`) y por contenido (`where_document`).
- ✅ **Portabilidad** de contexto (metadatos dentro del índice).
- ⚠️ **Recall < 100%** (ANN); depende de `ef_search`/parámetros del grafo.

---

## 4) UI Admin `/admin/vector_store` (común)

- **Construir índice**: selector `store = faiss|chroma`, `model`, `batch_size`, `run_id`, `source_id`, `collection`, `limit`, `rebuild`.
- **Evaluación rápida**: ejecuta `--smoke-query` y guarda artefactos en `models/<store>/<collection>/eval/<ts>/`:
  - `stdout.jsonl` (todos los logs)
  - `results.json` (solo el registro `smoke.results`)
- **Histórico de reconstrucciones**: `models/<store>/rebuild_*.json` (tabla con fecha, modo, batch, k, OK/ERR).

---

## 5) Guion de implementación (Chroma) — paso a paso

1. **Rama común**: respetar la interfaz FAISS (meta, manifest, logs).
2. **Inicialización Chroma**: `PersistentClient(path=<dir colección>)` + `get_or_create_collection(...)` con `hnsw:space="cosine"` y `embedding_function=None`.
3. **Construcción de metadatos** por chunk desde la selección `Chunk`+`Document`: `{chunk_id, document_id, source_id, run_id, title, url}`.
4. **Inserción por lotes** (`batch_size=4096` recomendado) con `metadatas` **no vacíos** (mínimo `{"chunk_id": ...}`).
5. **Meta/manifest**: actualizar `index_manifest.json` y escribir `index_meta.json` con el mismo contrato que FAISS.
6. **Smoke**: `include=["distances","metadatas"]`; calcular `score = 1 − distance` para homogeneizar.
7. **UI**: mantener rutas y plantillas; `store=chroma` funciona en Construir/Evaluar/Reconstruir.

---

## 6) Comparativa FAISS vs Chroma en este proyecto

| Criterio | FAISS (IndexFlatIP + L2) | ChromaDB (HNSW cosine) |
|---|---|---|
| Tipo de búsqueda | **Exacta** | **Aproximada (ANN)** |
| Métrica | Coseno (vía IP normalizado) | Coseno |
| Recall | **100%** | Alto, **ajustable** (`ef_search`) |
| Latencia | ↑ con N (lineal) | Baja (sub-lineal) |
| Filtros nativos | No (se filtra fuera) | **Sí** (`where`, `where_document`) |
| Metadatos en índice | No (solo `ids.npy`) | **Sí** (enriquecidos) |
| Artefactos | `index.faiss` + `ids.npy` | DB embebida (`chroma.sqlite3`, …) |
| Complejidad op. | Baja | Media (tuning HNSW) |

**Cuándo FAISS**: datasets pequeños/medios, necesidad de recall exacto, operación simple.  
**Cuándo Chroma**: latencia baja a N grande, filtros por metadatos, portabilidad/autonomía del índice.

---

## 7) Propuesta de analítica comparativa

**Objetivo**: medir _trade-offs_ FAISS vs Chroma sobre el **dominio real** (≈31K chunks) y en la **aplicación**.

### 7.1 Métricas
- **Recall@k**, **MRR@k**, **nDCG@k** (si hay ground-truth).
- **Latencia** (p50/p95) separando embedding vs búsqueda.
- **Throughput** (QPS) con carga ligera.
- **Tamaño en disco** del índice y **tiempo de build**.
- (Chroma) Curva **latencia vs recall** variando **`ef_search`** (p.ej., 10/50/100).

### 7.2 Metodología (sin frameworks nuevos)
- Seleccionar 50–200 consultas reales; si es posible, etiquetar relevancia (chunk/doc esperados).
- Crear un pequeño _runner_ en `scripts/` que:
  - Embeba cada consulta (mismo modelo).
  - Interrogue **FAISS** y **Chroma** (con varios `ef_search` y, opcionalmente, filtros `where`).
  - Registre top-k, tiempos y compute métricas.
  - Guarde `models/<store>/<collection>/eval/<ts>/summary.json` con agregados.

### 7.3 Qué esperar
- **FAISS**: recall máximo; latencia mayor en N grandes.
- **Chroma**: latencia menor; recall controlable con `ef_search` (más alto ⇒ más latencia y memoria).

---

## 8) Buenas prácticas y consideraciones

- **FAISS + coseno**: normalizar L2 SIEMPRE (embeddings y query) y usar `IndexFlatIP`.
- **Chroma ≥ 0.5**: `metadatas` NO vacíos; la respuesta de `.query()` es **columnar** (`res["ids"][0]`, `res["distances"][0]`, …); usar `where` para segmentar por `source_id`/`run_id`.
- **Embeddings**: `all-MiniLM-L6-v2` (D=384) equilibra calidad/latencia y reduce huella frente a modelos mayores.
- **Trazabilidad**: mantener `index_meta.json`, `index_manifest.json` y `eval/<ts>/` bajo control de versión o con copias de snapshot si se requieren auditorías.

---

## 9) CLI — referencia rápida

```bash
# Construcción de índice (FAISS)
python -m scripts.index_chunks --store faiss --model sentence-transformers/all-MiniLM-L6-v2   --collection chunks_default --batch-size 256 --rebuild

# Construcción de índice (Chroma)
python -m scripts.index_chunks --store chroma --model sentence-transformers/all-MiniLM-L6-v2   --collection chunks_default --batch-size 256 --rebuild

# Smoke query (FAISS)
python -m scripts.index_chunks --store faiss --model sentence-transformers/all-MiniLM-L6-v2   --collection chunks_default --smoke-query "empadronamiento" --k 5

# Smoke query (Chroma)
python -m scripts.index_chunks --store chroma --model sentence-transformers/all-MiniLM-L6-v2   --collection chunks_default --smoke-query "empadronamiento" --k 5
```

---

## 10) Bibliografía y enlaces

- **FAISS** (docs): https://faiss.ai/  
- **Producto interno + coseno (normalización L2)**:  
  - MyScale: https://www.myscale.com/blog/faiss-cosine-similarity-enhances-search-efficiency/  
  - `IndexFlatIP` (API): https://faiss.ai/cpp_api/struct/structfaiss_1_1IndexFlatIP.html
- **Chroma** (docs):  
  - Query & Get: https://docs.trychroma.com/docs/querying-collections/query-and-get  
  - Metadata filtering (`where`): https://docs.trychroma.com/docs/querying-collections/metadata-filtering  
  - Filtros avanzados (cookbook): https://cookbook.chromadb.dev/core/advanced/queries/  
  - Filtros por documento (cookbook): https://cookbook.chromadb.dev/core/filters/
- **HNSW** (paper original):  
  - arXiv: https://arxiv.org/abs/1603.09320
- **Embeddings**:  
  - all-MiniLM-L6-v2: https://huggingface.co/sentence-transformers/all-MiniLM-L6-v2

---

## 11) Criterios de aceptación (cumplidos)

- `scripts/index_chunks.py` acepta `--store faiss|chroma` y genera índice en `models/<store>/<collection>/`.
- `index_meta.json` coherente; `index_manifest.json` operativo para builds incrementales.
- Smoke queries (CLI/UI) devuelven resultados y se guardan artefactos en `eval/<timestamp>/`.
- Logs estructurados (`index.start`, `index.persist`, `smoke.results`, `index.end`) presentes en stdout/JSONL.
