# Gestión de datos

## Estructura de `data/`
- `raw/`: ficheros originales.
- `processed/`: BD y `runs/` (artefactos).

## Esquema SQLite
- Source, IngestionRun, Document, Chunk.

## Limpieza y backups
- Copias de `tracking.sqlite` antes de operaciones masivas.
- Deduplicación futura: índices únicos o tareas de mantenimiento.
