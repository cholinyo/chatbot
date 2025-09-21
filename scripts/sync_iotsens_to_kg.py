# app/blueprints/admin/routes_knowledge_graph.py
from __future__ import annotations

import os
import sys
import subprocess
from pathlib import Path
from typing import Dict, Any, Tuple

import networkx as nx
from flask import Blueprint, render_template, request, send_file, jsonify, current_app

bp = Blueprint("admin_kg", __name__, url_prefix="/admin")

# Deps
from app.datasources.graphs.graph_registry import get_rag, query_hybrid, BASE_WORKDIR
from app.core.embeddings_registry import get_embedding_from_env

GRAPHML_NAME = "graph_chunk_entity_relation.graphml"


# -------------------------
# Utilidades de rutas
# -------------------------
def _proj_root() -> Path:
    """Raíz del proyecto (directorio padre de app/)."""
    return Path(current_app.root_path).parent


def _abs_from_project(p: str | Path) -> Path:
    """Devuelve una ruta ABSOLUTA tomando como raíz del proyecto el padre de app/."""
    p = Path(p)
    if p.is_absolute():
        return p
    return (_proj_root() / p).resolve()


def _primary_paths(source: str) -> Tuple[Path, Path]:
    """Ruta oficial de LightRAG (rag.workspace) -> absoluta."""
    rag = get_rag(source)
    workdir_rel = getattr(rag, "workspace", "") or getattr(rag, "working_dir", "") or ""
    workdir = _abs_from_project(workdir_rel) if workdir_rel else _proj_root() / "models" / "kg"
    graphml = workdir / GRAPHML_NAME
    return workdir, graphml


def _fallback_paths(source: str) -> Tuple[Path, Path, int]:
    """
    Ruta determinista sin depender de RAG:
      models/kg/{source}/emb-{dim}/graph_chunk_entity_relation.graphml
    """
    emb = get_embedding_from_env()
    dim = int(getattr(emb, "embedding_dim", 384))
    workdir = _abs_from_project(os.path.join(BASE_WORKDIR or "models/kg", source.lower(), f"emb-{dim}"))
    workdir.mkdir(parents=True, exist_ok=True)
    graphml = workdir / GRAPHML_NAME
    return workdir, graphml, dim


def _resolve_graphml(source: str) -> Tuple[Path, Path]:
    """
    Devuelve (workdir_abs, graphml_abs):
      1) si existe el de rag.workspace, usa ese
      2) si no, usa fallback determinista
    """
    p_workdir, p_graphml = _primary_paths(source)
    if p_graphml.exists() and p_graphml.stat().st_size > 0:
        return p_workdir, p_graphml
    f_workdir, f_graphml, _ = _fallback_paths(source)
    return f_workdir, f_graphml


# -------------------------
# NUEVA landing de grafos
# -------------------------
def _kg_path(ns: str, emb_dim: int = 384) -> Path:
    base = os.getenv("LIGHTRAG_WORKDIR", "models/kg")
    return Path(base).joinpath(ns, f"emb-{emb_dim}", GRAPHML_NAME).resolve()


def _kg_info(ns: str, emb_dim: int = 384) -> dict:
    p = _kg_path(ns, emb_dim)
    exists = p.exists()
    nodes = edges = None
    if exists:
        try:
            G = nx.read_graphml(p)
            nodes, edges = G.number_of_nodes(), G.number_of_edges()
        except Exception:
            pass
    return {"namespace": ns, "path": str(p), "exists": exists, "nodes": nodes, "edges": edges, "emb_dim": emb_dim}


@bp.get("/knowledge-graphs/")
def kg_landing():
    """Página independiente con tarjetas SmartCity/SIA y acciones básicas."""
    kg_sources = [
        _kg_info("smartcity", emb_dim=384),
        _kg_info("sia", emb_dim=384),
    ]
    return render_template("admin/knowledge_graphs.html", kg_sources=kg_sources)


# -------------------------
# Vistas existentes (/admin/kg*)
# -------------------------
@bp.get("/kg")
def kg_home():
    source = (request.args.get("source") or "smartcity").lower()
    workdir, graphml = _resolve_graphml(source)

    nodes = edges = 0
    if graphml.exists() and graphml.stat().st_size > 0:
        try:
            G = nx.read_graphml(str(graphml))
            nodes, edges = G.number_of_nodes(), G.number_of_edges()
        except Exception as e:
            current_app.logger.exception("[KG] Error leyendo GraphML: %s", e)

    return render_template(
        "admin/kg.html",
        source=source,
        nodes=nodes,
        edges=edges,
        workdir=str(workdir),
    )


@bp.get("/kg/where")
def kg_where():
    source = (request.args.get("source") or "smartcity").lower()

    # Primario
    p_workdir, p_graphml = _primary_paths(source)
    p_exists = p_graphml.exists()
    p_size = p_graphml.stat().st_size if p_exists else 0

    # Fallback
    f_workdir, f_graphml, dim = _fallback_paths(source)
    f_exists = f_graphml.exists()
    f_size = f_graphml.stat().st_size if f_exists else 0

    # Resuelto
    r_workdir, r_graphml = _resolve_graphml(source)
    r_exists = r_graphml.exists()
    r_size = r_graphml.stat().st_size if r_exists else 0

    info: Dict[str, Any] = {
        "source": source,
        "embedding_dim": dim,
        "primary": {"workdir": str(p_workdir), "graphml": str(p_graphml), "exists": p_exists, "size": p_size},
        "fallback": {"workdir": str(f_workdir), "graphml": str(f_graphml), "exists": f_exists, "size": f_size},
        "resolved": {"workdir": str(r_workdir), "graphml": str(r_graphml), "exists": r_exists, "size": r_size},
    }

    if r_exists and r_size > 0:
        try:
            G = nx.read_graphml(str(r_graphml))
            info["resolved"]["nodes"] = G.number_of_nodes()
            info["resolved"]["edges"] = G.number_of_edges()
        except Exception as e:
            info["resolved"]["error"] = f"{type(e).__name__}: {e}"

    return jsonify(info)


