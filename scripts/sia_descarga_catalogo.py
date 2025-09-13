"""Cliente genérico para descargar catálogos (u otras operaciones) desde un WSDL SOAP (Axis2/SIA).
Requisitos:
  pip install zeep requests

Uso rápido:
  # 1) Ver operaciones y firmas
  python sia_descarga_catalogo.py --wsdl https://pre-sia2.gestion400.local/axis2/services/wsSIAConsultarCatalogos?wsdl --list

  # 2) Llamar a una operación (p.ej. ConsultarCatalogos) con parámetros
  python sia_descarga_catalogo.py --wsdl https://pre-sia2.gestion400.local/axis2/services/wsSIAConsultarCatalogos?wsdl \
      --op ConsultarCatalogos --params '{"tipoCatalogo":"CANAL","pagina":1,"tamanoPagina":500}' \
      --out catalogo_canal.json --fmt json

  # 3) Exportar a CSV (si la respuesta contiene una lista de registros)
  python sia_descarga_catalogo.py --wsdl https://pre-sia2.gestion400.local/axis2/services/wsSIAConsultarCatalogos?wsdl \
      --op ConsultarCatalogos --params params.json --out catalogo_canal.csv --fmt csv

Notas:
- Si el endpoint real difiere del 'location' del WSDL, usa --endpoint para sobrescribirlo.
- Si hay autenticación BASIC, usa --user y --password.
- Si el servidor usa una CA corporativa:
    * Pasa --ca /ruta/CA.pem
  En pruebas, puedes desactivar la verificación TLS con --insecure (no recomendado).
"""
import argparse
import json
import sys
import csv
from typing import Any, Dict, List, Tuple, Optional, Iterable

import requests
from requests.auth import HTTPBasicAuth
from zeep import Client, Settings
from zeep.transports import Transport
from zeep.helpers import serialize_object


def build_client(wsdl: str,
                 endpoint_override: Optional[str],
                 user: Optional[str],
                 password: Optional[str],
                 ca_path: Optional[str],
                 insecure: bool,
                 timeout: int = 30):
    session = requests.Session()
    if user and password:
        session.auth = HTTPBasicAuth(user, password)
    if ca_path:
        session.verify = ca_path
    else:
        session.verify = not insecure  # True por defecto; False si --insecure
    transport = Transport(session=session, timeout=timeout)
    settings = Settings(strict=False, xml_huge_tree=True)
    client = Client(wsdl, transport=transport, settings=settings)

    if endpoint_override:
        # Usamos el primer servicio/puerto por defecto; si tienes varios, ajusta aquí.
        service = next(iter(client.wsdl.services.values()))
        port = next(iter(service.ports.values()))
        binding_qname = port.binding.qname
        svc = client.create_service(binding_qname, endpoint_override)
    else:
        # Servicio por defecto
        service = next(iter(client.wsdl.services.values()))
        port = next(iter(service.ports.values()))
        binding_qname = port.binding.qname
        # location del WSDL
        endpoint = port.binding.options.get('address')
        svc = client.create_service(binding_qname, endpoint)
    return client, svc


def list_operations(client: Client) -> List[Tuple[str, str, str]]:
    """
    Devuelve [(service, port, operation_signature_string), ...]
    """
    out = []
    for s_name, service in client.wsdl.services.items():
        for p_name, port in service.ports.items():
            binding = port.binding
            for op_name, op in binding._operations.items():
                try:
                    sig = op.input.signature()
                except Exception as e:
                    sig = f"{op_name}(...)  # firma no disponible: {e}"
                out.append((str(s_name), str(p_name), sig))
    return out


def call_operation(service_proxy, op_name: str, params: Dict[str, Any]):
    """
    Llama a la operación por nombre con kwargs.
    """
    if not hasattr(service_proxy, op_name):
        raise AttributeError(f"La operación '{op_name}' no existe en este servicio.")
    func = getattr(service_proxy, op_name)
    return func(**params)


def find_first_records_list(obj: Any) -> Optional[List[Dict[str, Any]]]:
    """
    Busca la primera lista de diccionarios en la respuesta serializada.
    Útil para exportar a CSV sin conocer el esquema exacto.
    """
    # Caso lista directa
    if isinstance(obj, list) and obj and isinstance(obj[0], dict):
        return obj

    # Recorrido DFS sobre dicts/listas
    stack = [obj]
    while stack:
        cur = stack.pop()
        if isinstance(cur, list):
            if cur and isinstance(cur[0], dict):
                return cur
            stack.extend(cur)
        elif isinstance(cur, dict):
            stack.extend(cur.values())
    return None


