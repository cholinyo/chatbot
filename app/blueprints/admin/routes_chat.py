# app/blueprints/admin/routes_chat.py
# -*- coding: utf-8 -*-
"""
Blueprint para chat RAG básico y endpoint de consulta.
Integra con vector stores existentes (FAISS/ChromaDB) y enriquece respuestas desde SQLite.
"""

import json
import logging
import time
from pathlib import Path
from typing import Dict, List, Optional, Any

from flask import Blueprint, request, jsonify, render_template, current_app
from sqlalchemy import func

from app.extensions.db import SessionLocal
from app.models.document import Document
from app.models.chunk import Chunk

# Imports opcionales para vector stores
try:
    import numpy as np
    import faiss
except ImportError:
    faiss = None

try:
    import chromadb
except ImportError:
    chromadb = None

try:
    from sentence_transformers import SentenceTransformer
except ImportError:
    SentenceTransformer = None

bp = Blueprint('chat', __name__, url_prefix='/admin')
logger = logging.getLogger(__name__)

# Cache global para modelo de embeddings
_embedding_model = None

def get_embedding_model(model_name: str = "sentence-transformers/all-MiniLM-L6-v2"):
    """Obtener modelo de embeddings con cache."""
    global _embedding_model
    if _embedding_model is None and SentenceTransformer:
        logger.info(f"Cargando modelo de embeddings: {model_name}")
        _embedding_model = SentenceTransformer(model_name)
    return _embedding_model

def load_faiss_index(collection: str, models_dir: str = "models") -> Optional[Dict[str, Any]]:
    """Cargar índice FAISS y metadatos."""
    try:
        base_path = Path(models_dir) / "faiss" / collection
        index_path = base_path / "index.faiss"
        ids_path = base_path / "ids.npy"
        meta_path = base_path / "index_meta.json"
        
        if not all(p.exists() for p in [index_path, ids_path, meta_path]):
            logger.warning(f"Archivos FAISS faltantes para colección {collection}")
            return None
            
        # Cargar índice y metadatos
        index = faiss.read_index(str(index_path))
        ids = np.load(str(ids_path))
        
        with meta_path.open('r', encoding='utf-8') as f:
            meta = json.load(f)
            
        return {
            "index": index,
            "ids": ids,
            "meta": meta,
            "store_type": "faiss"
        }
    except Exception as e:
        logger.error(f"Error cargando índice FAISS {collection}: {e}")
        return None

def load_chroma_collection(collection: str, models_dir: str = "models") -> Optional[Dict[str, Any]]:
    """Cargar colección ChromaDB."""
    try:
        if not chromadb:
            return None
            
        base_path = Path(models_dir) / "chroma" / collection
        meta_path = base_path / "index_meta.json"
        
        if not meta_path.exists():
            logger.warning(f"Metadatos ChromaDB faltantes para colección {collection}")
            return None
            
        # Cargar cliente y colección
        client = chromadb.PersistentClient(path=str(base_path))
        chroma_collection = client.get_collection(name=collection)
        
        with meta_path.open('r', encoding='utf-8') as f:
            meta = json.load(f)
            
        return {
            "collection": chroma_collection,
            "client": client,
            "meta": meta,
            "store_type": "chroma"
        }
    except Exception as e:
        logger.error(f"Error cargando colección ChromaDB {collection}: {e}")
        return None

def search_faiss(store_data: Dict[str, Any], query_vector: np.ndarray, k: int = 5) -> List[Dict[str, Any]]:
    """Búsqueda en índice FAISS."""
    try:
        index = store_data["index"]
        ids = store_data["ids"]
        
        # Normalizar query para similitud coseno
        query_vector = query_vector / np.linalg.norm(query_vector)
        query_vector = query_vector.reshape(1, -1).astype('float32')
        
        # Búsqueda
        scores, indices = index.search(query_vector, k)
        
        results = []
        for i, (score, idx) in enumerate(zip(scores[0], indices[0])):
            if idx != -1:  # Índice válido
                chunk_id = int(ids[idx])
                results.append({
                    "chunk_id": chunk_id,
                    "score": float(score),
                    "rank": i + 1
                })
        
        return results
    except Exception as e:
        logger.error(f"Error en búsqueda FAISS: {e}")
        return []

