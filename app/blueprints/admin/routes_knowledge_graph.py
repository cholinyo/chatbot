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

GRAPHML_NAME = "graph_chunk_entity_relation.graphml"


# -------------------------
# Utilidades de rutas (SIN inicializar LightRAG)
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


def _embedding_dim_from_env(default_dim: int = 384) -> int:
    """
    Si alguna vez necesitas leer la dimensión desde tu registry/embeddings, hazlo aquí,
    pero NO importes nada que inicialice LightRAG en rutas sync.
    """
    # Para nuestro caso fijo a 384 (según reglas del proyecto).
    return int(os.getenv("EMBED_DIM", default_dim))


def _deterministic_paths(source: str, emb_dim: int | None = None) -> Tuple[Path, Path, int]:
    """
    Ruta determinista sin depender de RAG:
      LIGHTRAG_WORKDIR (o 'models/kg') / {source} / emb-{dim} / graph_chunk_entity_relation.graphml
    """
    base = os.getenv("LIGHTRAG_WORKDIR", "models/kg")
    dim = int(emb_dim or _embedding_dim_from_env(384))
    workdir = _abs_from_project(Path(base) / source.lower() / f"emb-{dim}")
    workdir.mkdir(parents=True, exist_ok=True)
    graphml = (workdir / GRAPHML_NAME).resolve()
    return workdir, graphml, dim


# -------------------------
# Landing de grafos (página independiente)
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


@bp.get("/knowledge-graphs")
@bp.get("/knowledge-graphs/")
def kg_landing():
    """Página independiente con tarjetas SmartCity/SIA y acciones básicas."""
    kg_sources = [
        _kg_info("smartcity", emb_dim=384),
        _kg_info("sia", emb_dim=384),
    ]
    return render_template("admin/knowledge_graphs.html", kg_sources=kg_sources)


# -------------------------
# Vistas KG (todas sincronas, sin crear event loops)
# -------------------------
@bp.get("/kg")
@bp.get("/kg/")
def kg_home():
    source = (request.args.get("source") or "smartcity").lower()
    workdir, graphml, _ = _deterministic_paths(source)

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
@bp.get("/kg/where/")
def kg_where():
    source = (request.args.get("source") or "smartcity").lower()
    workdir, graphml, dim = _deterministic_paths(source)

    info: Dict[str, Any] = {
        "source": source,
        "embedding_dim": dim,
        "resolved": {
            "workdir": str(workdir),
            "graphml": str(graphml),
            "exists": graphml.exists(),
            "size": (graphml.stat().st_size if graphml.exists() else 0),
        },
    }

    if info["resolved"]["exists"] and info["resolved"]["size"] > 0:
        try:
            G = nx.read_graphml(str(graphml))
            info["resolved"]["nodes"] = G.number_of_nodes()
            info["resolved"]["edges"] = G.number_of_edges()
        except Exception as e:
            info["resolved"]["error"] = f"{type(e).__name__}: {e}"

    return jsonify(info)


@bp.get("/kg/graphml")
@bp.get("/kg/graphml/")
def kg_graphml():
    source = (request.args.get("source") or "smartcity").lower()
    _, graphml, _ = _deterministic_paths(source)
    if not graphml.exists() or graphml.stat().st_size == 0:
        return f"No hay grafo para '{source}'. Esperado en: {graphml}", 404
    return send_file(str(graphml), as_attachment=True, download_name=f"{source}.graphml")


@bp.get("/kg/preview")
@bp.get("/kg/preview/")
def kg_preview():
    try:
        from pyvis.network import Network
    except Exception:
        return (
            "<h3>Preview no disponible</h3><p>Instala <code>pyvis</code> para habilitar la vista interactiva.</p>",
            500,
        )

    source = (request.args.get("source") or "smartcity").lower()
    workdir, graphml, _ = _deterministic_paths(source)

    if not graphml.exists() or graphml.stat().st_size == 0:
        return "Aún no hay grafo generado.", 404

    G = nx.read_graphml(str(graphml))

    # Construcción de red
    net = Network(height="900px", width="100%", bgcolor="#0b0f19", font_color="#e5e7eb", directed=True)
    net.from_nx(G)

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

    type_freq: Dict[str, int] = {}
    for _, data in G.nodes(data=True):
        t = (data.get("type") or "Entity")
        type_freq[t] = type_freq.get(t, 0) + 1

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

    out_path = _abs_from_project(workdir / f"kg_preview_{source}.html")
    net.write_html(str(out_path), notebook=False, open_browser=False)

    if not out_path.exists():
        return "No se pudo generar la vista del grafo.", 500

    return send_file(str(out_path), as_attachment=False)


# -------------------------
# Re-ingesta desde la UI (sin event loop aquí)
# -------------------------
@bp.post("/kg/rebuild")
@bp.get("/kg/rebuild")  # GET opcional para pruebas rápidas desde navegador
def kg_rebuild():
    """
    Lanza la reingesta del grafo indicado.
    Soporta: source=smartcity (scripts.sync_iotsens_to_kg).
    Retorna JSON con stdout/stderr y estado.
    """
    data = request.get_json(silent=True) or request.form or request.args
    source = (data.get("source") or "smartcity").lower()

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


# -------------------------
# Consultas híbridas (AQUÍ sí cargamos LightRAG, pero de forma perezosa)
# -------------------------
@bp.post("/kg/query")
def kg_query():
    data = request.get_json(silent=True) or request.form
    q = (data.get("q") or "").strip()
    source = (data.get("source") or request.args.get("source") or "smartcity").lower()
    if not q:
        return jsonify({"ok": False, "error": "Falta 'q'"}), 400

    # Import perezoso para no tocar event loops en las demás rutas
    from app.datasources.graphs.graph_registry import query_hybrid  # noqa: WPS433

    ans = query_hybrid(source, q)
    return jsonify({"ok": True, "answer": ans})
