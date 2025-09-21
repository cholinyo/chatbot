import os, shutil, glob, time

BASE = os.getenv("LIGHTRAG_WORKDIR", "models/kg")
NAMESPACE = os.getenv("KG_NAMESPACE", "smartcity")
EMB_DIM = os.getenv("KG_EMB_DIM", "384")
target_dir = os.path.join(BASE, NAMESPACE, f"emb-{EMB_DIM}")
os.makedirs(target_dir, exist_ok=True)
target = os.path.join(target_dir, "graph_chunk_entity_relation.graphml")

candidates = [
    os.path.join(BASE, "graph_chunk_entity_relation.graphml"),
    os.path.join(BASE, f"emb-{EMB_DIM}", "graph_chunk_entity_relation.graphml"),
    os.path.join(BASE, NAMESPACE, f"emb-{EMB_DIM}", "graph_chunk_entity_relation.graphml"),  # canónico (por si ya existe)
]

existing = [p for p in candidates if os.path.exists(p)]
if not existing:
    print("No se encontraron GraphML para migrar.")
    raise SystemExit(0)

# elige el más reciente
best = max(existing, key=lambda p: os.path.getmtime(p))
if os.path.abspath(best) == os.path.abspath(target):
    print("Ya está en la ruta canónica:", target)
else:
    shutil.copy2(best, target)
    ts = time.strftime("%Y%m%d_%H%M%S")
    backup = best + f".bak_{ts}"
    shutil.move(best, backup)
    print("Copiado a:", target)
    print("Backup del original en:", backup)
