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


Contexto (resumen)

Ingesta: Source → IngestionRun → Document → Chunk en SQLite.

Indexado: scripts/index_chunks lee chunks filtrados (p. ej., por --source-id o --run-id) y construye el índice FAISS o Chroma en models/<store>/<collection>/.

Recuperación: el endpoint /rag/query consulta el store + collection activos, devuelve chunk_id+score y enriquece con SQLite (chunk_id → Document.title) para citaciones.

Decisiones clave heredadas:

FAISS con IndexFlatIP + normalización L2 (coseno exacto). 
faiss.ai
datasciencebyexample.com
d2wozrt205r2fu.cloudfront.net

ChromaDB con HNSW (cosine), cliente persistente por collection; respuesta columnar y include=["metadatas","distances"]. 
Chroma Docs
Chroma Cookbook

Embedding por defecto: all-MiniLM-L6-v2 (dim=384), con comparativas previstas frente a multilingual-e5-base y bge-m3. 
Hugging Face
+2
Hugging Face
+2
BGE Model

1) Arquitectura común de indexado (recordatorio)

Script: scripts/index_chunks.py

Selección de chunks desde SQLite (filtros: --source-id, --run-id, --limit).

Plan de reindexado con index_manifest.json (hash por chunk_id; si cambia o --rebuild, se reembebe).

Embeddings por lotes (batch configurable).

Persistencia en el store elegido con el mismo contrato:

index_meta.json: {collection, store, model, dim, n_chunks, built_at, duration_sec, run_ids, source_ids, checksum, notes}

index_manifest.json: {chunk_ids: [...], hash_by_chunk_id: {"<id>":"sha256:..."}}

Smoke query opcional: --smoke-query "<texto>" --k <int> → smoke.results en eval/<ts>/.

Esta capa garantiza la trazabilidad y auditablez entre FAISS y Chroma, independientemente del backend.

2) Preguntas frecuentes y decisiones operativas
2.1 ¿FAISS/Chroma “contienen” los chunks?

No. Los chunks viven en SQLite. FAISS/Chroma almacenan solo los vectores (y, en Chroma, metadatos mínimos por item). Son snapshots del estado de SQLite en el momento del build.

Si ingieres nuevo contenido, el índice no se actualiza solo: reindexa (idealmente con --rebuild para evitar duplicados).

FAISS guarda index.faiss y el mapping posición→chunk_id (manifest). Chroma guarda vectores y metadatos ({"chunk_id": ...}) en su persistencia propia. 
faiss.ai
Chroma Docs

Chequeo rápido:

SELECT COUNT(*) FROM chunks; (SQLite) vs. index_meta.json["n_chunks"] (índice).

Si no cuadra (o cambió el corpus), usa --rebuild.

2.2 ¿Qué significan source_id y run_id? ¿Afectan al índice?

source_id identifica el origen lógico (p. ej., “Legislación S” = 1, “www.onda.es”
 = 999).

run_id identifica una corrida concreta de ese origen (auditoría temporal).

En indexado, se usan como filtros (qué subconjunto entra al índice). El backend de vectores no “pertenece” a un source/run; se documenta el filtro en index_meta.json (source_ids, run_ids) para trazabilidad.

Buenas prácticas:

Colecciones separadas por origen: onda_docs (source 1), onda_web (source 999).

Colección unificada (onda_all) solo si la construyes en una sola pasada (o con upsert confiable) para evitar duplicados.

2.3 ¿Cómo elijo el almacén (FAISS/Chroma) en el RAG?

En la petición: POST /rag/query?store=chroma&collection=onda_docs (o store=faiss).

Por entorno (defaults):

RAG_STORE=chroma|faiss

RAG_COLLECTION=onda_docs

El flujo siempre es: recuperar en el vector store elegido → devolver chunk_id+score → enriquecer desde SQLite para citaciones.

2.4 ¿Puedo buscar en varias colecciones a la vez?

Sí. Sin frameworks nuevos puedes:

Aceptar collections=onda_docs,onda_web (mismo store) o pairs=faiss:onda_docs,chroma:onda_web (mixto).

Recuperar k_per_source candidatos por fuente y fusionar por chunk_id con min–max (normaliza scores por fuente; toma el máximo por chunk_id).

