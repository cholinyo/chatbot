# Comparativa de recuperadores — colección `chunks_default`

| Store | k | n | chunk@k | MRR | doc@k | docMRR | title@k | text@k | p50 ms | p95 ms | mean ms | eval_dir |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---|
| chroma | 10 | 11 | 0.0% | 0.000 | 0.0% | 0.000 | 0.0% | 18.2% | 21.2 | 243.9 | 61.1 | C:\Users\vcaruncho\CascadeProjects\tfm_chatbot\models\chroma\chunks_default\eval\20250904T162401Z |
| faiss | 10 | 11 | 0.0% | 0.000 | 0.0% | 0.000 | 0.0% | 0.0% | 21.6 | 70.9 | 29.9 | C:\Users\vcaruncho\CascadeProjects\tfm_chatbot\models\faiss\chunks_default\eval\20250904T162426Z |