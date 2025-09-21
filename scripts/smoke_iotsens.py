import os, json
from app.integrations.iotsens_client import IoTsensClient

def main():
    cli = IoTsensClient()
    print("YAML cargado desde:", os.getenv("IOTSENS_ENDPOINTS_LOADED_FROM"))
    devs = cli.list_devices(page_size=5)
    print(f"Dispositivos (primeros 5): {len(devs)}")

    if not devs:
        raw = cli.list_devices_first_page_raw(page_size=5)
        if isinstance(raw, dict):
            keys = list(raw.keys())
            print("Top-level keys:", keys)
            # Muestra posibles contenedores y su longitud
            for k in ("items","data","results","content","page","result"):
                v = raw.get(k) if isinstance(raw, dict) else None
                if isinstance(v, list):
                    print(f"- {k}: list con {len(v)} elementos")
                elif isinstance(v, dict):
                    inner_keys = list(v.keys())
                    print(f"- {k}: dict con keys {inner_keys}")
                    for kk in ("items","content","results"):
                        if kk in v and isinstance(v[kk], list):
                            print(f"  · {k}.{kk} → {len(v[kk])} elementos")
        else:
            print("Respuesta no es dict, tipo:", type(raw).__name__)

if __name__ == "__main__":
    main()
