# PROJECT\_STRUCTURE.md (snapshot — 2025‑09‑06)

> Estructura actual del proyecto TFM Chatbot RAG tras las últimas ingestas, indexaciones y evaluaciones. Incluye colecciones FAISS/Chroma por modelo (MiniLM, E5, BGE‑M3), artefactos de evaluación y scripts clave.

---

## 1) Árbol de directorios relevante

```text
.
├── data/
│   ├── processed/
│   │   └── tracking.sqlite                  # SQLite principal (tables: documents, chunks, ...)
│   └── validation/
│       └── queries.csv                      # 50 queries con oro (doc_id / chunk_ids / contains)
│
├── models/
│   ├── faiss/
│   │   ├── onda_docs_minilm/
│   │   │   ├── index.faiss
│   │   │   ├── ids.npy
│   │   │   ├── index_meta.json              # {collection, store, model, dim, n_chunks, ...}
│   │   │   ├── index_manifest.json          # lista de chunk_ids indexados + checksum
│   │   │   └── eval/                        # resultados de evaluación para k variados
│   │   ├── onda_docs_e5/
│   │   │   ├── index.faiss
│   │   │   ├── ids.npy
│   │   │   ├── index_meta.json
│   │   │   ├── index_manifest.json
│   │   │   └── eval/
│   │   └── onda_docs_bgem3/
│   │       ├── index.faiss
│   │       ├── ids.npy
│   │       ├── index_meta.json
│   │       ├── index_manifest.json
│   │       └── eval/
│   │
│   ├── chroma/
│   │   ├── onda_docs_minilm/
│   │   │   ├── chroma.sqlite / parquet / data/        # datos internos de Chroma
│   │   │   ├── index_meta.json
│   │   │   ├── index_manifest.json
│   │   │   └── eval/
│   │   ├── onda_docs_e5/
│   │   │   ├── chroma.sqlite / parquet / data/
│   │   │   ├── index_meta.json
│   │   │   ├── index_manifest.json
│   │   │   └── eval/
│   │   └── onda_docs_bgem3/
│   │       ├── chroma.sqlite / parquet / data/
│   │       ├── index_meta.json
│   │       ├── index_manifest.json
│   │       └── eval/
│   │
│   └── compare/
│       └── <collection>/
│           ├── eval/<timestamp>/               # comparativas agregadas por k y store
│           └── diagnose/<timestamp>/           # side-by-side / overlap / presence
│
├── scripts/
│   ├── ingest_web.py                           # crawler (requests/selenium/sitemap)
│   ├── index_chunks.py                         # indexación unificada (FAISS/Chroma)
│   ├── make_queries_template.py                # genera plantilla de queries
│   ├── label_gold_from_db.py                   # etiqueta oro desde SQLite
│   ├── comparativa_recuperadores.py            # eval FAISS vs Chroma (k múltiples)
│   ├── check_docid_presence.py                 # rank del doc_id esperado (k y probe_k)
│   ├── diagnostico_side_by_side.py             # diagnóstico paralelo (overlap jaccard)
│   ├── evaluacion_recuperadores.py             # evaluación simple de un store/colección
│   └── utils_*.py                              # utilidades de soporte (si aplica)
│
├── sentence-transformers/
│   └── all-MiniLM-L6-v2/                       # cache local del modelo MiniLM (HF)
│
├── docs/
│   ├── vector_store.md                         # fundamentos + decisiones (cap. soporte)
│   ├── capitulo_5_*.md                         # capítulo 5 en progreso
│   └── anexos/
│
├── logs/
│   └── *.jsonl                                 # ejecuciones (index/eval) con eventos y métricas
│
└── results/                                    # salidas preparadas para la memoria (tablas/figuras)
```

> **Nota:** Los subárboles exactos de Chroma pueden variar según el backend (SQLite/duckdb/parquet).

---

## 2) Colecciones & modelos

* **Colecciones** evaluadas: `onda_docs_minilm`, `onda_docs_e5`, `onda_docs_bgem3` (≈30k chunks)
* **Modelos** de embedding:

  * `sentence-transformers/all-MiniLM-L6-v2` (D=384)
  * `intfloat/multilingual-e5-base` (D=768)
  * `BAAI/bge-m3` (D variable por modo; usado en modo text embedding)
* **Stores**: `faiss` (IndexFlatIP + normalize\_L2) y `chroma` (HNSW con `hnsw:space=cosine`).

---

## 3) Artefactos y trazabilidad

