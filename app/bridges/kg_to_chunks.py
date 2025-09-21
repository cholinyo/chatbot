# app/bridges/kg_to_chunks.py
from datetime import datetime
from sqlalchemy.orm import Session
from app.models.document import Document
from app.models.chunk import Chunk

def upsert_chunk_from_meas(session: Session, *, sensor_id: str, site_id: str, lat: float, lon: float,
                           magnitud: str, valor: float, unidad: str, ts_iso: str):
    # Documento lógico por sensor (si no existe)
    doc = (session.query(Document).filter_by(external_id=f"iotsens:{sensor_id}", path="smartcity/iotsens").one_or_none())
    if not doc:
        doc = Document(
            title=f"Sensor {sensor_id}",
            path="smartcity/iotsens",
            external_id=f"iotsens:{sensor_id}",
            created_at=datetime.utcnow()
        )
        session.add(doc); session.flush()

    content = (f"[{ts_iso}] Sensor {sensor_id} en {site_id} ({lat},{lon}) "
               f"→ {magnitud}={valor} {unidad}")
    ch = Chunk(document_id=doc.id, text=content, meta={
        "site_id": site_id, "lat": lat, "lon": lon, "magnitud": magnitud, "unidad": unidad, "ts": ts_iso
    })
    session.add(ch)
