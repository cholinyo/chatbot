 TFM / PROJECT_STRUCTURE.md
> Última actualización: 2025-09-03 · TZ: Europe/Madrid  
> Repositorio: https://github.com/cholinyo/chatbot

Este documento describe la **estructura del proyecto**, los **flujos de ingesta**, y la **indexación semántica** con **FAISS** y **ChromaDB**, incluyendo **UI administrativa**, **artefactos**, **métricas**, **reconstrucción masiva**, y una **guía de evaluación**.  
Al final se incluye **bibliografía** y una propuesta para **comparativas de embeddings**.

---

## 1) Estructura del repositorio

chatbot/
├─ README.md
├─ .gitignore
├─ .env.example
├─ requirements.txt
├─ requirements-dev.txt
├─ wsgi.py
│
├─ config/
│ ├─ settings.example.toml # Plantilla (sin secretos)
│ └─ logging.yaml # Logging estructurado
│
├─ app/
│ ├─ init.py # create_app(): config + extensiones
│ ├─ extensions/
│ │ ├─ db.py # SQLAlchemy (engine + SessionLocal)
│ │ └─ logging.py # Inicialización de logging
│ ├─ models/
│ │ ├─ source.py # Source (docs/web)
│ │ ├─ ingestion_run.py # IngestionRun (status/meta)
│ │ ├─ document.py # Document (path/title/size/meta)
│ │ └─ chunk.py # Chunk (document_id, index, text, meta)
│ ├─ blueprints/
│ │ └─ admin/
│ │ ├─ routes_data_sources.py
│ │ ├─ routes_ingesta_docs.py
│ │ ├─ routes_ingesta_web.py
│ │ └─ routes_vector_store.py # UI FAISS/Chroma (build/eval/rebuild)
│ ├─ templates/
│ │ └─ admin/
│ │ ├─ ingesta_docs.html
│ │ ├─ ingesta_web.html
│ │ └─ vector_store.html # UI compartida FAISS/Chroma
│ └─ static/
│ └─ css/custom.css
│
│ └─ rag/ # Núcleo RAG (en evolución)
│ ├─ scrapers/ # requests/selenium/sitemap
│ ├─ processing/ # limpieza, chunking
│ ├─ retrieval/ # (pendiente) BM25/Hybrid/Semantic
│ ├─ embeddings/ # (pendiente) wrappers modelos
│ ├─ generators/ # (pendiente) prompts + LLM
│ ├─ evaluation/ # (pendiente) métricas RAG
│ └─ pipeline/ # (pendiente) orquestación
│
├─ scripts/
│ ├─ ingest_documents.py # Ingesta PDF/DOCX/TXT/CSV → Document/Chunk
│ ├─ ingest_web.py # Ingesta web (requests|selenium|sitemap)
│ ├─ check_sources.py # Utilidad fuentes
│ └─ index_chunks.py # Indexación FAISS/Chroma (+ smoke test)
│
├─ data/
│ ├─ raw/ # Ficheros originales
│ └─ processed/
│ ├─ tracking.sqlite # BD SQLite (Source, Run, Document, Chunk)
│ └─ runs/
│ ├─ docs/run_<id>/ # artefactos ingesta docs
│ └─ web/run_<id>/ # artefactos ingesta web
│
├─ models/
│ ├─ embeddings/ # Caché (si se usa)
│ ├─ faiss/ # Índices FAISS por colección
│ └─ chroma/ # Colecciones ChromaDB por colección
│
├─ logs/
│ └─ ingestion.log # (opcional) log ingestas
│
└─ tests/
├─ test_ingestion.py
├─ test_rag_pipeline.py # (pendiente)
├─ test_retrievers.py # (pendiente)
├─ test_generators.py # (pendiente)
└─ verify_ingestion_sqlite.py # Verificación post-ingesta

markdown
Copiar código

### 1.1 Convenciones
- Python 3.10+ · Flask · SQLAlchemy · requests · beautifulsoup4 · selenium.  
- **Sin frameworks nuevos** (requisito TFM).  
- **Trazabilidad**: `Source → IngestionRun → Document → Chunk` + artefactos por `run_id`.

---

## 2) Configuración y ejecución

1) `.env` (a partir de `.env.example`)
DATABASE_URL=sqlite:///data/processed/tracking.sqlite
LOG_CONFIG=config/logging.yaml
SETTINGS_TOML=config/settings.example.toml