* `index_meta.json`: metadatos de construcción (modelo, dim, n\_chunks, duración, checksum, source\_ids).
* `index_manifest.json`: conjunto de `chunk_ids` indexados (permite comparar FAISS↔Chroma y detectar desalineaciones).
* `eval/<ts>/results.json`: métricas por corrida (chunk\@k, doc\@k, MRR, latencias).
* `compare/<collection>/evaluate/<ts>/matrix.json|md`: tablas agregadas multi‑k y multi‑store.
* `diagnose/<ts>/summary.json|md`: Jaccard top‑k (chunks/docs), solapes y *top1* divergente.

---

## 4) Bases de datos

* `data/processed/tracking.sqlite`

  * Tablas típicas: `documents(id, title, url, source_id, ...)`, `chunks(id, document_id, index, text, ...)`.
  * Usada por `index_chunks.py` y por los scripts de etiquetado y chequeos.

---

## 5) Comandos frecuentes (historial mínimo reproducible)

### 5.1 Indexación (ejemplos)

```bash
# MiniLM → FAISS (rebuild)
python -m scripts.index_chunks \
  --store faiss --collection onda_docs_minilm \
  --source-id 1 --model sentence-transformers/all-MiniLM-L6-v2 \
  --batch-size 256 --rebuild

# MiniLM → Chroma (rebuild)
python -m scripts.index_chunks \
  --store chroma --collection onda_docs_minilm \
  --source-id 1 --model sentence-transformers/all-MiniLM-L6-v2 \
  --batch-size 256 --rebuild
```

> Para E5/BGE‑M3 cambiar `--collection` y `--model` coherentemente.

### 5.2 Generación y etiquetado de *queries*

```bash
# Plantilla (rellena expected_document_id si title exact match; fallback a frases de chunks)
python -m scripts.make_queries_template --out data/validation/queries.csv \
  --limit 50 --db data/processed/tracking.sqlite --prefill-doc-gold id --min-chunk-scan 10000

# Etiquetado oro (doc_id + chunk_ids top‑N del doc)
python -m scripts.label_gold_from_db \
  --in data/validation/queries.csv --out data/validation/queries.csv \
  --db data/processed/tracking.sqlite --top-chunks 5
```

### 5.3 Evaluación y diagnóstico

```bash
# Comparativa FAISS vs Chroma (k múltiples)
python -m scripts.comparativa_recuperadores \
  --stores chroma,faiss --ks 10,20,40 \
  --collection onda_docs_minilm --queries-csv data/validation/queries.csv \
  --db-path data/processed/tracking.sqlite --model sentence-transformers/all-MiniLM-L6-v2

# Presencia de doc_id esperado (rank@k y rank@probe_k)
python -m scripts.check_docid_presence \
  --collection onda_docs_minilm --stores faiss,chroma \
  --db-path data/processed/tracking.sqlite --queries-csv data/validation/queries.csv \
  --k 20 --probe-k 200 --model sentence-transformers/all-MiniLM-L6-v2 --models-dir models

# Diagnóstico side-by-side (overlap top‑k FAISS↔Chroma)
python -m scripts.diagnostico_side_by_side \
  --collection onda_docs_minilm --stores faiss,chroma \
  --db-path data/processed/tracking.sqlite --queries-csv data/validation/queries.csv \
  --k 20
```

---

## 6) Buenas prácticas asumidas

* **Hard reset + rebuild** ante desalineaciones FAISS↔Chroma (manifests deben coincidir).
* **Consistencia de dimensión**: el modelo de consulta debe coincidir con `dim` del `index_meta.json`.
* **Normalización L2** previa al `IndexFlatIP` (FAISS ≡ coseno).
* **Anotar *source\_id/run\_id*** en metadatos de chunk para filtrados posteriores.
* **Logs JSONL** por corrida (métricas, p50/p95 de latencia, tiempos totales).

---

## 7) Pendientes (trabajo futuro inmediato)

* Barrido `ef_search` y análisis *recall–latencia*.
* Curvas *recall\@k* y *doc\@k* por modelo.
* Ampliar *query set* por categorías (trámites, ordenanzas, actas, turismo).
* Añadir re‑ranking y/híbrido BM25 + denso para comparar.

---

## 8) Glosario mínimo

* **chunk\@k / doc\@k**: porcentaje de consultas que recuperan al menos un chunk/doc relevante en top‑k.
* **MRR**: media del inverso del *rank* del primer acierto.
* **Jaccard**: |A∩B| / |A∪B| entre conjuntos (p. ej., IDs de chunks recuperados por FAISS y Chroma).
* **p50/p95**: percentiles de latencia de la operación de recuperación.
