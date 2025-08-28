# app/models/__init__.py
from app.extensions.db import Base  # garantiza mismo Base

# importa todos los modelos para registrar los mappers y relaciones
from .source import Source           # noqa: F401
from .document import Document       # noqa: F401
from .chunk import Chunk             # noqa: F401
from .ingestion_run import IngestionRun  # noqa: F401
