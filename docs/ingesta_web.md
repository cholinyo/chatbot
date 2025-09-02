# Guía de Ingesta Web

## Estrategias
- **requests**: HTML estático; rápido y simple.
- **selenium**: HTML dinámico; requiere driver.
- **sitemap**: exhaustive crawl basado en sitemaps.

## Flags relevantes
- Comunes: `--seed`, `--strategy`, `--source-id`, `--run-id`, `--allowed-domains`, `--max-pages`, `--timeout`, `--robots-policy`, `--force-https`, `--include`, `--exclude`.
- Selenium: `--no-headless`, `--render-wait-ms`, `--scroll`, `--scroll-steps`, `--wait-selector`, `--iframe-max`.

## Artefactos
- `stdout.txt`, `fetch_index.json`, `raw/*.html`, `summary.json`.

## Validación
- `python tests/verify_ingestion_sqlite.py --db data/processed/tracking.sqlite --run-id <id>`

## Problemas frecuentes
- Flags no reconocidos → borrar `__pycache__`/ver ruta del módulo.
- Páginas sin texto → ajustar `--wait-selector` y `--iframe-max`.
- PDFs con texto corrupto → usar **ingest_documents**.
