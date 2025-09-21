from __future__ import annotations
import os, time, copy
from dataclasses import dataclass
from typing import Iterator, Any, Dict, List, Optional
from pathlib import Path
import httpx, yaml
from dotenv import load_dotenv

load_dotenv()

# Defaults sensatos si el YAML no existe o está incompleto
DEFAULT_ENDPOINTS: Dict[str, Any] = {
    "base_path": "",
    "devices_org": "/v1/catalog/tenants/{tenantUuid}/organizations/{organizationUuid}/devices",
    "devices_tenant": "/v1/catalog/tenants/{tenantUuid}/devices",
    "device_details_org": "/v1/catalog/tenants/{tenantUuid}/organizations/{organizationUuid}/devices/{deviceUuid}",
    "device_details_tenant": "/v1/catalog/tenants/{tenantUuid}/devices/{deviceUuid}",
    "assets_org": "/v1/catalog/tenants/{tenantUuid}/organizations/{organizationUuid}/assets",
    "assets_tenant": "/v1/catalog/tenants/{tenantUuid}/assets",
    "asset_details_org": "/v1/catalog/tenants/{tenantUuid}/organizations/{organizationUuid}/assets/{assetUuid}",
    "asset_details_tenant": "/v1/catalog/tenants/{tenantUuid}/assets/{assetUuid}",
    # Timeseries a confirmar:
    "measurements_by_device_org": None,
    "measurements_by_device_tenant": None,
    "auth": {
        "mode": "headers",
        "api_key_header": "Authorization",
        "api_key_prefix": "ApiKey ",
        "tenant_header": "X-Tenant-UUID",
        "org_header": "X-Organization-UUID",
        "tenant_param": "tenantUuid",
        "org_param": "organizationUuid",
    },
    "pagination": {
        "scheme": "page_size",
        "page_param": "page",
        "size_param": "size",
        "index_base": 0,   # <<<<<< POR DEFECTO: Spring usa 0
        "offset_param": "offset",
        "limit_param": "limit",
    },
    "time_filters": {
        "from_param": "from",
        "to_param": "to",
        "format": "iso8601",
    },
    "mappings": {
        "device": {
            "id": ["uuid","deviceUuid","id","guid","device_id"],
            "serial": ["serialNumber","serial","sn"],
            "alias": ["alias","name"],
            "category": ["deviceCategory.name","category.name","category"],
            "current_asset_uuid": ["currentAsset.uuid","assetUuid"],
        },
        "asset": {
            "id": ["uuid","id","guid"],
            "alias": ["alias","name"],
            "lat": ["latitude","lat"],
            "lon": ["longitude","lon"],
            "timezone": ["timezone"],
        },
        "measure": {
            "ts": ["timestamp","ts","date","time"],
            "value": ["value","val"],
            "magnitude": ["magnitude","type","name"],
            "quality": ["quality","q","status"],
        },
        "list_container_keys": ["items","data","results","content"],
        "list_container_paths": ["data.items","data.content","result.items","page.content"],
    },
}

def _deep_update(dst: dict, src: dict) -> dict:
    for k, v in (src or {}).items():
        if isinstance(v, dict) and isinstance(dst.get(k), dict):
            _deep_update(dst[k], v)
        else:
            dst[k] = v
    return dst

def _load_yaml_or_default(endpoints_yaml: str | None):
    env_path = os.getenv("IOTSENS_ENDPOINTS_YAML")
    candidates: List[Path] = []
    if endpoints_yaml:
        candidates.append(Path(endpoints_yaml))
    if env_path:
        candidates.append(Path(env_path))
    candidates.append(Path("config") / "iotsens_endpoints.yaml")
    candidates.append(Path(__file__).resolve().parents[2] / "config" / "iotsens_endpoints.yaml")

    cfg = copy.deepcopy(DEFAULT_ENDPOINTS)
    for p in candidates:
        if p and p.exists():
            with open(p, "r", encoding="utf-8") as f:
                y = yaml.safe_load(f) or {}
                if not isinstance(y, dict):
                    y = {}
                _deep_update(cfg, y)   # merge con defaults
                os.environ["IOTSENS_ENDPOINTS_LOADED_FROM"] = str(p)
                return cfg
    os.environ["IOTSENS_ENDPOINTS_LOADED_FROM"] = "(defaults)"
    return cfg

def _get_nested(d: dict, dotted_keys: List[str]) -> Any:
    for dk in dotted_keys:
        cur = d
        ok = True
        for part in dk.split("."):
            if isinstance(cur, dict) and part in cur:
                cur = cur[part]
            else:
                ok = False
                break
        if ok:
            return cur
    return None

