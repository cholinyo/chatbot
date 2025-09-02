# Vector Store (Roadmap)

## Objetivo
Indexar `Chunk` en FAISS o Chroma para consultas semánticas.

## Diseño
- CLI: `scripts/index_chunks.py --store {faiss|chroma} --model <name> --limit N --rebuild`.
- Artefactos: `models/{faiss|chroma}/...` + `index_meta.json`.
- Métricas: tiempo construcción, tamaño, Latencia y Recall@k.