arduino
Copiar código

2) Dependencias
```bash
pip install -r requirements.txt
# desarrollo:
pip install -r requirements-dev.txt
Desarrollo

bash
Copiar código
flask --app app run --debug
3) Esquema de datos (SQLite)
Source: id, type("docs"|"web"), name, config(json)

IngestionRun: id, source_id, started_at, finished_at, status("running|done|error"), meta(json)

Document: id, source_id, path, title, size, meta(json: {fetched_at, run_id, …})

Chunk: id, source_id, document_id, index(ordinal), text, content, meta(json)

Nota: Chunk.ordinal en Python mapea a columna SQL "index".

4) Ingesta web y de documentos (resumen)
scripts/ingest_web.py: estrategias requests | selenium | sitemap, filtros, normalización HTML→texto, iframes same-domain, artefactos bajo data/processed/runs/web/run_<id>/.

scripts/ingest_documents.py: PDF/DOCX/TXT/CSV → Document/Chunk, artefactos en data/processed/runs/docs/run_<id>/.

Ver ejemplos de CLI en los documentos específicos.

5) Vector Store: diseño e implementación
El proyecto soporta dos backends de búsqueda vectorial, seleccionables de forma intercambiable:

FAISS (Facebook AI Similarity Search). Indexamos con IndexFlatIP (producto interno) y normalizamos L2 los embeddings para aproximar la similitud de coseno. Ventaja: búsqueda exacta con implementación simple y controlada.

ChromaDB (persistencia local, índice HNSW por defecto, métrica cosine). Ventaja: baja latencia y escalabilidad con recuperación aproximada; API sencilla con PersistentClient.

5.1 Artefactos por colección
FAISS (models/faiss/<collection>/)

bash
Copiar código
index.faiss              # índice FAISS
ids.npy                  # array paralelo de chunk_ids (orden de inserción)
index_meta.json          # métricas para UI
index_manifest.json      # control de re-indexación por hash de contenido
eval/<ts>/results.json   # (al ejecutar smoke tests desde UI/CLI)
Chroma (models/chroma/<collection>/)

bash
Copiar código
chroma.sqlite3           # metadatos (SQLite)
<uuid_dir>/              # segmentos HNSW internos
index_meta.json
index_manifest.json
eval/<ts>/results.json
Contratos unificados: index_meta.json y index_manifest.json tienen el mismo esquema en FAISS y Chroma para simplificar la UI y la trazabilidad.

5.2 Script unificado: scripts/index_chunks.py
Selección de Chunk desde SQLite con filtros --run-id, --source-id, --limit.

Embeddings con Sentence-Transformers (modelo configurable; batching).

Persistencia en FAISS o Chroma con el mismo contrato de meta/manifest.

Smoke test opcional --smoke-query con retorno estandarizado:

rank, chunk_id, score (FAISS: producto interno; Chroma: 1 - distancia_coseno), title, snippet, document_id, source_id.

Logs estructurados: index.start, emb.batch, plan, index.persist, smoke.results, index.end.

5.2.1 index_meta.json (contrato)
json
Copiar código
{
  "collection": "<name>",
  "store": "faiss|chroma",
  "model": "sentence-transformers/all-MiniLM-L6-v2",
  "dim": 384,
  "n_chunks": 31359,
  "built_at": "2025-09-03T13:12:45+02:00",
  "duration_sec": 957.547,
  "run_ids": [],
  "source_ids": [],
  "checksum": "sha256:<...>",
  "notes": "batched=256, normalized|metric=cosine"
}
5.2.2 index_manifest.json
Control incremental por hash de contenido:

json
Copiar código
{
  "chunk_ids": [ ... ],
  "hash_by_chunk_id": {
    "21105": "sha256:...",
    "21107": "sha256:..."
  }
}
Con --rebuild se rehace desde cero. En Chroma se elimina la carpeta de la colección antes de crearla; en FAISS se re-inicializa el índice.

6) Ejecución: construcción y smoke tests
6.1 Construcción de índice
powershell
Copiar código
# FAISS: colección por defecto
python -m scripts.index_chunks `
  --store faiss `
  --model sentence-transformers/all-MiniLM-L6-v2 `
  --batch-size 256 `
  --rebuild

# Chroma: "chunks_default"
python -m scripts.index_chunks `
  --store chroma `
  --model sentence-transformers/all-MiniLM-L6-v2 `
  --collection chunks_default `
  --batch-size 256 `
  --rebuild
