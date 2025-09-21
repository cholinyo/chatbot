import os
from app.integrations.iotsens_client import IoTsensClient

def main():
    print("YAML cargado desde:", os.getenv("IOTSENS_ENDPOINTS_LOADED_FROM"))
    cli = IoTsensClient(force_scope="tenant")
    devs = cli.list_devices(page_size=5)
    print(f"[TENANT] Dispositivos (primeros 5): {len(devs)}")
    if devs:
        for d in devs[:5]:
            print(" -", d)

if __name__ == "__main__":
    main()
