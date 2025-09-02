# Decisiones de diseño

## D1. Separación de ingesta web vs documentos
**Motivo**: calidad del texto, control de binarios, artefactos diferenciados.

## D2. Filtros de binarios en web (Content-Type + extensión)
**Motivo**: evitar texto corrupto (mojibake) cuando servers devuelven PDF con `text/html`.

## D3. Fallbacks (HTTP e iframes)
**Motivo**: resiliencia ante HTTPS mal configurado y contenido embebido.

## D4. Vector store conmutable (FAISS/Chroma)
**Motivo**: comparativa y portabilidad. **Riesgos**: dependencia nativa/versions.
