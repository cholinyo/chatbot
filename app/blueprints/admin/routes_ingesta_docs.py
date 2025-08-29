from __future__ import annotations
import shlex, subprocess, sys
from pathlib import Path
from flask import Blueprint, render_template, request, redirect, url_for, flash

import app.extensions.db as db
from app.models import Source, IngestionRun

bp = Blueprint("ingesta_docs", __name__, url_prefix="/admin/ingesta-docs")

# Carpeta “drop” por defecto
UPLOAD_DIR = Path("data/raw/uploads")
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)

# Defaults UI
UI_DEFAULTS = {
    "input_dir": str(UPLOAD_DIR),
    "patterns": "*.pdf,*.docx,*.txt,*.md,*.csv",
    "recursive": True,
    "only_new": True,
}

# Flags del script (ajústalos si tu script usa otros nombres)
FLAG_INPUTDIR  = "--input-dir"
FLAG_PATTERN   = "--pattern"     # repetible
FLAG_RECURSIVE = "--recursive"
FLAG_ONLY_NEW  = "--only-new"


@bp.route("/", methods=["GET"])
def index():
    select_id = request.args.get("select_id", type=int)
    with db.get_session() as s:
        sources = (
            s.query(Source)
             .filter(Source.type == "docs")
             .order_by(Source.id.desc())
             .all()
        )
        runs = (
            s.query(IngestionRun)
             .order_by(IngestionRun.id.desc())
             .limit(20)
             .all()
        )
    return render_template(
        "admin/ingesta_docs.html",
        sources=sources,
        runs=runs,
        upload_dir=str(UPLOAD_DIR),
        defaults=UI_DEFAULTS,
        select_id=select_id,
    )


@bp.post("/quick-save")
def quick_save():
    """
    Crea una fuente 'docs' sin salir de la página.
    Guarda también patrones, recursividad y 'sólo nuevos' en config.
    """
    name      = (request.form.get("name") or "").strip()
    input_dir = (request.form.get("input_dir") or "").strip()
    patterns  = (request.form.get("patterns") or "").strip()
    recursive = bool(request.form.get("recursive"))
    only_new  = bool(request.form.get("only_new"))

    if not input_dir:
        flash("Indica una carpeta para la fuente.", "warning")
        return redirect(url_for("ingesta_docs.index"))

    cfg = {
        "input_dir": input_dir,
        "patterns": patterns or UI_DEFAULTS["patterns"],
        "recursive": recursive,
        "only_new": only_new,
    }

    with db.get_session() as s:
        src = Source(type="docs", url=input_dir, name=name or None, config=cfg)
        s.add(src)
        s.commit()
        new_id = src.id

    flash("Fuente 'docs' creada.", "success")
    # Tras crearla, volvemos y la dejamos seleccionada
    return redirect(url_for("ingesta_docs.index", select_id=new_id))


@bp.route("/upload", methods=["POST"])
def upload():
    files = request.files.getlist("files")
    if not files:
        flash("Selecciona al menos un fichero", "warning")
        return redirect(url_for("ingesta_docs.index"))

    saved = 0
    for f in files:
        if not f or not f.filename:
            continue
        dest = UPLOAD_DIR / f.filename
        dest.parent.mkdir(parents=True, exist_ok=True)
        f.save(dest)
        saved += 1

    flash(f"Subidos {saved} fichero(s) a {UPLOAD_DIR}", "success")
    return redirect(url_for("ingesta_docs.index"))


@bp.route("/run", methods=["POST"])
def run():
    source_id  = request.form.get("source_id")
    input_dir  = (request.form.get("input_dir") or "").strip()
    patterns_s = (request.form.get("patterns") or "").strip()
    recursive  = bool(request.form.get("recursive"))
    only_new   = bool(request.form.get("only_new"))
    extra_args = (request.form.get("extra_args") or "").strip()

    if not source_id:
        flash("Selecciona una fuente 'docs' o crea una con 'Guardar como fuente'.", "danger")
        return redirect(url_for("ingesta_docs.index"))

    with db.get_session() as s:
        src = s.get(Source, int(source_id))
        if not src or src.type != "docs":
            flash("Fuente inválida", "danger")
            return redirect(url_for("ingesta_docs.index"))

        # Prefiere lo que hay en el formulario; si falta, tira de la fuente; si falta, de los defaults
        cfg_f = src.config or {}
        input_dir  = input_dir  or cfg_f.get("input_dir") or UI_DEFAULTS["input_dir"]
        patterns_s = patterns_s or cfg_f.get("patterns")  or UI_DEFAULTS["patterns"]
        if "recursive" not in request.form:
            recursive = bool(cfg_f.get("recursive", UI_DEFAULTS["recursive"]))
        if "only_new" not in request.form:
            only_new = bool(cfg_f.get("only_new", UI_DEFAULTS["only_new"]))

        run = IngestionRun(
            source_id=src.id,
            status="running",
            meta={
                "docs_config": {
                    "input_dir": input_dir,
                    "patterns": patterns_s,
                    "recursive": recursive,
                    "only_new": only_new,
                    "extra_args": extra_args,
                }
            },
        )
        s.add(run)
        s.commit()

    py = sys.executable
    script = Path("scripts/ingest_documents.py").as_posix()
    args = [py, script, FLAG_INPUTDIR, input_dir]

    patterns = [p.strip() for p in patterns_s.split(",") if p.strip()]
    for p in patterns:
        args += [FLAG_PATTERN, p]
    if recursive:
        args.append(FLAG_RECURSIVE)
    if only_new:
        args.append(FLAG_ONLY_NEW)
    if extra_args:
        args += shlex.split(extra_args)

    try:
        proc = subprocess.run(args, capture_output=True, text=True, check=False)
        out = (proc.stdout or "") + "\n" + (proc.stderr or "")

        with db.get_session() as s:
            run_db = s.get(IngestionRun, run.id)
            if run_db:
                meta = run_db.meta or {}
                meta["stdout"] = out[-20000:]
                run_db.meta = meta
                run_db.status = "done" if proc.returncode == 0 else "error"
                s.commit()

        flash(
            "Ingesta finalizada correctamente." if proc.returncode == 0
            else "Ingesta finalizada con errores. Revisa el preview.",
            "success" if proc.returncode == 0 else "danger",
        )
    except Exception as e:
        with db.get_session() as s:
            run_db = s.get(IngestionRun, run.id)
            if run_db:
                run_db.status = "error"
                run_db.meta = {**(run_db.meta or {}), "exception": str(e)}
                s.commit()
        flash(f"Fallo al ejecutar la ingesta: {e}", "danger")

    return redirect(url_for("ingesta_docs.index"))
