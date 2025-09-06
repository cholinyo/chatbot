# Comparativa de recuperadores — colección `onda_docs_minilm`

| Store | k | n | chunk@k | MRR | doc@k | docMRR | title@k | text@k | p50 ms | p95 ms | mean ms | eval_dir |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---|
| chroma | 10 | 50 | 2.0% | 0.020 | 8.0% | 0.052 | 8.0% | 0.0% | 21.3 | 43.2 | 30.8 | C:\Users\vcaruncho\CascadeProjects\tfm_chatbot\models\chroma\onda_docs_minilm\eval\20250906T161316Z |
| chroma | 20 | 50 | 4.1% | 0.021 | 8.0% | 0.052 | 8.0% | 0.0% | 22.0 | 39.1 | 26.9 | C:\Users\vcaruncho\CascadeProjects\tfm_chatbot\models\chroma\onda_docs_minilm\eval\20250906T161316Z |
| chroma | 40 | 50 | 8.2% | 0.023 | 10.0% | 0.053 | 10.0% | 0.0% | 22.8 | 35.8 | 24.2 | C:\Users\vcaruncho\CascadeProjects\tfm_chatbot\models\chroma\onda_docs_minilm\eval\20250906T161316Z |
| faiss | 10 | 50 | 2.0% | 0.020 | 6.0% | 0.040 | 6.0% | 0.0% | 19.3 | 32.6 | 22.1 | C:\Users\vcaruncho\CascadeProjects\tfm_chatbot\models\faiss\onda_docs_minilm\eval\20250906T161316Z |
| faiss | 20 | 50 | 4.1% | 0.021 | 8.0% | 0.042 | 8.0% | 0.0% | 19.7 | 31.3 | 21.4 | C:\Users\vcaruncho\CascadeProjects\tfm_chatbot\models\faiss\onda_docs_minilm\eval\20250906T161316Z |
| faiss | 40 | 50 | 8.2% | 0.023 | 10.0% | 0.043 | 10.0% | 0.0% | 19.9 | 33.6 | 22.3 | C:\Users\vcaruncho\CascadeProjects\tfm_chatbot\models\faiss\onda_docs_minilm\eval\20250906T161316Z |
