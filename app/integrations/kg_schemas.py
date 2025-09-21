from __future__ import annotations
from dataclasses import dataclass
from typing import Optional, List

@dataclass
class Ubicacion:
    id_site: str
    lat: Optional[float] = None
    lon: Optional[float] = None
    direccion: Optional[str] = None

@dataclass
class Sensor:
    id_sensor: str
    tipo: str
    unidad: str = ""
    site_id: Optional[str] = None
    alias: Optional[str] = None
    serial: Optional[str] = None
    categoria: Optional[str] = None

@dataclass
class Magnitud:
    nombre: str
