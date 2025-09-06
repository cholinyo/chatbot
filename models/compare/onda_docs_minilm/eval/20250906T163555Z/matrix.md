# Comparativa de recuperadores — colección `onda_docs_minilm`

| Store | k | n | chunk@k | MRR | doc@k | docMRR | title@k | text@k | p50 ms | p95 ms | mean ms | eval_dir |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---|
| chroma | 10 | 50 | 2.0% | 0.020 | 8.0% | 0.052 | 8.0% | 0.0% | 20.0 | 38.3 | 28.4 | C:\Users\vcaruncho\CascadeProjects\tfm_chatbot\models\chroma\onda_docs_minilm\eval\20250906T163555Z |
| chroma | 20 | 50 | 4.1% | 0.021 | 8.0% | 0.052 | 8.0% | 0.0% | 20.1 | 39.0 | 23.2 | C:\Users\vcaruncho\CascadeProjects\tfm_chatbot\models\chroma\onda_docs_minilm\eval\20250906T163555Z |
| chroma | 40 | 50 | 8.2% | 0.023 | 10.0% | 0.053 | 10.0% | 0.0% | 20.4 | 37.5 | 22.5 | C:\Users\vcaruncho\CascadeProjects\tfm_chatbot\models\chroma\onda_docs_minilm\eval\20250906T163555Z |
| faiss | 10 | 50 | 2.0% | 0.020 | 6.0% | 0.040 | 6.0% | 0.0% | 19.2 | 36.0 | 22.5 | C:\Users\vcaruncho\CascadeProjects\tfm_chatbot\models\faiss\onda_docs_minilm\eval\20250906T163555Z |
| faiss | 20 | 50 | 4.1% | 0.021 | 8.0% | 0.042 | 8.0% | 0.0% | 20.0 | 36.5 | 22.1 | C:\Users\vcaruncho\CascadeProjects\tfm_chatbot\models\faiss\onda_docs_minilm\eval\20250906T163555Z |
| faiss | 40 | 50 | 8.2% | 0.023 | 10.0% | 0.043 | 10.0% | 0.0% | 19.4 | 34.8 | 21.7 | C:\Users\vcaruncho\CascadeProjects\tfm_chatbot\models\faiss\onda_docs_minilm\eval\20250906T163555Z |