def search_chroma(store_data: Dict[str, Any], query_text: str, k: int = 5) -> List[Dict[str, Any]]:
    """Búsqueda en colección ChromaDB."""
    try:
        collection = store_data["collection"]
        
        # ChromaDB maneja embeddings internamente
        response = collection.query(
            query_texts=[query_text],
            n_results=k,
            include=["metadatas", "distances"]
        )
        
        results = []
        if response["ids"] and response["ids"][0]:
            for i, (chunk_id_str, distance) in enumerate(zip(response["ids"][0], response["distances"][0])):
                # Convertir distancia a score (1 - distancia para ChromaDB)
                score = 1.0 - distance
                
                results.append({
                    "chunk_id": int(chunk_id_str),
                    "score": float(score),
                    "rank": i + 1
                })
        
        return results
    except Exception as e:
        logger.error(f"Error en búsqueda ChromaDB: {e}")
        return []

def enrich_results_from_db(chunk_results: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Enriquecer resultados con datos desde SQLite."""
    if not chunk_results:
        return []
    
    try:
        with SessionLocal() as session:
            chunk_ids = [r["chunk_id"] for r in chunk_results]
            
            # Query con JOIN para obtener chunk + document data
            query = session.query(
                Chunk.id,
                Chunk.text,
                Chunk.index,
                Document.title,
                Document.path,
                Document.id.label('document_id')
            ).join(Document, Chunk.document_id == Document.id).filter(
                Chunk.id.in_(chunk_ids)
            )
            
            # Crear mapping de chunk_id -> datos
            chunk_data = {}
            for row in query:
                chunk_data[row.id] = {
                    "text": row.text,
                    "chunk_index": row.index,
                    "document_title": row.title,
                    "document_path": row.path,
                    "document_id": row.document_id
                }
            
            # Enriquecer resultados manteniendo orden original
            enriched = []
            for result in chunk_results:
                chunk_id = result["chunk_id"]
                if chunk_id in chunk_data:
                    enriched.append({
                        **result,
                        **chunk_data[chunk_id]
                    })
                else:
                    logger.warning(f"Chunk {chunk_id} no encontrado en BD")
            
            return enriched
            
    except Exception as e:
        logger.error(f"Error enriqueciendo resultados: {e}")
        return chunk_results

@bp.route('/chat')
def chat_interface():
    """Interfaz de chat RAG."""
    # Listar colecciones disponibles
    available_collections = []
    models_dir = Path("models")
    
    # Buscar colecciones FAISS
    if (models_dir / "faiss").exists():
        for collection_dir in (models_dir / "faiss").iterdir():
            if collection_dir.is_dir() and (collection_dir / "index_meta.json").exists():
                available_collections.append({
                    "name": collection_dir.name,
                    "store": "faiss"
                })
    
    # Buscar colecciones ChromaDB
    if (models_dir / "chroma").exists():
        for collection_dir in (models_dir / "chroma").iterdir():
            if collection_dir.is_dir() and (collection_dir / "index_meta.json").exists():
                available_collections.append({
                    "name": collection_dir.name,
                    "store": "chroma"
                })
    
    return render_template('admin/chat.html', collections=available_collections)

@bp.route('/rag/query', methods=['POST'])
def rag_query():
    """
    Endpoint principal RAG.
    
    Parámetros de query string:
    - store: faiss|chroma
    - collection: nombre de la colección
    
    Body JSON:
    - query: texto de consulta
    - k: número de resultados (default: 5)
    """
    start_time = time.time()
    
    try:
        # Parámetros de URL
        store_type = request.args.get('store', 'chroma')
        collection = request.args.get('collection')
        
        if not collection:
            return jsonify({"error": "Parámetro 'collection' requerido"}), 400
        
        # Body JSON
        data = request.get_json()
        if not data:
            return jsonify({"error": "Body JSON requerido"}), 400
        
        query_text = data.get('query', '').strip()
        k = min(data.get('k', 5), 20)  # Máximo 20 resultados
        
        if not query_text:
            return jsonify({"error": "Campo 'query' requerido"}), 400
        
        # Cargar vector store
        store_data = None
        if store_type == 'faiss':
            store_data = load_faiss_index(collection)
        elif store_type == 'chroma':
            store_data = load_chroma_collection(collection)
        else:
            return jsonify({"error": f"Store type '{store_type}' no soportado"}), 400
        
        if not store_data:
            return jsonify({"error": f"No se pudo cargar {store_type}/{collection}"}), 404
        
        # Realizar búsqueda vectorial
        if store_type == 'faiss':
            # FAISS requiere embeddings explícitos
            model = get_embedding_model(store_data["meta"].get("model", "sentence-transformers/all-MiniLM-L6-v2"))
            if not model:
                return jsonify({"error": "Modelo de embeddings no disponible"}), 500
            
            query_vector = model.encode([query_text])[0]
            chunk_results = search_faiss(store_data, query_vector, k)
        else:
            # ChromaDB maneja embeddings internamente
            chunk_results = search_chroma(store_data, query_text, k)
        
        # Enriquecer con datos de BD
        enriched_results = enrich_results_from_db(chunk_results)
        
        # Calcular métricas
        elapsed_time = (time.time() - start_time) * 1000  # ms
        
        response = {
            "query": query_text,
            "store": store_type,
            "collection": collection,
            "k": k,
            "results": enriched_results,
            "total_results": len(enriched_results),
            "elapsed_ms": round(elapsed_time, 2),
            "model_info": store_data["meta"]
        }
        
        logger.info(f"RAG query '{query_text[:50]}...' -> {len(enriched_results)} results in {elapsed_time:.1f}ms")
        
        return jsonify(response)
        
    except Exception as e:
        logger.error(f"Error en RAG query: {e}")
        return jsonify({"error": "Error interno del servidor"}), 500

@bp.route('/rag/collections')
def list_collections():
    """Listar colecciones disponibles para RAG."""
    try:
        collections = []
        models_dir = Path("models")
        
        # Escanear FAISS
        faiss_dir = models_dir / "faiss"
        if faiss_dir.exists():
            for collection_dir in faiss_dir.iterdir():
                if collection_dir.is_dir():
                    meta_path = collection_dir / "index_meta.json"
                    if meta_path.exists():
                        try:
                            with meta_path.open('r', encoding='utf-8') as f:
                                meta = json.load(f)
                            collections.append({
                                "name": collection_dir.name,
                                "store": "faiss",
                                "chunks": meta.get("n_chunks", 0),
                                "model": meta.get("model", "unknown"),
                                "dim": meta.get("dim", 0),
                                "built_at": meta.get("built_at", "unknown")
                            })
                        except Exception as e:
                            logger.warning(f"Error leyendo meta FAISS {collection_dir.name}: {e}")
        
        # Escanear ChromaDB
        chroma_dir = models_dir / "chroma"
        if chroma_dir.exists():
            for collection_dir in chroma_dir.iterdir():
                if collection_dir.is_dir():
                    meta_path = collection_dir / "index_meta.json"
                    if meta_path.exists():
                        try:
                            with meta_path.open('r', encoding='utf-8') as f:
                                meta = json.load(f)
                            collections.append({
                                "name": collection_dir.name,
                                "store": "chroma",
                                "chunks": meta.get("n_chunks", 0),
                                "model": meta.get("model", "unknown"),
                                "dim": meta.get("dim", 0),
                                "built_at": meta.get("built_at", "unknown")
                            })
                        except Exception as e:
                            logger.warning(f"Error leyendo meta ChromaDB {collection_dir.name}: {e}")
        
        return jsonify({
            "collections": collections,
            "total": len(collections)
        })
        
    except Exception as e:
        logger.error(f"Error listando colecciones: {e}")
        return jsonify({"error": "Error interno del servidor"}), 500