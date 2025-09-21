# scripts/sync_iotsens_to_kg.py
from __future__ import annotations

import os
from typing import Dict, Any

from app.integrations.iotsens_client import IoTsensClient
from app.integrations.kg_schemas import Sensor, Ubicacion, Magnitud
from app.integrations.kg_adapter import build_custom_kg_device_only

# Inserta en un namespace concreto (p.ej. "smartcity") usando el registry
from app.datasources.graphs.graph_registry import insert_custom_kg as insert_custom_kg_ns

NAMESPACE = "smartcity"


def _props_from_details(det: Dict[str, Any]) -> Dict[str, str]:
    """
    Normaliza deviceProperties (y algunos campos útiles de detalle) a dict clave->valor (str).
    """
    out: Dict[str, str] = {}

    try:
        props = det.get("deviceProperties") or []
        for p in props:
            k = str(p.get("name") or p.get("key") or "").strip()
            v = p.get("value")
            if not k:
                continue
            # lo dejamos en string corto
            vs = "" if v is None else str(v)
            out[k] = vs
    except Exception:
        pass

    # detalles que suelen ser informativos (si existen)
    try:
        tz = det.get("timezone")
        if tz:
            out["timezone"] = str(tz)
    except Exception:
        pass

    # integración / fabricante / modelo, si aparece
    try:
        imods = det.get("integrationModules") or []
        # metemos el nombre del primer módulo como “integrationModule”
        if imods:
            name = imods[0].get("name") or imods[0].get("type")
            if name:
                out["integrationModule"] = str(name)
    except Exception:
        pass

    return out


def sync():
    force_scope = os.getenv("IOTSENS_FORCE_SCOPE") or None
    if force_scope:
        print(f"[sync] Force scope: {force_scope}")

    cli = IoTsensClient(force_scope=force_scope)

    # 1) Dispositivos (auto-scope: tenant/org)
    devices = cli.list_devices_auto(page_size=int(os.getenv("IOTSENS_PAGE_SIZE", "200")))
    print(f"[sync] Dispositivos recibidos (auto-scope): {len(devices)}")

    # 2) Mapa de assets (uuid -> {alias, lat, lon})
    assets = cli.list_assets_map_auto(page_size=1000)
    print(f"[sync] Assets recibidos (auto-scope): {len(assets)}")

    # 3) Recorrido y construcción de KG
    total, skipped_no_id = 0, 0
    for d in devices:
        sensor_id = d.id or d.serial or d.alias
        if not sensor_id:
            skipped_no_id += 1
            continue

        # --- Detalles del dispositivo: currentAsset + measurements + properties
        det = {}
        dm = []
        asset_uuid = None
        props: Dict[str, str] = {}
        try:
            det = cli.device_details(sensor_id) or {}
            asset = det.get("currentAsset") or {}
            asset_uuid = asset.get("uuid") or asset.get("id")
            dm = det.get("deviceMeasurements") or []
            props = _props_from_details(det)
        except Exception:
            det, dm, asset_uuid, props = {}, [], None, {}

        # --- Resolver el site_id: preferimos el uuid del currentAsset
        resolved_site_id = asset_uuid or (d.site_id or f"asset-unknown-{sensor_id}")

        # Enriquecer ubicación con datos del assets-map cuando existan
        a = assets.get(resolved_site_id, {})
        ubic = Ubicacion(
            id_site=resolved_site_id,
            lat=a.get("lat") if a else (d.lat or None),
            lon=a.get("lon") if a else (d.lon or None),
            direccion=a.get("alias") if a else None,
        )

        # Sensor completo
        sensor = Sensor(
            id_sensor=sensor_id,
            tipo=(d.category or "Device"),
            unidad="",
            site_id=ubic.id_site,
            alias=d.alias,
            serial=d.serial,
            categoria=d.category,
        )

        # Magnitudes desde deviceMeasurements
        mags: list[Magnitud] = []
        for m in dm:
            nombre = (m.get("name") or m.get("type") or m.get("magnitude"))
            if nombre:
                mags.append(Magnitud(nombre=str(nombre)))

        # Construcción del payload de KG e inserción namespaced
        custom_kg = build_custom_kg_device_only(
            sensor, ubic, mags,
            properties=props,                    # <<< NUEVO
            device_category=(d.category or None) # <<< NUEVO
        )
        insert_custom_kg_ns(NAMESPACE, custom_kg)
        total += 1

    print(
        f"[sync_iotsens_to_kg] Insertados/actualizados {total} dispositivos en el KG. "
        f"Saltados sin ID: {skipped_no_id}"
    )


if __name__ == "__main__":
    sync()
