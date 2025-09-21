# app/blueprints/admin_kg.py
from __future__ import annotations
import os
from flask import Blueprint, render_template, request, send_file, jsonify
import networkx as nx

from app.datasources.graphs.graph_registry import get_rag, query_hybrid

admin_kg_bp = Blueprint("admin_kg", __name__, url_prefix="/admin")  # <- clave

@admin_kg_bp.route("/kg")
def admin_kg_home():
    source = (request.args.get("source") or "smartcity").lower()
    rag = get_rag(source)
    graphml = os.path.join(rag.workspace, "graph_chunk_entity_relation.graphml")
    nodes = edges = 0
    if os.path.exists(graphml):
        try:
            G = nx.read_graphml(graphml); nodes, edges = G.number_of_nodes(), G.number_of_edges()
        except Exception:
            pass
    return render_template("admin/kg.html", nodes=nodes, edges=edges, workdir=rag.workspace, source=source)

@admin_kg_bp.route("/kg/graphml")
def admin_kg_graphml():
    source = (request.args.get("source") or "smartcity").lower()
    rag = get_rag(source)
    graphml = os.path.join(rag.workspace, "graph_chunk_entity_relation.graphml")
    if not os.path.exists(graphml): return "Aún no hay grafo generado.", 404
    return send_file(graphml, as_attachment=True)

@admin_kg_bp.route("/kg/query", methods=["POST"])
def admin_kg_query():
    data = request.get_json(silent=True) or request.form
    q = data.get("q"); source = (data.get("source") or request.args.get("source") or "smartcity").lower()
    if not q: return jsonify({"ok": False, "error": "Falta 'q'"}), 400
    ans = query_hybrid(source, q)
    return jsonify({"ok": True, "answer": ans})

@admin_kg_bp.route("/kg/preview")
def admin_kg_preview():
    try:
        from pyvis.network import Network
    except Exception:
        return render_template("admin/_kg_preview_missing.html"), 500
    source = (request.args.get("source") or "smartcity").lower()
    rag = get_rag(source)
    graphml = os.path.join(rag.workspace, "graph_chunk_entity_relation.graphml")
    if not os.path.exists(graphml): return "Aún no hay grafo generado.", 404
    G = nx.read_graphml(graphml)
    net = Network(height="900px", width="100%", bgcolor="#111", font_color="#eee", directed=True)
    net.from_nx(G)
    for nid, data in G.nodes(data=True):
        t = (data.get("type") or "Entity")
        color = {"Device":"#60a5fa","Site":"#34d399","Magnitude":"#f59e0b","Procedure":"#e879f9","Step":"#22d3ee"}.get(t,"#a78bfa")
        n = net.get_node(nid)
        if n:
            n["color"] = color
            n["title"] = f"<b>{data.get('entity_name','')}</b><br>type={t}<br>{data.get('description','')}"
    out = os.path.join(rag.workspace, f"kg_preview_{source}.html")
    net.show(out)
    return send_file(out, as_attachment=False)
