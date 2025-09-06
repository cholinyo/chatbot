# Comparativa de recuperadores — colección `onda_docs_minilm`

| Store | k | n | chunk@k | MRR | doc@k | docMRR | title@k | text@k | p50 ms | p95 ms | mean ms | eval_dir |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---|
| chroma | 10 | 50 | 2.0% | 0.020 | 8.0% | 0.052 | 8.0% | 0.0% | 21.0 | 35.7 | 28.8 | C:\Users\vcaruncho\CascadeProjects\tfm_chatbot\models\chroma\onda_docs_minilm\eval\20250906T164302Z |
| chroma | 20 | 50 | 4.1% | 0.021 | 8.0% | 0.052 | 8.0% | 0.0% | 22.0 | 32.7 | 24.6 | C:\Users\vcaruncho\CascadeProjects\tfm_chatbot\models\chroma\onda_docs_minilm\eval\20250906T164302Z |
| chroma | 40 | 50 | 8.2% | 0.023 | 10.0% | 0.053 | 10.0% | 0.0% | 21.1 | 43.1 | 24.1 | C:\Users\vcaruncho\CascadeProjects\tfm_chatbot\models\chroma\onda_docs_minilm\eval\20250906T164302Z |
| faiss | 10 | 50 | 2.0% | 0.020 | 6.0% | 0.040 | 6.0% | 0.0% | 17.7 | 32.7 | 21.1 | C:\Users\vcaruncho\CascadeProjects\tfm_chatbot\models\faiss\onda_docs_minilm\eval\20250906T164302Z |
| faiss | 20 | 50 | 4.1% | 0.021 | 8.0% | 0.042 | 8.0% | 0.0% | 19.2 | 31.4 | 21.3 | C:\Users\vcaruncho\CascadeProjects\tfm_chatbot\models\faiss\onda_docs_minilm\eval\20250906T164302Z |
| faiss | 40 | 50 | 8.2% | 0.023 | 10.0% | 0.043 | 10.0% | 0.0% | 19.7 | 30.4 | 23.8 | C:\Users\vcaruncho\CascadeProjects\tfm_chatbot\models\faiss\onda_docs_minilm\eval\20250906T164302Z |
