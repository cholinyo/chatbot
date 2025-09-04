# Metodología de evaluación de recuperación (RAG)

## Objetivo
Medir la calidad y el rendimiento de los recuperadores usados por la app (FAISS exacto; ChromaDB HNSW) para consultas del dominio, con métricas estándar (Recall@k, MRR) y latencias (p50/p95).

## Métricas de calidad
- **Recall@k (chunk y doc)**: proporción de consultas en las que aparece al menos un objetivo (chunk o documento) entre los k primeros resultados.
- **MRR@k (Mean Reciprocal Rank)**: media de 1/rango del **primer** acierto. Valores guía: 1.0 (siempre #1), ~0.5 (promedio #2), ~0.33 (promedio #3), 0 (nunca en top-k).

## Métricas de rendimiento
- **latency_ms.p50/p95/mean**: mediana, percentil 95 y media de latencia por consulta. p95 describe la “cola” de latencias.

## Tipos de “oro” admitidos (por orden de solidez)
1) **Chunk**: `expected_chunk_id(s)`  
2) **Documento (ID)**: `expected_document_id`  
3) **Documento (título contiene)**: `expected_document_title_contains`  
4) **Texto (chunk contiene)**: `expected_text_contains`  

> Cuando no hay IDs de oro, usamos “contains” robusto (sin tildes, token-based).

## Artefactos y trazabilidad
Cada corrida crea:  
`models/<store>/<collection>/eval/<ts>/{metrics.json, results.jso
