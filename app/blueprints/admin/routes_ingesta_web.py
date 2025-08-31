# File: app/blueprints/admin/routes_ingesta_web.py
# -*- coding: utf-8 -*-
from __future__ import annotations

import json
import os
import re
import shlex
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional, Tuple

from flask import (
    Blueprint,
    render_template,
    request,
    redirect,
    url_for,
    flash,
    send_file,
    abort,
    current_app,
)

import app.extensions.db as db
from app.models import Source, IngestionRun

# Nota: Conservamos el mismo blueprint y url_prefix que ya usas
bp_ingesta_web = Blueprint("ingesta_web", __name__, url_prefix="/admin/ingesta-web")

# Directorio raíz de runs (igual que en tu implementación previa)
RUNS_ROOT = Path("data/processed/runs")
RUNS_ROOT.mkdir(parents=True, exist_ok=True)


def _default_config():
    """
    Defaults seguros para el formulario (sin afectar a la DB si el usuario no los cambia).
    Añadimos campos Selenium que la plantilla ya esperaba.
    """
    return {
        "strategy": "sitemap",
        "depth": 1,
        "allowed_domains": [],
        "include": [],
        "exclude": [],
        "robots_policy": "strict",
        "ignore_robots_for": [],
        "rate_per_host": 1.0,
        "timeout": 15,
        "force_https": True,
        "user_agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36",
        "max_pages": 100,
        # ---- Defaults que la plantilla ya usa ----
        "driver": "chrome",
        "window_size": "1366,900",
        "render_wait_ms": 3000,
        "wait_selector": "",
        "no_headless": False,
        "scroll": False,
        "scroll_steps": 4,
        "scroll_wait_ms": 500,
    }


@bp_ingesta_web.route("/", methods=["GET"])
def index():
    with db.get_session() as s:
        sources = (
            s.query(Source)
            .filter(Source.type == "web")
            .order_by(Source.id.desc())
            .all()
        )
        runs = s.query(IngestionRun).order_by(IngestionRun.id.desc()).limit(20).all()

    # routes_ingesta_web.py -> index()
    return render_template(
        "admin/ingesta_web.html",
        sources=sources,
        runs=runs,
        cfg_defaults=_default_config(),
    )



@bp_ingesta_web.route("/save", methods=["POST"])
def save():
    logger = _get_ingestion_logger() if "_get_ingestion_logger" in globals() else None

    src_id = request.form.get("id")
    url = (request.form.get("url") or "").strip()
    name = (request.form.get("name") or "").strip()
    if not url:
        flash("La URL es obligatoria", "danger")
        return redirect(url_for("ingesta_web.index"))

    # Construir config desde el form
    cfg = _default_config()
    cfg["strategy"] = request.form.get("strategy") or "sitemap"
    cfg["depth"] = int(request.form.get("depth") or 1)
    cfg["allowed_domains"] = [
        d.strip() for d in (request.form.get("allowed_domains") or "").split(",") if d.strip()
    ]
    cfg["include"] = [s.strip() for s in (request.form.get("include") or "").splitlines() if s.strip()]
    cfg["exclude"] = [s.strip() for s in (request.form.get("exclude") or "").splitlines() if s.strip()]
    cfg["robots_policy"] = request.form.get("robots_policy") or "strict"
    cfg["ignore_robots_for"] = [
        d.strip() for d in (request.form.get("ignore_robots_for") or "").split(",") if d.strip()
    ]
    cfg["rate_per_host"] = float(request.form.get("rate_per_host") or 1.0)
    cfg["timeout"] = int(request.form.get("timeout") or 15)
    cfg["force_https"] = bool(request.form.get("force_https"))
    cfg["user_agent"] = request.form.get("user_agent") or _default_config()["user_agent"]
    cfg["max_pages"] = int(request.form.get("max_pages") or 100)

    # Selenium
    cfg["driver"] = request.form.get("driver") or "chrome"
    cfg["window_size"] = request.form.get("window_size") or "1366,900"
    cfg["render_wait_ms"] = int(request.form.get("render_wait_ms") or 3000)
    cfg["wait_selector"] = request.form.get("wait_selector") or ""
    cfg["no_headless"] = bool(request.form.get("no_headless"))
    cfg["scroll"] = bool(request.form.get("scroll"))
    cfg["scroll_steps"] = int(request.form.get("scroll_steps") or 4)
    cfg["scroll_wait_ms"] = int(request.form.get("scroll_wait_ms") or 500)

    # Sanity: cfg debe ser un dict serializable
    if not isinstance(cfg, dict):
        flash("Config inválido (no es dict).", "danger")
        return redirect(url_for("ingesta_web.index"))

    with db.get_session() as s:
        if src_id:
            # ---- EDITAR ----
            src = s.get(Source, int(src_id))
            if not src:
                flash("Source no encontrado.", "danger")
                return redirect(url_for("ingesta_web.index"))
            src.url = url
            src.type = "web"
            if name:
                src.name = name
            # merge de configuración
            src.config = {**(src.config or {}), **cfg}
            action = "update"
        else:
            # ---- CREAR ----
            src = Source(url=url, name=(name or None), type="web", config=cfg)
            s.add(src)
            action = "create"

        s.commit()

    if logger:
        logger.info("[INGEST_WEB] source_%s id=%s url=%s name=%s", action, src.id, src.url, src.name or "")

    flash("Fuente guardada", "success")
    return redirect(url_for("ingesta_web.index"))



