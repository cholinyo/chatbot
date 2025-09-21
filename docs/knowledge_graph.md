# Grafo de Conocimiento (Smart City) — Integración en el RAG Híbrido

> **Objetivo**  
> Integrar los datos de los sistemas Smart City (IoTsens) vía API en un **Grafo de Conocimiento (KG)** usando **LightRAG**, y conectarlo con el **RAG híbrido** del proyecto. Opcionalmente, generar chunks textuales para tu **Vector Store** (FAISS/Chroma) ya existente.

---

## 1) Arquitectura de alto nivel

    +------------------+
    |  IoTsens API     |
    | (devices, data)  |
    +---------+--------+
              |  REST (API key, TenantUUID, OrgUUID)
              v
    +------------------+         +---------------------+
    |  ETL Smart City  |  --->   |  LightRAG (KG)      |
    | (scripts/sync_   |         | - entities/edges    |
    |  iotsens_to_kg)  |         | - chunks de soporte |
    +---------+--------+         +----------+----------+
              |                             |
              | (opcional)                  | consultas híbridas
              v                             v
    +------------------+           +-----------------------------+
    |  BD / Chunks     |  --->     |  RAG Híbrido (Flask/LLM)   |
    |  (bridge KG→VS)  |           |  - vector + subgrafo       |
    +---------+--------+           +-----------------------------+
              |
              v
    +------------------+
    |  FAISS / Chroma  |
    +------------------+

---

## 2) Componentes y ficheros añadidos

> **Nota**: estos ficheros se añaden como módulo nuevo, sin romper la ingesta de documentos/web ni los vector stores actuales.

- `app/integrations/lightrag_core.py`  
  Inicializa **LightRAG** embebido (persistencia en `models/kg`) y expone:
  - `insert_custom_kg(payload: dict)` — inserción de entidades/relaciones/chunks
  - `query_hybrid(question: str)` — consulta híbrida texto+grafo

- `app/integrations/kg_schemas.py`  
  Esquema mínimo de dominio: `Sensor`, `Ubicacion`, `Magnitud`, `Medicion`.

- `app/integrations/kg_adapter.py`  
  Adaptador que **mapea** los objetos anteriores a un `payload` para `insert_custom_kg()`:
  - Entidades: `Sensor`, `Ubicacion`, `Magnitud`, `Medicion`
  - Relaciones: `UBICADO_EN`, `MIDE`, `GENERA`, `CORRESPONDE_A`
  - Chunk textual trazable (fuente: `smartcity/iotsens`)

- `app/integrations/iotsens_client.py`  
  Cliente HTTP parametrizable (env + YAML) para **IoTsens**:
  - `list_devices()` y `measurements_by_device(id, from, to, page_size)`
  - Cabeceras/params para API key + *tenant/organization* (ver §4)

- `config/iotsens_endpoints.yaml`  
  YAML para ajustar **rutas** y **nombres de parámetros** (paginación, filtros temporales, etc.).

- `scripts/sync_iotsens_to_kg.py`  
  **ETL incremental** (últimas N horas) que:
  1) lista dispositivos,
  2) pide mediciones por dispositivo,
  3) inserta entidades/relaciones/chunk en LightRAG.

- *(Opcional)* `app/bridges/kg_to_chunks.py`  
  Puente **KG → BD de Chunks** para reutilizar **FAISS/Chroma** como hasta ahora.

- *(Opcional UI)* `app/blueprints/admin/routes_knowledge_graph.py` + `app/templates/admin/knowledge_graph.html`  
  Pantalla **Admin** para: lanzar sync, smoke test, ver histórico `models/kg`.

---

## 3) Dependencias y entorno

### 3.1. `requirements.txt`
Añade:
lightrag-hku>=0.1.5
httpx>=0.27
pydantic>=2.7
python-dotenv>=1.0
pyyaml>=6.0


### 3.2. Variables de entorno (`.env`)
```env
# --- LightRAG ---
LIGHTRAG_WORKDIR=models/kg
LIGHTRAG_LLM=gpt-4o-mini                # ajustable
LIGHTRAG_EMBED_MODEL=text-embedding-3-large
OPENAI_API_KEY=...

# --- IoTsens ---
IOTSENS_BASE_URL=https://openapi.iotsens.com/
IOTSENS_API_KEY=...                     # tu API key
IOTSENS_TENANT_UUID=...                 # TenantUUID
IOTSENS_ORG_UUID=...                    # OrganizationUUID
IOTSENS_TIMEOUT=20
IOTSENS_TZ=Europe/Madrid                # opcional, para normalización
IOTSENS_PAGE_SIZE=500                   # opcional (lotes)
