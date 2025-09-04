# Comparativa de recuperadores — colección `chunks_default`

| Store | k | n | chunk@k | MRR | doc@k | docMRR | title@k | text@k | p50 ms | p95 ms | mean ms | eval_dir |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---|
| chroma | 5 | 11 | 0.0% | 0.000 | 0.0% | 0.000 | 0.0% | 18.2% | 14.2 | 134.1 | 35.8 | C:\Users\vcaruncho\CascadeProjects\tfm_chatbot\models\chroma\chunks_default\eval\20250904T163023Z |
| chroma | 10 | 11 | 0.0% | 0.000 | 0.0% | 0.000 | 0.0% | 18.2% | 24.0 | 190.1 | 53.8 | C:\Users\vcaruncho\CascadeProjects\tfm_chatbot\models\chroma\chunks_default\eval\20250904T163039Z |
| chroma | 20 | 11 | 0.0% | 0.000 | 20.0% | 0.018 | 0.0% | 36.4% | 21.4 | 163.1 | 46.7 | C:\Users\vcaruncho\CascadeProjects\tfm_chatbot\models\chroma\chunks_default\eval\20250904T163101Z |
| faiss | 5 | 11 | 0.0% | 0.000 | 0.0% | 0.000 | 0.0% | 0.0% | 22.3 | 62.1 | 28.0 | C:\Users\vcaruncho\CascadeProjects\tfm_chatbot\models\faiss\chunks_default\eval\20250904T163122Z |
| faiss | 10 | 11 | 0.0% | 0.000 | 0.0% | 0.000 | 0.0% | 0.0% | 16.2 | 42.9 | 20.7 | C:\Users\vcaruncho\CascadeProjects\tfm_chatbot\models\faiss\chunks_default\eval\20250904T163139Z |
| faiss | 20 | 11 | 0.0% | 0.000 | 0.0% | 0.000 | 0.0% | 0.0% | 19.4 | 49.9 | 24.3 | C:\Users\vcaruncho\CascadeProjects\tfm_chatbot\models\faiss\chunks_default\eval\20250904T163154Z |