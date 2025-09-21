from __future__ import annotations
import os
import asyncio
from dataclasses import dataclass
from typing import Protocol, List

# Perfiles locales frecuentes (ajusta/añade los tuyos si quieres)
_LOCAL_DEFAULT = "sentence-transformers/all-MiniLM-L6-v2"  # 384
_LOCAL_PROFILES = {
    "minilm-l6": "sentence-transformers/all-MiniLM-L6-v2",                         # 384
    "mpnet-multi": "sentence-transformers/paraphrase-multilingual-mpnet-base-v2",  # 768
    "bge-small": "BAAI/bge-small-en-v1.5",                                         # 384
    "bge-base": "BAAI/bge-base-en-v1.5",                                           # 768
    "bge-m3": "BAAI/bge-m3",                                                       # 1024
    "e5-small": "intfloat/e5-small-v2",                                            # 384
    "e5-base": "intfloat/e5-base-v2",                                              # 768
}

_OPENAI_DIMS = {
    "text-embedding-3-large": 3072,
    "text-embedding-3-small": 1536,
    "text-embedding-ada-002": 1536,
}

class EmbeddingFn(Protocol):
    embedding_dim: int
    async def __call__(self, texts: List[str]) -> List[List[float]]: ...

@dataclass
class _LocalEmbedder:
    model_name: str
    def __post_init__(self):
        from sentence_transformers import SentenceTransformer  # pip install sentence-transformers
        self._m = SentenceTransformer(self.model_name)
        self.embedding_dim = self._m.get_sentence_embedding_dimension()

    async def __call__(self, texts: List[str]) -> List[List[float]]:
        # Ejecutamos el encode en hilo para no bloquear el event loop
        vecs = await asyncio.to_thread(self._m.encode, texts, normalize_embeddings=True)
        return vecs.tolist()

@dataclass
class _OpenAIEmbedder:
    model_name: str
    def __post_init__(self):
        self.embedding_dim = _OPENAI_DIMS.get(self.model_name, 1536)

    async def __call__(self, texts: List[str]) -> List[List[float]]:
        # El helper de LightRAG ya es async
        from lightrag.llm.openai import openai_embed
        return await openai_embed(texts, model=self.model_name)

def get_embedding_from_env() -> EmbeddingFn:
    """
    Selección unificada (para tu ingesta y LightRAG):
      - EMBED_PROVIDER: local | openai | azure
      - EMBED_PROFILE:  (local) minilm-l6 | mpnet-multi | bge-small | e5-small | ...
      - EMBED_MODEL:    (openai/azure) text-embedding-3-small | text-embedding-3-large | ...
    """
    provider = (os.getenv("EMBED_PROVIDER") or os.getenv("LIGHTRAG_EMBED_PROVIDER") or "local").lower()
    profile  = (os.getenv("EMBED_PROFILE")  or "").lower()

    if provider in ("openai", "azure"):
        model = os.getenv("EMBED_MODEL") or os.getenv("LIGHTRAG_EMBED_MODEL") or "text-embedding-3-small"
        return _OpenAIEmbedder(model)

    # Local por defecto
    model_name = _LOCAL_PROFILES.get(profile) or os.getenv("LIGHTRAG_LOCAL_MODEL") or _LOCAL_DEFAULT
    return _LocalEmbedder(model_name)