def _pluck_by_path(d: dict, path: str) -> Any:
    cur = d
    for part in path.split("."):
        if isinstance(cur, dict) and part in cur:
            cur = cur[part]
        else:
            return None
    return cur

@dataclass
class IoTsensDevice:
    id: str
    alias: Optional[str] = None
    category: Optional[str] = None
    site_id: Optional[str] = None
    lat: Optional[float] = None
    lon: Optional[float] = None
    serial: Optional[str] = None

@dataclass
class IoTsensMeasure:
    device_id: str
    magnitude: str
    ts_iso: str
    value: float
    quality: str | None = None

class IoTsensClient:
    def __init__(self, endpoints_yaml: str | None = "config/iotsens_endpoints.yaml", *, force_scope: Optional[str] = None):
        self.base = (os.getenv("IOTSENS_BASE_URL", "")).rstrip("/")
        self.key = os.getenv("IOTSENS_API_KEY", "")
        self.tenant_uuid = os.getenv("IOTSENS_TENANT_UUID", "")
        self.org_uuid = os.getenv("IOTSENS_ORG_UUID", "")
        self.timeout = int(os.getenv("IOTSENS_TIMEOUT", 20))
        self._force_scope = force_scope  # "tenant" | "org" | None

        if not self.base:
            raise RuntimeError("IOTSENS_BASE_URL no configurada en .env")
        if not self.key:
            raise RuntimeError("IOTSENS_API_KEY no configurada en .env")
        if not self.tenant_uuid:
            raise RuntimeError("IOTSENS_TENANT_UUID no configurada en .env")

        self.eps: Dict[str, Any] = _load_yaml_or_default(endpoints_yaml)
        self.base_path = (self.eps.get("base_path") or "").rstrip("/")

        # Query auth (si el API lo exigiera)
        self._auth_query: Dict[str, str] = {}
        if self.eps["auth"].get("mode") == "query":
            self._auth_query[self.eps["auth"].get("tenant_param","tenantUuid")] = self.tenant_uuid
            if self.org_uuid and self._force_scope != "tenant":
                self._auth_query[self.eps["auth"].get("org_param","organizationUuid")] = self.org_uuid

        # Headers base sin la API key (se añade con _make_client)
        self._base_headers = {"Accept": "application/json"}
        if self.eps["auth"].get("mode") == "headers":
            tenant_header = self.eps["auth"].get("tenant_header", "X-Tenant-UUID")
            org_header = self.eps["auth"].get("org_header", "X-Organization-UUID")
            if self.tenant_uuid:
                self._base_headers[tenant_header] = self.tenant_uuid
            if self.org_uuid and self._force_scope != "tenant":
                self._base_headers[org_header] = self.org_uuid

        # Combo principal desde YAML + fallbacks
        self._auth_combo = (
            self.eps["auth"].get("api_key_header", "Authorization"),
            self.eps["auth"].get("api_key_prefix", "ApiKey "),
        )
        self._fallback_combos = [
            ("Authorization", "Bearer "),
            ("Authorization", ""),
            ("X-API-KEY", ""),
            ("X-Api-Key", ""),
            ("apikey", ""),
        ]
        self._client = self._make_client(*self._auth_combo)

    # ---------- infra ----------
    def _make_client(self, header_name: str, prefix: str) -> httpx.Client:
        h = dict(self._base_headers)
        h[header_name] = f"{prefix}{self.key}"
        return httpx.Client(timeout=self.timeout, headers=h)

    def _do_get(self, url: str, params: dict) -> httpx.Response:
        combos = [self._auth_combo] + [c for c in self._fallback_combos if c != self._auth_combo]
        last_resp: httpx.Response | None = None
        for (hdr, pfx) in combos:
            client = self._make_client(hdr, pfx)
            self._debug(f"GET {url} (auth='{hdr}' prefix='{pfx or '∅'}') params={params}")
            resp = client.get(url, params=params)
            if resp.status_code != 401:
                self._client = client
                self._auth_combo = (hdr, pfx)
                self._debug(f"Auth OK con header '{hdr}' prefix='{pfx or '∅'}' — cacheado")
                return resp
            else:
                self._debug(f"401 con header '{hdr}' prefix='{pfx or '∅'}'; siguiente…")
                last_resp = resp
                time.sleep(0.05)
        if last_resp is not None:
            last_resp.raise_for_status()
        raise httpx.HTTPStatusError("Fallo de autenticación (sin respuesta)", request=None, response=None)

    def _url(self, path: str) -> str:
        path = f"{self.base_path}/{path.lstrip('/')}" if self.base_path else path
        return f"{self.base}/{path.lstrip('/')}"

    def _format(self, template: str, **kw) -> str:
        return template.format(
            tenantUuid=self.tenant_uuid,
            organizationUuid=self.org_uuid,
            deviceUuid=kw.get("deviceUuid",""),
            assetUuid=kw.get("assetUuid",""),
        )

    def _extract_list(self, data: Any) -> list:
        if isinstance(data, list):
            return data
        if isinstance(data, dict):
            maps = self.eps.get("mappings", {})
            keys = maps.get("list_container_keys", ["items"])
            paths = maps.get("list_container_paths", [])
            # 1) keys directas
            for k in keys:
                if k in data:
                    v = data[k]
                    if isinstance(v, list):
                        return v
                    if isinstance(v, dict):
                        for k2 in keys:
                            if k2 in v and isinstance(v[k2], list):
                                return v[k2]
            # 2) rutas tipo data.content / result.items
            for p in paths:
                v = _pluck_by_path(data, p)
                if isinstance(v, list):
                    return v
            # 3) heurística
            for v in data.values():
                if isinstance(v, list) and v and isinstance(v[0], dict) and any(kk in v[0] for kk in ("uuid","id")):
                    return v
                if isinstance(v, dict):
                    sub = self._extract_list(v)
                    if sub:
                        return sub
        return []

    def _debug(self, msg: str):
        if os.getenv("IOTSENS_LOG","").lower() in ("1","true","debug"):
            print(f"[IoTsensClient] {msg}")

    # ---------- helpers de scope ----------
    def _resolve_use_org(self) -> bool:
        if self._force_scope == "tenant":
            return False
        if self._force_scope == "org":
            return True
        return bool(self.org_uuid)

    # ---------- catálogo ----------
    def _list_devices(self, use_org: bool, page_size: int) -> list[IoTsensDevice]:
        path_tmpl = self.eps["devices_org"] if use_org else self.eps["devices_tenant"]
        path = self._format(path_tmpl)
        url = self._url(path)

        pag = self.eps["pagination"]
        scheme = pag.get("scheme","page_size")
        index_base = int(pag.get("index_base", 0)) if scheme == "page_size" else 0

        page, offset = index_base, 0
        results: list[IoTsensDevice] = []

        while True:
            params: Dict[str, Any] = dict(self._auth_query)
            if scheme == "page_size":
                params[pag.get("page_param","page")] = page
                params[pag.get("size_param","size")] = page_size
            else:
                params[pag.get("offset_param","offset")] = offset
                params[pag.get("limit_param","limit")] = page_size

            r = self._do_get(url, params)
            if r.status_code == 404:
                self._debug(f"404 en {url}")
                break
            r.raise_for_status()

            data = r.json()
            items = self._extract_list(data)
            if items is None:
                items = []
            if not isinstance(items, list):
                self._debug(f"Respuesta sin lista; type={type(items)}")
                break

            # Agrega resultados
            for it in items:
                id_ = _get_nested(it, self.eps["mappings"]["device"]["id"])
                serial = _get_nested(it, self.eps["mappings"]["device"]["serial"])
                alias = _get_nested(it, self.eps["mappings"]["device"]["alias"])
                category = _get_nested(it, self.eps["mappings"]["device"]["category"])
                site_uuid = _get_nested(it, self.eps["mappings"]["device"]["current_asset_uuid"])
                lat = _get_nested(it, ["currentAsset.latitude","latitude"])
                lon = _get_nested(it, ["currentAsset.longitude","longitude"])

                # Fallback de ID
                id_final = (str(id_) if id_ else None) or (str(serial) if serial else None) or (str(alias) if alias else None) or ""
                results.append(IoTsensDevice(
                    id=id_final, alias=alias, serial=serial, category=category,
                    site_id=(str(site_uuid) if site_uuid else None),
                    lat=lat, lon=lon
                ))

            # Corte por última página
            if len(items) < page_size:
                break

            # Avanza
            if scheme == "page_size":
                page += 1
            else:
                offset += page_size
            time.sleep(0.05)

        return results

    def list_devices(self, page_size: int = 100) -> list[IoTsensDevice]:
        return self._list_devices(self._resolve_use_org(), page_size)

    def list_devices_auto(self, page_size: int = 100) -> list[IoTsensDevice]:
        primary_use_org = self._resolve_use_org()
        res = self._list_devices(primary_use_org, page_size)
        if res:
            return res
        self._debug(f"Scope primario ({'org' if primary_use_org else 'tenant'}) vacío; probando alternativo…")
        return self._list_devices(not primary_use_org, page_size)

    def list_devices_first_page_raw(self, page_size: int = 5) -> Any:
        use_org = self._resolve_use_org()
        path_tmpl = self.eps["devices_org"] if use_org else self.eps["devices_tenant"]
        path = self._format(path_tmpl)
        url = self._url(path)

        pag = self.eps["pagination"]
        scheme = pag.get("scheme","page_size")
        index_base = int(pag.get("index_base", 0)) if scheme == "page_size" else 0

        params: Dict[str, Any] = dict(self._auth_query)
        if scheme == "page_size":
            params[pag.get("page_param","page")] = index_base
            params[pag.get("size_param","size")] = page_size
        else:
            params[pag.get("offset_param","offset")] = 0
            params[pag.get("limit_param","limit")] = page_size

        r = self._do_get(url, params)
        r.raise_for_status()
        return r.json()

    def device_details(self, device_uuid: str) -> dict:
        use_org = self._resolve_use_org()
        tmpl = self.eps["device_details_org"] if use_org else self.eps["device_details_tenant"]
        path = self._format(tmpl, deviceUuid=device_uuid)
        url = self._url(path)
        r = self._do_get(url, self._auth_query)
        r.raise_for_status()
        return r.json()

    def _list_assets_map(self, use_org: bool, page_size: int) -> dict[str, dict]:
        tmpl = self.eps["assets_org"] if use_org else self.eps["assets_tenant"]
        path = self._format(tmpl)
        url = self._url(path)

        pag = self.eps["pagination"]
        scheme = pag.get("scheme","page_size")
        index_base = int(pag.get("index_base", 0)) if scheme == "page_size" else 0

        page, offset = index_base, 0
        out: dict[str, dict] = {}

        while True:
            params: Dict[str, Any] = dict(self._auth_query)
            if scheme == "page_size":
                params[pag.get("page_param","page")] = page
                params[pag.get("size_param","size")] = page_size
            else:
                params[pag.get("offset_param","offset")] = offset
                params[pag.get("limit_param","limit")] = page_size

            r = self._do_get(url, params)
            if r.status_code == 404:
                self._debug(f"404 en {url}")
                break
            r.raise_for_status()

            data = r.json()
            items = self._extract_list(data)
            if items is None:
                items = []
            if not isinstance(items, list):
                self._debug(f"Respuesta assets sin lista; type={type(items)}")
                break

            for it in items:
                aid = _get_nested(it, self.eps["mappings"]["asset"]["id"])
                if not aid:
                    continue
                out[str(aid)] = {
                    "alias": _get_nested(it, self.eps["mappings"]["asset"]["alias"]),
                    "lat": _get_nested(it, self.eps["mappings"]["asset"]["lat"]),
                    "lon": _get_nested(it, self.eps["mappings"]["asset"]["lon"]),
                    "timezone": _get_nested(it, self.eps["mappings"]["asset"]["timezone"]),
                }

            if len(items) < page_size:
                break

            if scheme == "page_size":
                page += 1
            else:
                offset += page_size
            time.sleep(0.05)

        return out

    def list_assets_map(self, page_size: int = 200) -> dict[str, dict]:
        return self._list_assets_map(self._resolve_use_org(), page_size)

    def list_assets_map_auto(self, page_size: int = 200) -> dict[str, dict]:
        primary_use_org = self._resolve_use_org()
        res = self._list_assets_map(primary_use_org, page_size)
        if res:
            return res
        self._debug(f"Scope primario assets ({'org' if primary_use_org else 'tenant'}) vacío; probando alternativo…")
        return self._list_assets_map(not primary_use_org, page_size)

    # ---------- timeseries (activar cuando tengamos los endpoints) ----------
    def measurements_by_device(self, device_id: str, ts_from: str, ts_to: str, page_size: int = 500) -> Iterator[IoTsensMeasure]:
        use_org = self._resolve_use_org()
        tmpl = (self.eps.get("measurements_by_device_org") if use_org else self.eps.get("measurements_by_device_tenant"))
        if not tmpl:
            raise NotImplementedError("Endpoint de mediciones no configurado aún en iotsens_endpoints.yaml")
        path = self._format(tmpl, deviceUuid=device_id)
        url = self._url(path)
        raise NotImplementedError("Implementar cuando confirmemos el bloque de Swagger de timeseries")
