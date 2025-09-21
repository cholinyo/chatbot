from __future__ import annotations
from typing import List, Dict, Any, Optional
import re
import json

from app.integrations.kg_schemas import Sensor, Ubicacion, Magnitud


# ----------------- utilidades -----------------
def _slug(s: str) -> str:
    s = (s or "").strip().lower()
    s = re.sub(r"[^a-z0-9:_\-. ]+", "", s)
    s = s.replace(" ", "_")
    return s or "unknown"


def _keywords(*vals: str) -> List[str]:
    toks: List[str] = []
    for v in vals:
        if not v:
            continue
        parts = re.split(r"[,\s;/|]+", str(v).strip())
        for p in parts:
            p = p.strip().lower()
            if p and p not in toks:
                toks.append(p)
    return toks or ["iot", "sensor"]


def _sanitize_scalar(v: Any) -> Any:
    """GraphML admite solo escalar; None→"", listas/dicts→JSON string."""
    if v is None:
        return ""
    if isinstance(v, (str, int, float, bool)):
        return v
    try:
        return json.dumps(v, ensure_ascii=False)
    except Exception:
        return str(v)


# ----------------- constructores básicos -----------------
def _entity(
    eid: str,
    name: str,
    etype: str,
    description: str,
    *,
    source_id: str,
    file_path: str | None = None,
    keywords: List[str] | None = None,
) -> Dict[str, Any]:
    return {
        "id": _sanitize_scalar(eid),
        "entity_name": _sanitize_scalar(name),
        "label": _sanitize_scalar(name),  # útil para pyvis
        "type": _sanitize_scalar(etype),
        "description": _sanitize_scalar(description),
        "content": _sanitize_scalar(description),
        "source_id": _sanitize_scalar(source_id),
        "file_path": _sanitize_scalar(file_path or ""),
        "keywords": _sanitize_scalar(keywords or ["entity"]),  # JSON string si es lista
    }


def _rel(
    src: str,
    tgt: str,
    rtype: str,
    description: str,
    *,
    source_id: str,
    keywords: List[str] | None = None,
    weight: float | None = None,
) -> Dict[str, Any]:
    return {
        "src_id": _sanitize_scalar(src),  # LightRAG espera src_id/tgt_id
        "tgt_id": _sanitize_scalar(tgt),
        "type": _sanitize_scalar(rtype),
        "description": _sanitize_scalar(description),
        "content": _sanitize_scalar(description),
        "source_id": _sanitize_scalar(source_id),
        "keywords": _sanitize_scalar(keywords or ["relationship"]),
        "weight": float(weight if weight is not None else 1.0),
    }


