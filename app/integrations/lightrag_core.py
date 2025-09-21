import os
import asyncio
from functools import lru_cache
from dotenv import load_dotenv

from lightrag import LightRAG, QueryParam
from lightrag.llm.openai import gpt_4o_mini_complete

from app.core.embeddings_registry import get_embedding_from_env

load_dotenv()

BASE_WORKDIR = os.getenv("LIGHTRAG_WORKDIR", "models/kg")
LLM_FUNC = gpt_4o_mini_complete

def _ensure_graphml_not_empty(workdir: str):
    """Si el GraphML existe pero está vacío, elimínalo para que se regenere."""
    graphml = os.path.join(workdir, "graph_chunk_entity_relation.graphml")
    try:
        if os.path.exists(graphml) and os.path.getsize(graphml) == 0:
            os.remove(graphml)
    except Exception:
        # No bloquear el arranque por esto
        pass

async def _ainit_rag():
    embedder = get_embedding_from_env()
    workdir = os.path.join(BASE_WORKDIR, f"emb-{embedder.embedding_dim}")
    os.makedirs(workdir, exist_ok=True)

    # Blindaje contra ficheros GraphML vacíos/corruptos
    _ensure_graphml_not_empty(workdir)

    rag = LightRAG(
        working_dir=workdir,
        embedding_func=embedder,
        llm_model_func=LLM_FUNC,
    )
    await rag.initialize_storages()
    return rag

@lru_cache(maxsize=1)
def get_rag() -> LightRAG:
    return asyncio.run(_ainit_rag())

def insert_custom_kg(custom_kg: dict):
    rag = get_rag()
    return rag.insert_custom_kg(custom_kg)

def query_hybrid(question: str, user_prompt: str | None = None) -> str:
    rag = get_rag()
    param = QueryParam(mode="hybrid", user_prompt=user_prompt)
    return rag.query(question, param=param)
