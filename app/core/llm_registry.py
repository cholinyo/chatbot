# app/core/llm_registry.py
from __future__ import annotations

import os
import json
import time
from typing import Callable, Dict, Any

import requests

try:
    # openai>=1.x (nuevo SDK). Si no está, lo tratamos como opcional.
    from openai import OpenAI  # type: ignore
except Exception:  # pragma: no cover
    OpenAI = None  # type: ignore


class LLMError(Exception):
    pass


def _ollama_generate(model: str, prompt: str, temperature: float = 0.2, **kwargs) -> Dict[str, Any]:
    base = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434").rstrip("/")
    url = f"{base}/api/generate"
    payload = {
        "model": model,
        "prompt": prompt,
        "stream": False,
        "options": {"temperature": temperature},
    }
    t0 = time.perf_counter()
    resp = requests.post(url, json=payload, timeout=120)
    dt = time.perf_counter() - t0
    if resp.status_code != 200:
        raise LLMError(f"Ollama error {resp.status_code}: {resp.text[:400]}")
    data = resp.json()
    text = data.get("response", "")
    return {"text": text, "provider": "ollama", "model": model, "latency_s": dt}


def _openai_chat(model: str, prompt: str, temperature: float = 0.2, **kwargs) -> Dict[str, Any]:
    if OpenAI is None:
        raise LLMError("Paquete 'openai' no instalado. pip install openai")
    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        raise LLMError("OPENAI_API_KEY no configurada")
    client = OpenAI()
    t0 = time.perf_counter()
    res = client.chat.completions.create(
        model=model,
        messages=[{"role": "user", "content": prompt}],
        temperature=temperature,
    )
    dt = time.perf_counter() - t0
    text = (res.choices[0].message.content or "").strip()
    return {"text": text, "provider": "openai", "model": model, "latency_s": dt}


def get_llm(provider: str, model: str) -> Callable[..., Dict[str, Any]]:
    """
    Devuelve un callable(prompt:str, temperature:float=0.2, **kw)-> dict(text, provider, model, latency_s)
    """
    provider = (provider or "").lower()
    if provider == "ollama":
        return lambda prompt, **kw: _ollama_generate(model, prompt, **kw)
    if provider == "openai":
        return lambda prompt, **kw: _openai_chat(model, prompt, **kw)
    raise LLMError(f"Proveedor LLM no soportado: {provider}")