# ----------------- builder principal -----------------
def build_custom_kg_device_only(
    sensor: Sensor,
    ubic: Ubicacion,
    magnitudes: List[Magnitud] | None = None,
    *,
    properties: Optional[Dict[str, str]] = None,
    device_category: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Crea un KG con:
      - Device
      - Site
      - Magnitudes (compartidas por nombre)
      - Category (compartida por nombre), relación TYPE_OF
      - Properties (clave=valor) como nodos Property, relación HAS_PROPERTY
    """
    magnitudes = magnitudes or []
    properties = properties or {}

    # IDs canónicos
    dev_id = f"device:{_slug(sensor.id_sensor)}"
    site_raw = ubic.id_site or f"asset-unknown-{sensor.id_sensor}"
    site_id = f"site:{_slug(site_raw)}"

    dev_name = sensor.alias or sensor.id_sensor
    dev_type = sensor.tipo or "Device"   # lo dejamos como tipo “Device” semántico si quieres homogeneizar
    site_name = ubic.direccion or site_raw

    lat_s = "" if ubic.lat is None else str(ubic.lat)
    lon_s = "" if ubic.lon is None else str(ubic.lon)

    # --------- ENTITIES ---------
    entities: List[Dict[str, Any]] = []

    ent_device = _entity(
        eid=dev_id,
        name=dev_name,
        etype="Device",  # homogeneizamos visualmente
        description=(
            f"Device '{dev_name}' [{dev_type}] site_id={site_raw} "
            f"lat={lat_s} lon={lon_s} serial={sensor.serial or sensor.id_sensor}"
        ),
        source_id=f"iotsens:{sensor.id_sensor}",
        keywords=_keywords(dev_name, dev_type, sensor.categoria or "", sensor.serial or "", sensor.id_sensor),
    )
    entities.append(ent_device)

    ent_site = _entity(
        eid=site_id,
        name=site_name,
        etype="Site",
        description=(f"Site '{site_name}' id={site_raw} lat={lat_s} lon={lon_s}"),
        source_id=f"iotsens-site:{site_raw}",
        keywords=_keywords(site_name, "site", site_raw),
    )
    entities.append(ent_site)

    # Categoria (compartida)
    cat_id = None
    if device_category:
        cat_id = f"category:{_slug(device_category)}"
        entities.append(
            _entity(
                eid=cat_id,
                name=device_category,
                etype="DeviceCategory",
                description=f"Device category '{device_category}'",
                source_id=f"iotsens-category:{_slug(device_category)}",
                keywords=_keywords(device_category, "category"),
            )
        )

    # Magnitudes (compartidas por nombre)
    mag_entities_ids: List[str] = []
    for m in magnitudes:
        mid = f"magnitude:{_slug(m.nombre)}"
        if mid not in mag_entities_ids:
            mag_entities_ids.append(mid)
        entities.append(
            _entity(
                eid=mid,
                name=m.nombre,
                etype="Magnitude",
                description=f"Magnitude '{m.nombre}' measured by device '{dev_name}'",
                source_id=f"iotsens-mag:{sensor.id_sensor}:{m.nombre}",
                keywords=_keywords(m.nombre, "magnitude"),
            )
        )

    # Properties (clave=valor) como nodos Property compartidos
    prop_ids: List[str] = []
    for k, v in properties.items():
        k_clean = k.strip()
        if not k_clean:
            continue
        name = f"{k_clean}={v}"
        pid = f"property:{_slug(k_clean)}={_slug(v)}"
        prop_ids.append(pid)
        entities.append(
            _entity(
                eid=pid,
                name=name,
                etype="Property",
                description=f"Property {k_clean}={v}",
                source_id=f"iotsens-prop:{sensor.id_sensor}:{k_clean}",
                keywords=_keywords("property", k_clean, str(v)),
            )
        )

    # --------- RELATIONSHIPS ---------
    relationships: List[Dict[str, Any]] = []

    # ubicación
    relationships.append(
        _rel(
            src=dev_id,
            tgt=site_id,
            rtype="INSTALLED_AT",
            description=f"Device '{dev_name}' is installed at site '{site_name}'",
            source_id=f"iotsens:{sensor.id_sensor}",
            keywords=_keywords("installed", "site", site_name, sensor.categoria or "", dev_type),
        )
    )

    # tipo/categoría
    if cat_id:
        relationships.append(
            _rel(
                src=dev_id,
                tgt=cat_id,
                rtype="TYPE_OF",
                description=f"Device '{dev_name}' type/category '{device_category}'",
                source_id=f"iotsens:{sensor.id_sensor}",
                keywords=_keywords("type_of", device_category or ""),
            )
        )

    # medidas
    for mid in mag_entities_ids:
        mname = mid.split(":", 1)[1].replace("_", " ")
        relationships.append(
            _rel(
                src=dev_id,
                tgt=mid,
                rtype="MEASURES",
                description=f"Device '{dev_name}' measures '{mname}'",
                source_id=f"iotsens:{sensor.id_sensor}",
                keywords=_keywords("measures", mname, sensor.categoria or "", dev_type),
            )
        )

    # propiedades
    for pid in prop_ids:
        relationships.append(
            _rel(
                src=dev_id,
                tgt=pid,
                rtype="HAS_PROPERTY",
                description=f"Device '{dev_name}' has property '{pid.split(':',1)[1]}'",
                source_id=f"iotsens:{sensor.id_sensor}",
                keywords=_keywords("has_property"),
            )
        )

    # --------- CHUNKS ---------
    chunks: List[Dict[str, Any]] = [
        {
            "id": _sanitize_scalar(f"chunk:iotsens:{_slug(sensor.id_sensor)}"),
            "content": _sanitize_scalar(
                f"Device '{dev_name}' (type '{dev_type}') at '{site_name}'. "
                f"Lat={lat_s} Lon={lon_s}. "
                f"Category={device_category or 'N/A'}. "
                f"Magnitudes: {', '.join([m.nombre for m in magnitudes]) or 'N/A'}. "
                f"Properties: {', '.join([f'{k}={v}' for k, v in properties.items()]) or 'N/A'}."
            ),
            "source_id": _sanitize_scalar(f"iotsens:{sensor.id_sensor}"),
            "file_path": _sanitize_scalar(""),
            "keywords": _sanitize_scalar(
                _keywords(dev_name, dev_type, site_name, device_category or "", *[m.nombre for m in magnitudes])
            ),
        }
    ]

    return {
        "entities": entities,
        "relationships": relationships,
        "chunks": chunks,
    }
