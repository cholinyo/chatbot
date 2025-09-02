# Arquitectura

## Visión general
Aplicación Flask + SQLAlchemy con pipeline de ingesta (web y documentos), almacenamiento en SQLite, y roadmap hacia indexación vectorial (FAISS/Chroma) y RAG completo.

## Componentes principales
- **App Flask**: blueprints admin, templates y estáticos.
- **Modelos**: Source, IngestionRun, Document, Chunk.
- **Extensiones**: DB y logging estructurado.
- **RAG**: scrapers, normalización, (fut.) retrieval/embeddings/generators.
- **Scripts**: ingest_web, ingest_documents, check_sources, (fut.) index_chunks.

## Flujos
1. Ingesta de documentos (PDF/DOCX/TXT/CSV → Document/Chunk).
2. Ingesta web (requests/selenium/sitemap → Document/Chunk + artefactos).
3. (Roadmap) Indexación vectorial.
4. (Roadmap) Retrieval + Generación (chat RAG).

## Diagramas (a completar)
- Diagrama de componentes
- Diagrama de flujo de ingesta
