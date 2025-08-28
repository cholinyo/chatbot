#!/usr/bin/env python
"""
Arranca el servidor Flask usando la application factory.

Ejemplo:
  python scripts/run_server.py --host 127.0.0.1 --port 5000 --debug

Luego prueba:
  http://127.0.0.1:5000/status/ping
  POST http://127.0.0.1:5000/ingestion/sources
  POST http://127.0.0.1:5000/ingestion/run/<source_id>
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path


def main() -> int:
    ap = argparse.ArgumentParser(description="Run Flask dev server")
    ap.add_argument("--project-root", default=".")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=5000)
    ap.add_argument("--debug", action="store_true")
    args = ap.parse_args()

    root = Path(args.project_root).resolve()
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))
    os.chdir(root)

    from app import create_app

    app = create_app()
    app.run(host=args.host, port=args.port, debug=args.debug)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())