@bp_ingesta_web.route("/run/<int:source_id>", methods=["POST"])
def run(source_id: int):
    logger = getattr(current_app, "logger", None)

    # crear run + leer cfg y seed_url
    with db.get_session() as s:
        src = s.get(Source, source_id)
        if not src:
            flash("Source no encontrado.", "danger")
            return redirect(url_for("ingesta_web.index"))

        cfg = {**(_default_config()), **(src.config or {})}
        run = IngestionRun(source_id=src.id, status="running", meta={"web_config": cfg})
        s.add(run)
        s.commit()  # asegura run.id

        seed_url = src.url  # usar fuera de sesión

    # Directorio de fallback para este run (aunque el script no lo imprima)
    runs_web_root = (RUNS_ROOT / "web")
    runs_web_root.mkdir(parents=True, exist_ok=True)
    fallback_run_dir = runs_web_root / f"run_{run.id}"

    if logger:
        logger.info("[INGEST_WEB] start run source_id=%s run_id=%s url=%s strategy=%s max_pages=%s",
                    source_id, run.id, seed_url, cfg.get("strategy"), cfg.get("max_pages"))

    # localizar script (manteniendo tu lógica de candidatos)
    candidates = [
        Path("scripts/ingest_web.py"),
        Path("ingest_web.py"),
    ]
    script_path = next((p for p in candidates if p.exists()), None)
    if not script_path:
        _update_run_meta(
            run_id=run.id,
            status="error",
            stdout=f"[NO_SCRIPT_FOUND] Ninguno de: {', '.join(str(p) for p in candidates)}",
            extra={"cmd": "(sin comando)", "run_dir": str(fallback_run_dir)},
        )
        # Creamos el directorio y dejamos una traza en stdout.txt para UX consistente
        try:
            fallback_run_dir.mkdir(parents=True, exist_ok=True)
            (fallback_run_dir / "stdout.txt").write_text(
                "[NO_SCRIPT_FOUND] Revisa la ruta del script de ingesta web.\n", encoding="utf-8"
            )
        except Exception:
            pass
        flash("No encuentro el script de ingesta web. Revisa la ruta.", "danger")
        if logger:
            logger.error("[INGEST_WEB] run_id=%s ERROR no script found", run.id)
        return redirect(url_for("ingesta_web.index"))

    # construir comando para tu CLI actual (semillas, flags, dump-html, preview…)
    py = sys.executable
    args = [
        py,
        str(script_path),
        "--seed", seed_url,
        "--strategy", cfg.get("strategy", "sitemap"),
        "--max-pages", str(cfg.get("max_pages", 100)),
        "--timeout", str(cfg.get("timeout", 15)),
        "--rate", str(cfg.get("rate_per_host", 1.0)),
        "--user-agent", cfg.get("user_agent", _default_config()["user_agent"]),
        "--dump-html",
        "--preview",
        "--verbose",
    ]
    if cfg.get("force_https"):
        args.append("--force-https")

    policy = cfg.get("robots_policy", "strict")
    if policy == "ignore":
        args.append("--no-robots")
    else:
        args += ["--robots-policy", policy]
        if policy == "list" and cfg.get("ignore_robots_for"):
            args += ["--ignore-robots-for", ",".join(cfg["ignore_robots_for"])]

    if cfg.get("strategy") == "requests":
        args += ["--depth", str(cfg.get("depth", 1))]

    if cfg.get("allowed_domains"):
        args += ["--allowed-domains", ",".join(cfg["allowed_domains"])]

    for pat in cfg.get("include", []):
        args += ["--include", pat]
    for pat in cfg.get("exclude", []):
        args += ["--exclude", pat]

    # ejecutar con cwd en raíz del proyecto
    project_root = Path(current_app.root_path).parent
    cmd_shown = " ".join(shlex.quote(a) for a in args)

    try:
        if logger:
            logger.info("[INGEST_WEB] exec run_id=%s cmd=%s", run.id, cmd_shown)

        proc = subprocess.run(
            args,
            capture_output=True,
            text=True,
            check=False,
            cwd=str(project_root),
            env={**os.environ},
        )
        out = (proc.stdout or "") + ("\n" + proc.stderr if proc.stderr else "")

        # extraer run_dir del stdout (tu script lo emite como [RUN_DIR]…)
        run_dir = _extract_run_dir(out)
        if not run_dir:
            run_dir = str(fallback_run_dir)
        Path(run_dir).mkdir(parents=True, exist_ok=True)

        # escribir stdout.txt SIEMPRE para que haya algo descargable/visible
        try:
            (Path(run_dir) / "stdout.txt").write_text(out or "(sin salida)", encoding="utf-8")
        except Exception:
            pass

        extra = {"returncode": proc.returncode, "cmd": cmd_shown, "run_dir": run_dir}

        # Postprocesado: generar summary.json si tenemos fetch_index.json
        if Path(run_dir).exists():
            try:
                summary = _build_summary(Path(run_dir))
                if summary:
                    (Path(run_dir) / "summary.json").write_text(
                        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
                    )
                    extra["summary_totals"] = summary.get("totals", {})
                    if logger:
                        t = extra["summary_totals"]
                        logger.info("[INGEST_WEB] postprocessed run_id=%s pages=%s chunks=%s bytes=%s",
                                    run.id, t.get("pages", 0), t.get("chunks", 0), t.get("bytes", 0))
            except Exception as e:
                extra["summary_error"] = f"{type(e).__name__}: {e}"
                if logger:
                    logger.warning("[INGEST_WEB] summary_error run_id=%s %s: %s", run.id, type(e).__name__, e)

        _update_run_meta(
            run_id=run.id,
            status=("done" if proc.returncode == 0 else "error"),
            stdout=(out[-20000:] or "(sin salida del proceso)"),
            extra=extra,
        )

        if logger:
            logger.info("[INGEST_WEB] finished run_id=%s returncode=%s run_dir=%s",
                        run.id, proc.returncode, run_dir or "(none)")

        flash(
            "Ingesta finalizada con éxito." if proc.returncode == 0
            else "Ingesta finalizada con error. Revisa la salida.",
            "success" if proc.returncode == 0 else "danger",
        )
    except Exception as e:
        # En excepción, garantizamos igualmente un stdout.txt mínimo
        try:
            fallback_run_dir.mkdir(parents=True, exist_ok=True)
            (fallback_run_dir / "stdout.txt").write_text(f"[exception] {e}", encoding="utf-8")
        except Exception:
            pass

        _update_run_meta(
            run_id=run.id,
            status="error",
            stdout=f"[exception] {e}",
            extra={"cmd": cmd_shown, "run_dir": str(fallback_run_dir)},
        )
        if logger:
            logger.exception("[INGEST_WEB] exception run_id=%s: %s", run.id, e)
        flash(f"Fallo al ejecutar la ingesta: {e}", "danger")

    return redirect(url_for("ingesta_web.index"))


