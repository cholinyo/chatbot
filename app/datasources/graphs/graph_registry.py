# app/datasources/graphs/graph_registry.py
from __future__ import annotations
import os, asyncio, json
from typing import Dict

from dotenv import load_dotenv
from lightrag import LightRAG, QueryParam
from lightrag.llm.openai import gpt_4o_mini_complete  # fallback OpenAI
from app.core.embeddings_registry import get_embedding_from_env

load_dotenv()

BASE_WORKDIR = os.getenv("LIGHTRAG_WORKDIR", "models/kg")


# --- LLM provider selector (ollama | openai) ---
def _ollama_complete_local(prompt: str, **kwargs) -> str:
    """
    Wrapper simple contra Ollama /api/generate.
    Requiere OLLAMA_BASE_URL y LIGHTRAG_LLM_MODEL.
    """
    import requests
    base = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434").rstrip("/")
    model = os.getenv("LIGHTRAG_LLM_MODEL", "llama3.1:8b-instruct")
    url = f"{base}/api/generate"
    payload = {
        "model": model,
        "prompt": prompt,
        "stream": False,
        # Puedes añadir opciones aquí si quieres (temperature, top_p, etc.)
    }
    r = requests.post(url, json=payload, timeout=60)
    r.raise_for_status()
    data = r.json()
    # Los campos comunes son 'response' o 'generated_text' según versión
    return data.get("response") or data.get("generated_text") or ""


def _get_llm_func():
    prov = (os.getenv("LIGHTRAG_LLM_PROVIDER") or "ollama").lower()
    if prov == "openai":
        # usa el helper de LightRAG (necesita OPENAI_API_KEY)
        return gpt_4o_mini_complete
    # por defecto: ollama local (sin depender de lightrag.llm.ollama)
    return _ollama_complete_local


LLM_FUNC = _get_llm_func()


def _workdir_for(namespace: str, emb_dim: int) -> str:
    # p.ej. models/kg/smartcity/emb-384
    w = os.path.join(BASE_WORKDIR, namespace, f"emb-{emb_dim}")
    os.makedirs(w, exist_ok=True)
    return w


async def _ainit_rag(namespace: str) -> LightRAG:
    embedder = get_embedding_from_env()
    workdir = _workdir_for(namespace.lower(), embedder.embedding_dim)
    rag = LightRAG(working_dir=workdir, embedding_func=embedder, llm_model_func=LLM_FUNC)
    await rag.initialize_storages()
    return rag


_RAG_CACHE: Dict[str, LightRAG] = {}


def get_rag(namespace: str) -> LightRAG:
    ns = (namespace or "smartcity").lower()
    if ns not in _RAG_CACHE:
        _RAG_CACHE[ns] = asyncio.run(_ainit_rag(ns))
    return _RAG_CACHE[ns]


def insert_custom_kg(namespace: str, custom_kg: dict):
    return get_rag(namespace).insert_custom_kg(custom_kg)


def query_hybrid(namespace: str, question: str, user_prompt: str | None = None) -> str:
    rag = get_rag(namespace)
    return rag.query(question, param=QueryParam(mode="hybrid", user_prompt=user_prompt))
