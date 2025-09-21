# app/blueprints/admin/routes_query_hub.py
from __future__ import annotations

import os
from typing import Dict, Any

from flask import Blueprint, render_template, request, jsonify

from app.datasources.query_hub import (
    list_vector_collections, list_kg_namespaces,
    retrieve_vector, query_kg
)
from app.core.llm_registry import get_llm

bp = Blueprint("query_hub", __name__, url_prefix="/admin")


@bp.get("/query-hub/")
def query_hub_index():
    vec_cols = list_vector_collections()
    kg_names = list_kg_namespaces(emb_dim=384)

    # LLMs visibles por defecto (amplía a gusto)
    ollama_models = ["llama3.1:8b-instruct", "mistral:7b-instruct"]
    openai_models = ["gpt-4o-mini", "gpt-4.1"]

    has_openai = bool(os.getenv("OPENAI_API_KEY"))
    llm_options = {
        "ollama": ollama_models,
        "openai": openai_models if has_openai else [],
    }

    return render_template(
        "admin/query_hub.html",
        vector_collections=vec_cols,
        kg_namespaces=kg_names,
        llm_options=llm_options,
        has_openai=has_openai,
    )


@bp.post("/query-hub/run")
def query_hub_run():
    data: Dict[str, Any] = request.get_json(silent=True) or {}
    mode = (data.get("mode") or "vector").lower()
    q = (data.get("q") or "").strip()
    provider = (data.get("llm_provider") or "ollama").lower()
    model = (data.get("llm_model") or "llama3.1:8b-instruct")
    namespace = (data.get("namespace") or "smartcity").lower()
    collection = data.get("collection")  # {"kind": "faiss|chroma", "name": "..."}

    params = data.get("params") or {}
    temperature = float(params.get("temperature", 0.2))
    vec_params = (params.get("vector") or {})
    k = int(vec_params.get("k", 4))
    mmr = bool(vec_params.get("mmr", False))
    rerank = bool(vec_params.get("rerank", False))

    if not q:
        return jsonify({"ok": False, "error": "Falta 'q'"}), 400
    if mode not in {"vector", "kg", "hybrid"}:
        return jsonify({"ok": False, "error": "Modo inválido"}), 400
    if provider not in {"ollama", "openai"}:
        return jsonify({"ok": False, "error": "Proveedor LLM inválido"}), 400

    # --- Contextos ---
    trace: Dict[str, Any] = {}
    prompt_ctx_parts = []

    if mode in {"vector", "hybrid"}:
        vec = retrieve_vector(q, collection, k=k, mmr=mmr, rerank=rerank)
        trace["vector"] = vec
        if vec.get("as_text"):
            prompt_ctx_parts.append("Fuente VECTOR:\n" + vec["as_text"])

    if mode in {"kg", "hybrid"}:
        kg = query_kg(namespace, q)
        trace["kg"] = kg
        if kg.get("as_text"):
            prompt_ctx_parts.append("Fuente KG:\n" + kg["as_text"])

    ctx_text = "\n\n".join(prompt_ctx_parts).strip() or "(sin contexto recuperado)"

    # --- Prompt de orquestación (simple y claro) ---
    prompt = (
        "Eres un asistente que responde SOLO con la información del contexto.\n"
        "Si no hay datos suficientes, responde 'No tengo datos suficientes'.\n\n"
        f"Contexto:\n{ctx_text}\n\n"
        f"Pregunta: {q}\n"
        "Respuesta concisa:"
    )

    # --- LLM ---
    llm = get_llm(provider, model)
    try:
        out = llm(prompt, temperature=temperature)
    except Exception as e:
        return jsonify({"ok": False, "error": f"LLM error: {type(e).__name__}: {e}"}), 500

    return jsonify({
        "ok": True,
        "answer": out.get("text", ""),
        "llm": {"provider": out.get("provider"), "model": out.get("model"), "latency_s": out.get("latency_s")},
        "trace": trace,
        "prompt_used": prompt,  # opcional: útil para depurar
    })
