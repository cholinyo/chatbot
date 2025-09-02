# Despliegue

## Entornos
- Dev: Flask dev server + SQLite.
- Prod: WSGI (gunicorn/uwsgi) + Nginx/Apache; BD opcional (PostgreSQL).

## Variables de entorno
- APP_ENV, DATABASE_URL, LOG_CONFIG, SETTINGS_TOML.

## Operativa
- Backups de `tracking.sqlite`.
- Rotación/limpieza de `data/processed/runs/*`.