@bp_ingesta_web.route("/delete/<int:source_id>", methods=["POST"])
def delete(source_id: int):
    with db.get_session() as s:
        src = s.get(Source, source_id)
        if not src or src.type != "web":
            flash("Fuente no encontrada.", "warning")
            return redirect(url_for("ingesta_web.index"))
        s.delete(src)
        s.commit()
    flash("Fuente eliminada", "success")
    return redirect(url_for("ingesta_web.index"))


def _update_run_meta(run_id: int, *, status: str, stdout: str, extra: dict | None = None) -> None:
    """Actualizar meta/estado del run con trazas útiles (stdout, returncode, cmd, run_dir...)."""
    with db.get_session() as s:
        run_db = s.get(IngestionRun, run_id)
        if not run_db:
            return
        meta = run_db.meta or {}
        meta["stdout"] = stdout
        if extra:
            meta.update(extra)
        run_db.meta = meta
        run_db.status = status
        s.commit()


def _normalize_artifact_path(relpath: str) -> Path:
    """
    Normaliza un 'relpath' que puede venir:
      - relativo a RUNS_ROOT (p.ej. 'web_sitemap_.../fetch_index.json')
      - prefijado por RUNS_ROOT ('data/processed/runs/web_.../fetch_index.json')
      - incluso absoluto en Windows (con backslashes)
    y devuelve la ruta absoluta FINAL asegurando que permanece dentro de RUNS_ROOT.
    """
    base = RUNS_ROOT.resolve()
    rel = relpath.replace("\\", "/")

    p = Path(rel)
    if p.is_absolute():
        cand = p.resolve()
    else:
        rel_no_base = rel
        base_str = str(base).replace("\\", "/")
        if rel_no_base.startswith(base_str):
            rel_no_base = rel_no_base[len(base_str):].lstrip("/")
        cand = (base / rel_no_base).resolve()

    if not str(cand).startswith(str(base)):
        raise PermissionError("Ruta fuera del directorio de runs")

    return cand


