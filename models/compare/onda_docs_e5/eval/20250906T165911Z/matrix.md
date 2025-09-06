# Comparativa de recuperadores — colección `onda_docs_e5`

| Store | k | n | chunk@k | MRR | doc@k | docMRR | title@k | text@k | p50 ms | p95 ms | mean ms | eval_dir |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---|
| chroma | 10 | 50 | 12.2% | 0.053 | 12.0% | 0.100 | 12.0% | 0.0% | 68.9 | 159.2 | 112.2 | C:\Users\vcaruncho\CascadeProjects\tfm_chatbot\models\chroma\onda_docs_e5\eval\20250906T165911Z |
| chroma | 20 | 50 | 14.3% | 0.054 | 14.0% | 0.101 | 14.0% | 0.0% | 69.8 | 153.8 | 85.7 | C:\Users\vcaruncho\CascadeProjects\tfm_chatbot\models\chroma\onda_docs_e5\eval\20250906T165911Z |
| chroma | 40 | 50 | 14.3% | 0.054 | 14.0% | 0.101 | 14.0% | 0.0% | 68.9 | 171.2 | 87.2 | C:\Users\vcaruncho\CascadeProjects\tfm_chatbot\models\chroma\onda_docs_e5\eval\20250906T165911Z |
| faiss | 10 | 50 | 12.2% | 0.053 | 12.0% | 0.100 | 12.0% | 0.0% | 70.2 | 167.2 | 91.3 | C:\Users\vcaruncho\CascadeProjects\tfm_chatbot\models\faiss\onda_docs_e5\eval\20250906T165911Z |
| faiss | 20 | 50 | 14.3% | 0.054 | 14.0% | 0.101 | 14.0% | 0.0% | 70.1 | 165.1 | 87.7 | C:\Users\vcaruncho\CascadeProjects\tfm_chatbot\models\faiss\onda_docs_e5\eval\20250906T165911Z |
| faiss | 40 | 50 | 14.3% | 0.054 | 14.0% | 0.101 | 14.0% | 0.0% | 71.6 | 161.4 | 87.2 | C:\Users\vcaruncho\CascadeProjects\tfm_chatbot\models\faiss\onda_docs_e5\eval\20250906T165911Z |