@bp.get("/kg/graphml")
def kg_graphml():
    source = (request.args.get("source") or "smartcity").lower()
    workdir, graphml = _resolve_graphml(source)
    if not graphml.exists() or graphml.stat().st_size == 0:
        return f"No hay grafo para '{source}'. Esperado en: {graphml}", 404
    return send_file(str(graphml), as_attachment=True)


@bp.post("/kg/query")
def kg_query():
    data = request.get_json(silent=True) or request.form
    q = (data.get("q") or "").strip()
    source = (data.get("source") or request.args.get("source") or "smartcity").lower()
    if not q:
        return jsonify({"ok": False, "error": "Falta 'q'"}), 400
    ans = query_hybrid(source, q)
    return jsonify({"ok": True, "answer": ans})


@bp.get("/kg/preview")
def kg_preview():
    try:
        from pyvis.network import Network
    except Exception:
        return render_template("admin/_kg_preview_missing.html"), 500

    source = (request.args.get("source") or "smartcity").lower()
    workdir, graphml = _resolve_graphml(source)

    if not graphml.exists() or graphml.stat().st_size == 0:
        return "Aún no hay grafo generado.", 404

    G = nx.read_graphml(str(graphml))

    # Construcción de red
    net = Network(height="900px", width="100%", bgcolor="#0b0f19", font_color="#e5e7eb", directed=True)
    net.from_nx(G)

    # Colores por tipo
    color_by_type = {
        "Device": "#60a5fa",
        "Site": "#34d399",
        "Magnitude": "#f59e0b",
        "MagnitudeGroup": "#fbbf24",
        "DeviceCategory": "#a78bfa",
        "Property": "#f472b6",
        "Procedure": "#e879f9",
        "Step": "#22d3ee",
    }

    # Frecuencia por tipo para escalar tamaño (cluster por tipo)
    type_freq: Dict[str, int] = {}
    for _, data in G.nodes(data=True):
        t = (data.get("type") or "Entity")
        type_freq[t] = type_freq.get(t, 0) + 1

    # Estilo nodos (color + tamaño + tooltip)
    for nid, data in G.nodes(data=True):
        t = (data.get("type") or "Entity")
        n = net.get_node(nid)
        if not n:
            continue
        n["color"] = color_by_type.get(t, "#a78bfa")
        freq = max(1, type_freq.get(t, 1))
        n["size"] = 15 + int(80 / freq)
        n["title"] = (
            f"<b>{data.get('entity_name','')}</b>"
            f"<br>type={t}"
            f"<br>{(data.get('description') or '')}"
        )

    net.set_options("""
    {
      "physics": {
        "enabled": true,
        "forceAtlas2Based": {
          "gravitationalConstant": -50,
          "springLength": 120,
          "springConstant": 0.08
        },
        "minVelocity": 0.75,
        "solver": "forceAtlas2Based",
        "timestep": 0.4
      },
      "nodes": { "shape": "dot" },
      "edges": { "color": {"inherit": true}, "smooth": false }
    }
    """)

    out_path = (workdir / f"kg_preview_{source}.html")
    out_path = _abs_from_project(out_path)
    net.write_html(str(out_path), notebook=False, open_browser=False)

    if not out_path.exists():
        return "No se pudo generar la vista del grafo.", 500

    return send_file(str(out_path), as_attachment=False)


# -------------------------
# Re-ingesta desde la UI
# -------------------------
@bp.post("/kg/rebuild")
def kg_rebuild():
    """
    Lanza la reingesta del grafo indicado.
    Soporta: source=smartcity (scripts.sync_iotsens_to_kg).
    Retorna JSON con stdout/stderr y estado.
    """
    data = request.get_json(silent=True) or request.form
    source = (data.get("source") or request.args.get("source") or "smartcity").lower()

    if source != "smartcity":
        return jsonify({"ok": False, "error": f"Rebuild no implementado para '{source}'"}), 400

    cmd = [sys.executable, "-m", "scripts.sync_iotsens_to_kg"]
    env = os.environ.copy()
    env.setdefault("EMBED_PROVIDER", "local")
    env.setdefault("EMBED_PROFILE", "minilm-l6")

    try:
        proc = subprocess.run(
            cmd, env=env, cwd=str(_proj_root()),
            capture_output=True, text=True, check=False
        )
        ok = (proc.returncode == 0)
        payload: Dict[str, Any] = {
            "ok": ok,
            "returncode": proc.returncode,
            "stdout": (proc.stdout or "").splitlines()[-100:],
            "stderr": (proc.stderr or "").splitlines()[-200:],
            "cmd": " ".join(cmd),
        }
        return (jsonify(payload), 200 if ok else 500)
    except Exception as e:
        return jsonify({"ok": False, "error": f"{type(e).__name__}: {e}"}), 500