Logs esperados (extracto):

json
Copiar código
{"event":"index.start", ...}
{"event":"select.done","n_input_chunks":31359}
{"event":"plan","n_reindex":31359,"n_skipped":0,"rebuild":true}
{"event":"emb.batch","batch_from":0,"batch_to":256,"batch_size":256}
...
{"event":"index.persist","n_input":31359,"n_reindex":31359,"n_skipped":0,"out_dir":"models\\chroma\\chunks_default"}
{"event":"index.end","duration_ms":957547,"n_chunks":31359,"dim":384}
6.2 Smoke query
powershell
Copiar código
# Top-5 "empadronamiento" sobre Chroma
python -m scripts.index_chunks `
  --store chroma `
  --model sentence-transformers/all-MiniLM-L6-v2 `
  --collection chunks_default `
  --smoke-query "empadronamiento" `
  --k 5
Salida (formato estándar):

json
Copiar código
{
  "event":"smoke.results",
  "k":5,
  "query":"empadronamiento",
  "results":[
    {"rank":1, "chunk_id":21105, "score":0.5758, "title":"Listado de Código SIA ...", "document_id":392, "source_id":1, "snippet":"..."},
    ...
  ]
}
7) UI Administrativa: /admin/vector_store
7.1 Pantalla principal
Construir índice: store (faiss|chroma), model, batch_size, filtros (run_id, source_id, limit), collection, rebuild.

Evaluación rápida: store + collection + model + k + smoke_query → guarda artefactos en models/<store>/<collection>/eval/<timestamp>/.

Reconstrucción masiva:

Modo "collections": recorre models/<store>/ y re-indexa cada colección detectada (presencia de index_meta.json).

Modo "runs": itera IDs de IngestionRun y construye colecciones run_<id>.

Registra artefacto JSON models/<store>/rebuild_<timestamp>.json con:

store, mode, model, batch_size, limit, k, dry_run, started_at, finished_at, total_jobs, results[].

7.2 Métricas del índice (panel)
Lee index_meta.json de models/<store>/<collection>/.

Muestra collection, store, model, dim, n_chunks, duration_sec, built_at, checksum y tamaño en disco (suma de ficheros dentro de la carpeta).

7.3 Histórico de reconstrucciones (panel)
Lee models/<store>/rebuild_*.json (orden desc).

Muestra Fecha, Modo, Store, Duración (s), Modelo, Batch, Limit, k, Dry-run, Jobs, Estado y enlace al Artefacto.

La URL de la página controla el contexto (?store=...&collection=...) para mantener coherencia entre formularios y paneles (los selects de Store actualizan la URL y, por tanto, los paneles).

8) Particularidades técnicas por store
8.1 FAISS
Índice: IndexFlatIP (exacto).

Métrica: similitud de coseno vía normalización L2 + producto interno.

Ventajas: resultados exactos, implementación simple, reproducible, artefactos de disco compactos.

Inconvenientes: coste O(n·d) por consulta; latencia crece con n_chunks.

Uso recomendado: colecciones pequeñas/medias o escenarios donde prima la precisión exacta.

8.2 ChromaDB
Índice: HNSW (aproximado) + metadatos en SQLite; persistencia con PersistentClient (1 carpeta = 1 DB de colección).

Métrica: cosine (no normalizamos explícitamente; se usa la métrica del motor).

Ventajas: baja latencia a gran escala, filtros por metadata, API simple.

Inconvenientes: recuperación aproximada (recall < 100%); más ficheros internos; tuning de HNSW si se requiere.

Uso recomendado: colecciones grandes o con requisitos de latencia y filtros.

Nota: HNSW está ampliamente usado para ANN (Approximate Nearest Neighbors) y ofrece un buen compromiso entre calidad y latencia.

9) Analítica y evaluación
9.1 Métricas operativas (por colección)
Build: duration_sec (de index_meta.json), progreso emb.batch en logs.

Tamaño: suma de ficheros en models/<store>/<collection>/.

Rebuild history: duración (finished_at - started_at), nº de jobs, éxito/errores.

9.2 Métricas de recuperación (smoke/evaluación)
Top-k overlap (FAISS vs Chroma) sobre un conjunto de queries de validación.

Recall@k y MRR si hay ground truth (pares query→chunk esperado).

Latencia (p50/p95) del pipeline de consulta.

Efecto en RAG: calidad de respuesta (juicio humano/LLM), tasa de alucinaciones, nº de contextos usados.

9.3 Protocolo sugerido
Definir 50–100 queries reales en español con expectativa de respuesta.

Ejecutar smoke/eval en ambos stores a k ∈ {5,10,20}.

Comparar overlap, Recall@k, MRR, latencias, tamaño.

(Opcional) Evaluar RAG end-to-end con un mismo generador y prompts idénticos.

10) Troubleshooting
Chroma .query(..., include=[...]): no incluir "ids" en include (el campo ids viene siempre en la respuesta). Un include típico: ["metadatas","distances"].

FAISS con coseno: usar IndexFlatIP y normalizar L2 tanto la base como la query antes de buscar.

Chroma persistencia: PersistentClient(path=...) por colección para separar artefactos por índice.

Sin texto extraíble (ingesta web): ajustar --wait-selector, --iframe-max, y dominios permitidos.

11) Criterios de aceptación (Vector Store)
scripts/index_chunks.py acepta --store {faiss|chroma} y genera índice en models/<store>/<collection>/.

index_meta.json válido: n_chunks, dim, duration_sec, checksum.

Smoke queries por CLI y desde UI /admin/vector_store (Evaluación rápida).

Artefactos de evaluación guardados en models/<store>/<collection>/eval/<ts>/results.json.

Logs estructurados: index.start, index.persist, smoke.results, index.end.

Reconstrucción masiva: genera models/<store>/rebuild_<ts>.json; la UI muestra Store y Duración (s).

12) Roadmap próximo (RAG)
Retrievers: BM25 + Hybrid (BM25 + vec) para comparar con semántico puro.

Generators: prompts multi-turn, citación de fuentes (chunk_ids → Document.title + url/path).

Evaluation: suite reproducible (Recall@k, MRR, F1 respuesta, judge LLM) y dashboards.

13) Comparativas de embeddings (recomendación)
Actualmente usamos sentence-transformers/all-MiniLM-L6-v2 (dim=384, rápido y eficaz). Para robustecer el TFM es interesante probar otros sistemas de embedding (sin introducir frameworks nuevos, usando sentence-transformers):

BAAI/bge-m3 (multilingüe, fuerte en MTEB; mayor dimensión; más pesado).

intfloat/multilingual-e5-base (multilingüe, buen recall).

sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2 (baseline multilingüe clásico).

Cómo probar (solo cambiar el flag --model):

powershell
Copiar código
python -m scripts.index_chunks --store faiss  --model intfloat/multilingual-e5-base --rebuild
python -m scripts.index_chunks --store chroma --model BAAI/bge-m3                  --rebuild
Qué medir:

Calidad: Recall@k / MRR / top-k overlap vs baseline.

Coste: duration_sec de indexación, tamaño en disco, latencia de consulta.

Efecto RAG: precisión percibida, nº de citas útiles, longitud de contexto.

14) Bibliografía y enlaces
FAISS

GitHub (documentación y ejemplos): https://github.com/facebookresearch/faiss

Cosine similarity con FAISS (normalización + inner product): https://github.com/facebookresearch/faiss/wiki/FAQ#how-can-i-use-cosine-similarity

ChromaDB

Documentación oficial: https://docs.trychroma.com/

Persistencia (PersistentClient): https://docs.trychroma.com/usage-guide#persisting-and-loading

Query & resultados (include, ids, metadatas, distances): https://docs.trychroma.com/usage-guide#query-and-get

HNSW

Malkov & Yashunin (2018), Efficient and robust approximate nearest neighbor search using HNSW: https://arxiv.org/abs/1603.09320

Sentence-Transformers

Web oficial / documentación: https://www.sbert.net/

Modelos en Hugging Face (ejemplos):

all-MiniLM-L6-v2: https://huggingface.co/sentence-transformers/all-MiniLM-L6-v2

multilingual-e5-base: https://huggingface.co/intfloat/multilingual-e5-base

BGE-M3: https://huggingface.co/BAAI/bge-m3