Consideración: usa el mismo modelo de embeddings en todas las colecciones que fusionas; si no, documenta la mezcla.

Chroma devuelve distancias (cosine). Convertimos a similitud como sim = 1 − dist antes de normalizar para fusión, tal y como indican sus docs sobre consultas y forma de resultados. 
Chroma Docs

2.5 ¿Por qué FAISS dio 0% en algún “text@k” y Chroma no?

Caso típico: desalineación de IDs (chunk_id en índice vs. chunks.id en SQLite).

En FAISS, el mapping posición→chunk_id vive en el manifest; si lo construiste con IDs distintos (o cadenas con prefijos), el join contra SQLite puede fallar.

Mitigación: mantener chunk_id numérico estable; si no, normalizar (p. ej., extraer dígitos) antes del JOIN.

Chroma suele ser más tolerante si guardaste {"chunk_id":"<id>"} como metadata y lo usas para el join.

3) FAISS — diseño y razones

Índice: IndexFlatIP (producto interno). Con normalización L2 en base y query, el IP equivale a coseno; es una práctica documentada en FAISS y habitual en IR vectorial. 
faiss.ai
datasciencebyexample.com

Exactitud: búsqueda exacta (recall=100%); coste lineal con N (adecuado para corpus pequeño/medio).

Artefactos: index.faiss, index_meta.json, index_manifest.json (mapping chunk_ids).

Uso: baseline robusto; reproducibilidad alta.

4) ChromaDB — diseño y razones

Persistencia por colección con PersistentClient(...) y vector index HNSW (cosine). Baja latencia y buen equilibrio latencia/recall; el grafo HNSW es el estándar ANN moderno. 
Chroma Cookbook
arXiv
Virtual Server List
Wikipedia

Respuesta columnar (ids, distances, metadatas) y filtros nativos por metadatos (where) y contenido (where_document). 
Chroma Docs
Chroma Cookbook

Metadatos por item (al menos {"chunk_id": "<id>"}), lo que habilita filtrado y diagnóstico sin abrir SQLite.

Limitación: recall < 100% (por ser ANN); se compensa ajustando parámetros (p. ej., ef_search) y con buenas prácticas de corpus/embedding.

5) Glosario (términos usados en el TFM)

Source: origen lógico de contenido (p. ej., carpeta de legislación, dominio web).

IngestionRun: ejecución concreta de ingesta de un Source.

Document / Chunk: documento y fragmento indexable.

Store: backend de vectores (FAISS o Chroma).

Collection: carpeta/espacio de índice dentro del store (p. ej., onda_docs).

Snapshot: instantánea del corpus usado para indexar; cambia tras --rebuild.

Recall@k / MRR@k: métricas IR estándar para “¿está el relevante en el top-k?” y “¿qué tan arriba aparece el primero?”. 
Stanford University
Evidently AI

p50/p95 de latencia: percentiles 50 y 95 del tiempo de respuesta; p95 describe la cola (peores casos habituales). 
DEV Community

6) Procedimiento operativo recomendado
6.1 Ingesta (web/docs)

Crear Source (si no existe) y abrir IngestionRun.

Ejecutar la ingesta (requests/selenium/sitemap) con --source-id y --run-id.

Verificar artefactos del run: stdout.txt, fetch_index.json, summary.json.

6.2 Indexado por colección

Separar por origen para auditoría:

onda_docs ⇢ --source-id 1

onda_web ⇢ --source-id 999

Unificar (opcional) en onda_all en una sola pasada.

Siempre que cambie el corpus, usar --rebuild.

Ejemplos (mismo embedding para comparar stores):

# FAISS
python -m scripts.index_chunks --store faiss  --collection onda_docs --source-id 1   --model sentence-transformers/all-MiniLM-L6-v2 --batch-size 256 --rebuild
# Chroma
python -m scripts.index_chunks --store chroma --collection onda_docs --source-id 1   --model sentence-transformers/all-MiniLM-L6-v2 --batch-size 256 --rebuild

6.3 Sanidad post-build

Comparar COUNT(chunks) (SQLite) vs index_meta.json["n_chunks"].

Ejecutar smoke query y revisar eval/<ts>/stdout.jsonl en la colección.

6.4 Configuración del RAG

Single-store/collection (pruebas A/B):