def write_json(path: str, data: Any):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def write_csv(path: str, rows: List[Dict[str, Any]]):
    # Recoger todos los campos posibles
    fieldnames = set()
    for r in rows:
        fieldnames.update(r.keys())
    field_order = sorted(fieldnames)
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=field_order)
        w.writeheader()
        for r in rows:
            w.writerow(r)


def main():
    parser = argparse.ArgumentParser(description="Descargar catálogos SIA/Axis2 vía SOAP (WSDL).")
    parser.add_argument("--wsdl", required=True, help="URL del WSDL (ej.: https://.../wsSIAConsultarCatalogos?wsdl)")
    parser.add_argument("--endpoint", help="Sobrescribe el endpoint real (si difiere del WSDL).")
    parser.add_argument("--user", help="Usuario BASIC (si aplica).")
    parser.add_argument("--password", help="Password BASIC (si aplica).")
    parser.add_argument("--ca", help="Ruta a CA corporativa (PEM) para validar TLS.")
    parser.add_argument("--insecure", action="store_true", help="Desactiva verificación TLS (solo pruebas).")
    parser.add_argument("--timeout", type=int, default=30, help="Timeout en segundos (por defecto 30).")

    sub = parser.add_mutually_exclusive_group(required=True)
    sub.add_argument("--list", action="store_true", help="Lista operaciones y firmas.")
    sub.add_argument("--op", help="Nombre de la operación a invocar (ej.: ConsultarCatalogos).")

    parser.add_argument("--params", help="Parámetros en JSON (string o ruta a .json).")
    parser.add_argument("--out", help="Ruta de salida (json/csv).")
    parser.add_argument("--fmt", choices=["json", "csv"], default="json", help="Formato de salida.")

    args = parser.parse_args()

    # Cargar params (si procede)
    params: Dict[str, Any] = {}
    if args.params:
        try:
            # ¿Es una ruta a fichero?
            if args.params.endswith(".json"):
                with open(args.params, "r", encoding="utf-8") as f:
                    params = json.load(f)
            else:
                params = json.loads(args.params)
        except Exception as e:
            print(f"ERROR al leer --params: {e}", file=sys.stderr)
            sys.exit(2)

    client, svc = build_client(
        wsdl=args.wsdl,
        endpoint_override=args.endpoint,
        user=args.user,
        password=args.password,
        ca_path=args.ca,
        insecure=args.insecure,
        timeout=args.timeout,
    )

    if args.list:
        ops = list_operations(client)
        if not ops:
            print("No se han encontrado operaciones en el WSDL.")
            return
        print("Operaciones disponibles:\n")
        cur_s, cur_p = None, None
        for s_name, p_name, sig in ops:
            if (s_name, p_name) != (cur_s, cur_p):
                print(f"Service: {s_name}  |  Port: {p_name}")
                cur_s, cur_p = s_name, p_name
            print(f"  - {sig}")
        return

    # Llamada a la operación solicitada
    try:
        resp = call_operation(svc, args.op, params)
    except Exception as e:
        print(f"ERROR al invocar '{args.op}': {e}", file=sys.stderr)
        sys.exit(3)

    # Serializar a tipos de Python
    data = serialize_object(resp)

    if not args.out:
        # Si no hay salida, mostramos JSON por stdout (truncado si es enorme)
        try:
            print(json.dumps(data, ensure_ascii=False, indent=2)[:2000])
            print("... (salida truncada; usa --out para guardar completa)")
        except Exception:
            print(str(data)[:2000])
        return

    # Guardar según formato
    if args.fmt == "json":
        write_json(args.out, data)
        print(f"[OK] Guardado JSON en: {args.out}")
    else:
        rows = find_first_records_list(data)
        if not rows:
            print("No se ha encontrado automáticamente una lista de registros en la respuesta.", file=sys.stderr)
            print("Guarda en JSON (--fmt json) y revisa la estructura para mapear los campos a CSV.", file=sys.stderr)
            sys.exit(4)
        write_csv(args.out, rows)
        print(f"[OK] Guardado CSV en: {args.out}")


if __name__ == "__main__":
    main()
