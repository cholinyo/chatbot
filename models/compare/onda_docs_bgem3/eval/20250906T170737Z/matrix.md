# Comparativa de recuperadores — colección `onda_docs_bgem3`

| Store | k | n | chunk@k | MRR | doc@k | docMRR | title@k | text@k | p50 ms | p95 ms | mean ms | eval_dir |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---|
| chroma | 10 | 50 | 8.2% | 0.070 | 12.0% | 0.087 | 12.0% | 0.0% | 181.5 | 517.6 | 283.4 | C:\Users\vcaruncho\CascadeProjects\tfm_chatbot\models\chroma\onda_docs_bgem3\eval\20250906T170737Z |
| chroma | 20 | 50 | 12.2% | 0.073 | 12.0% | 0.087 | 12.0% | 0.0% | 184.4 | 481.0 | 247.2 | C:\Users\vcaruncho\CascadeProjects\tfm_chatbot\models\chroma\onda_docs_bgem3\eval\20250906T170737Z |
| chroma | 40 | 50 | 16.3% | 0.074 | 16.0% | 0.088 | 16.0% | 0.0% | 203.1 | 848.2 | 293.9 | C:\Users\vcaruncho\CascadeProjects\tfm_chatbot\models\chroma\onda_docs_bgem3\eval\20250906T170737Z |
| faiss | 10 | 50 | 8.2% | 0.070 | 12.0% | 0.083 | 12.0% | 0.0% | 208.1 | 561.3 | 286.3 | C:\Users\vcaruncho\CascadeProjects\tfm_chatbot\models\faiss\onda_docs_bgem3\eval\20250906T170737Z |
| faiss | 20 | 50 | 12.2% | 0.073 | 12.0% | 0.083 | 12.0% | 0.0% | 208.2 | 623.0 | 284.8 | C:\Users\vcaruncho\CascadeProjects\tfm_chatbot\models\faiss\onda_docs_bgem3\eval\20250906T170737Z |
| faiss | 40 | 50 | 16.3% | 0.074 | 16.0% | 0.085 | 16.0% | 0.0% | 207.8 | 582.6 | 279.9 | C:\Users\vcaruncho\CascadeProjects\tfm_chatbot\models\faiss\onda_docs_bgem3\eval\20250906T170737Z |