POST /rag/query?store=chroma&collection=onda_docs

POST /rag/query?store=faiss&collection=onda_docs

Multi-colección:

POST /rag/query?store=chroma&collections=onda_docs,onda_web&merge=minmax

POST /rag/query?pairs=faiss:onda_docs,chroma:onda_web&merge=minmax

Enriquecimiento siempre desde SQLite (chunk_id → Document.title) para citaciones.

7) Embeddings y motivación

Baseline: all-MiniLM-L6-v2 (D=384): rápido y compacto (buen equilibrio para RAG en castellano básico). 
Hugging Face

Comparativas recomendadas:

multilingual-e5-base: multilingüe, suele mejorar recuperación en español y dominios mixtos. 
Hugging Face

bge-m3: permite señales híbridas (denso + disperso) y multilingüe; útil para pruebas de futura hibridación light. 
Hugging Face
BGE Model

En cualquier comparativa, mantén el mismo corpus y construye la misma colección con cada modelo (FAISS y/o Chroma) para comparar manzanas con manzanas.

8) Razones de diseño (síntesis)

Contrato unificado (meta/manifest/logs): simplifica la UI, la auditoría y el “swap” entre stores.

FAISS (exacto) + Chroma (ANN): cubrimos el trade-off recall/latencia documentado en la literatura (HNSW vs flat exact). 
arXiv
Virtual Server List

Normalización L2 + IP para coseno (FAISS): práctica estándar, simple y reproducible. 
faiss.ai
datasciencebyexample.com

Metadatos en Chroma: habilitan filtros nativos y diagnósticos sin tocar la BD; mejora operatividad. 
Chroma Docs
Chroma Cookbook

9) Qué irá en el capítulo vector_store_evaluación (siguiente documento)

Diseño del dataset de validación (data/validation/queries.csv) y “oro” (expected_*).

Métricas: Recall@k, MRR@k y latencias p50/p95 (definiciones y fórmulas). 
Stanford University
Evidently AI
DEV Community

Protocolo de evaluación (scripts, artefactos en models/<store>/<collection>/eval/<ts>/).

Comparativas FAISS vs Chroma, k = 5/10/20, y embeddings alternativos (MiniLM vs E5 vs BGE-M3).

Análisis y conclusiones con impacto en el diseño RAG.

10) Bibliografía y enlaces

FAISS — docs oficiales (similarity search, IndexFlatIP): 
faiss.ai

Coseno con FAISS (normalización L2 + IP) — tutoriales/explicaciones: 
datasciencebyexample.com
d2wozrt205r2fu.cloudfront.net

ChromaDB — Query & Get (forma de resultados, include): 
Chroma Docs

Chroma — arquitectura de consulta (metadata index + vector index HNSW): 
Chroma Cookbook

HNSW — paper (ANN sub-lineal): 
arXiv
Virtual Server List

Embeddings:

all-MiniLM-L6-v2 (384-d): 
Hugging Face

multilingual-e5-base: 
Hugging Face

bge-m3: 
Hugging Face
BGE Model

Métricas IR (Recall/MRR): 
Stanford University
Evidently AI

Latencias percentil (p50/p95): 
DEV Community

11) Anexos (comandos de referencia)

Construcción de índices

# FAISS
python -m scripts.index_chunks --store faiss  --model sentence-transformers/all-MiniLM-L6-v2 --collection chunks_default --batch-size 256 --rebuild
# Chroma
python -m scripts.index_chunks --store chroma --model sentence-transformers/all-MiniLM-L6-v2 --collection chunks_default --batch-size 256 --rebuild


Smoke query

python -m scripts.index_chunks --store chroma --model sentence-transformers/all-MiniLM-L6-v2 --collection chunks_default --smoke-query "empadronamiento" --k 5


RAG (pruebas)

# Single store/collection
curl -X POST "http://localhost:5000/rag/query?store=chroma&collection=onda_docs" \
  -H "Content-Type: application/json" -d '{"query":"empadronamiento","k":5}'
# Multi-colección (fusión min-max)
curl -X POST "http://localhost:5000/rag/query?store=chroma&collections=onda_docs,onda_web&merge=minmax" \
  -H "Content-Type: application/json" -d '{"query":"licencia de obra menor","k":10}'