@bp_ingesta_web.route("/artifact/<path:relpath>")
def artifact(relpath: str):
    try:
        file_path = _normalize_artifact_path(relpath)
    except PermissionError:
        abort(403)
    except Exception:
        flash("Ruta de artefacto inválida.", "warning")
        return redirect(url_for("ingesta_web.index"))

    if not file_path.exists() or not file_path.is_file():
        flash("Archivo no encontrado.", "warning")
        return redirect(url_for("ingesta_web.index"))

    return send_file(file_path, as_attachment=True)


# --- NUEVO: vista de preview del stdout dentro de la misma página ---

@bp_ingesta_web.route("/preview/<int:run_id>")
def preview(run_id: int):
    with db.get_session() as s:
        sources = (
            s.query(Source)
            .filter(Source.type == "web")
            .order_by(Source.id.desc())
            .all()
        )
        runs = s.query(IngestionRun).order_by(IngestionRun.id.desc()).limit(20).all()
        run_obj = s.get(IngestionRun, run_id)

    preview_text = ""
    if run_obj and run_obj.meta and "stdout" in run_obj.meta:
        preview_text = str(run_obj.meta.get("stdout") or "")
    elif run_obj and run_obj.meta and "run_dir" in run_obj.meta:
        # fallback: si existe stdout.txt en el run_dir
        stdout_path = Path(str(run_obj.meta["run_dir"])) / "stdout.txt"
        if stdout_path.exists():
            preview_text = stdout_path.read_text(encoding="utf-8", errors="ignore")

    return render_template(
        "admin/ingesta_web.html",
        sources=sources,
        runs=runs,
        cfg_defaults=_default_config(),
        preview=preview_text,
    )


# --- Helpers de postprocesado ---

def _extract_run_dir(proc_output: str) -> Optional[str]:
    """
    Tu script imprime la línea con prefijo [RUN_DIR]... dos veces por compatibilidad.
    Extraemos la primera ocurrencia.
    """
    for line in proc_output.splitlines():
        if line.startswith("[RUN_DIR]"):
            return line.replace("[RUN_DIR]", "").strip()
    return None


@dataclass
class _SummaryTotals:
    pages: int = 0
    chunks: int = 0
    bytes: int = 0


def _build_summary(run_dir: Path) -> Optional[dict]:
    """
    A partir de fetch_index.json y los HTML crudos en /raw, generamos summary.json con:
      - pages: número de páginas
      - chunks: número de chunks (split conservador por ~1200 caracteres)
      - bytes: suma de bytes de los HTML
      - pages[]: listado con url, title, status, raw, num_chunks, bytes
    Si no hay fetch_index.json o /raw, devolvemos None.
    """
    index_path = run_dir / "fetch_index.json"
    raw_dir = run_dir / "raw"
    if not index_path.exists() or not raw_dir.exists():
        return None

    try:
        index = json.loads(index_path.read_text(encoding="utf-8"))
    except Exception:
        return None

    pages = []
    totals = _SummaryTotals()

    for rec in index:
        raw_path = rec.get("raw")
        url = rec.get("url")
        title = rec.get("title")
        status = rec.get("status")
        b = 0
        n_chunks = 0

        if raw_path and os.path.exists(raw_path):
            try:
                b = os.path.getsize(raw_path)
                html = Path(raw_path).read_text(encoding="utf-8", errors="ignore")
                # chunking muy simple por longitud (~1200 chars) para terna de métricas
                n_chunks = _count_chunks_simple(html, max_chars=1200)
            except Exception:
                pass

        pages.append(
            {
                "url": url,
                "title": title,
                "status": status,
                "raw": raw_path,
                "bytes": b,
                "num_chunks": n_chunks,
            }
        )
        totals.pages += 1
        totals.chunks += n_chunks
        totals.bytes += int(b or 0)

    summary = {
        "kind": "web",
        "run_dir": str(run_dir),
        "totals": {"pages": totals.pages, "chunks": totals.chunks, "bytes": totals.bytes},
        "pages": pages,
    }
    return summary


def _count_chunks_simple(html: str, max_chars: int = 1200) -> int:
    """
    Contamos chunks aproximando texto con un limpiado básico y split por longitud.
    No introduce dependencias nuevas; es suficiente para métricas y UI.
    """
    # quitar scripts/styles muy básico (sin BS4)
    html = re.sub(r"<(script|style)[\s\S]*?</\1>", " ", html, flags=re.IGNORECASE)
    # suprimir etiquetas
    text = re.sub(r"<[^>]+>", " ", html)
    # normalizar espacios
    text = re.sub(r"\s+", " ", text).strip()
    if not text:
        return 0
    n = len(text)
    return (n + max_chars - 1) // max_chars
