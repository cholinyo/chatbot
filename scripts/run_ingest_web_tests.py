#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
run_ingest_web_tests.py - Runner multiplataforma para probar 4 estrategias sin romper el regex en Windows.
- Evita shell=True y construye argv como lista -> CMD no interpreta '|' del regex.
- Imprime comandos "bonitos" solo a modo informativo.
"""
import json, os, sys, subprocess, datetime
from pathlib import Path

SCRIPT = "scripts/ingest_web.py"  # ajusta si tu ruta es distinta

def _now_stamp():
    return datetime.datetime.now().strftime("%Y%m%d_%H%M%S")

def _pretty(cmd_list):
    parts = []
    for c in cmd_list:
        if " " in c or any(ch in c for ch in ['"', "'", "|", "\\"]):
            parts.append(f'"{c}"')
        else:
            parts.append(c)
    return " ".join(parts)

def _run(cmd_list, env=None):
    print("\n$ " + _pretty(cmd_list))
    result = subprocess.run(cmd_list, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    print(result.stdout)
    return result.returncode

def _read_summary(run_dir: Path):
    f = run_dir / "summary.json"
    if f.exists():
        try:
            return json.loads(f.read_text(encoding="utf-8"))
        except Exception:
            return {}
    return {}

def build_common_flags(args):
    base = [
        ("--seed", args.seed),
        ("--source-id", str(args.source_id)),
        ("--user-agent", args.user_agent),
        ("--timeout", str(args.timeout)),
        ("--max-pages", str(args.max_pages)),
        ("--rate-per-host", str(args.rate_per_host)),
        ("--allowed-domains", args.domains or ""),
        ("--exclude", args.exclude),
    ]
    if args.force_https: base.append(("--force-https",))
    if args.robots_policy: base.append(("--robots-policy", args.robots_policy))
    return base

def run_strategy(args, strat: str, base_dir: Path, run_id: int, extra=None):
    extra = extra or []
    run_dir = base_dir / f"run_{run_id}_{strat}"
    run_dir.mkdir(parents=True, exist_ok=True)
    env = os.environ.copy()
    env["RUN_DIR"] = str(run_dir)

    cmd = ["python", SCRIPT, "--strategy", strat, "--run-id", str(run_id)]
    for item in build_common_flags(args):
        cmd.extend(item)
    if strat in ("requests", "selenium"):
        cmd.extend(["--depth", str(args.depth)])
    if strat == "selenium":
        cmd.extend(["--driver", args.driver])
        if not args.headless:
            cmd.append("--no-headless")
        cmd.extend(["--render-wait-ms", str(args.render_wait_ms), "--window-size", args.window_size])
        if args.wait_selector:
            cmd.extend(["--wait-selector", args.wait_selector])
    cmd.extend(extra)

    rc = _run(cmd, env=env)
    summary = _read_summary(run_dir)
    return strat, rc, run_dir, summary

def main(argv=None):
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--seed", required=True)
    ap.add_argument("--domains", default="")
    ap.add_argument("--max-pages", type=int, default=25)
    ap.add_argument("--timeout", type=int, default=15)
    ap.add_argument("--depth", type=int, default=2)
    ap.add_argument("--rate-per-host", type=float, default=1.0)
    ap.add_argument("--user-agent", default="Mozilla/5.0 (Windows NT 10.0; Win64; x64)")
    ap.add_argument("--exclude", default=r"\.(png|jpg|jpeg|gif|css|js|pdf)$")
    ap.add_argument("--source-id", type=int, default=1)
    ap.add_argument("--force-https", action="store_true")
    ap.add_argument("--robots-policy", choices=["strict", "ignore"], default="strict")
    # selenium
    ap.add_argument("--driver", default="chrome")
    ap.add_argument("--headless", action="store_true")
    ap.add_argument("--render-wait-ms", type=int, default=2500)
    ap.add_argument("--window-size", default="1366,900")
    ap.add_argument("--wait-selector", default="")
    args = ap.parse_args(argv)

    base_dir = Path("data/processed/runs/web") / f"batch_{_now_stamp()}"
    base_dir.mkdir(parents=True, exist_ok=True)
    print(f"[BASE_RUN_DIR] {base_dir}")

    results = []
    results.append(run_strategy(args, "sitemap", base_dir, 4001))
    results.append(run_strategy(args, "requests", base_dir, 4002))
    results.append(run_strategy(args, "selenium", base_dir, 4003))

    strat, rc, run_dir, summary = run_strategy(args, "sitemap", base_dir, 4004)
    if (summary.get("n_pages") or 0) == 0:
        print("[AUTO] sitemap devolvió 0 páginas → fallback a requests")
        strat, rc, run_dir, summary = run_strategy(args, "requests", base_dir, 4004)
    results.append(("auto", rc, run_dir, summary))

    csv_lines = ["strategy,run_dir,pages,bytes"]
    for strat, rc, run_dir, summary in results:
        pages = summary.get("n_pages", 0)
        by = summary.get("bytes", 0)
        print(f"{strat.upper():>8} → pages={pages:>4} bytes={by:>8}  [{run_dir}]")
        csv_lines.append(f"{strat},{run_dir},{pages},{by}")

    csv_path = base_dir / "summary_batch.csv"
    csv_path.write_text("\n".join(csv_lines), encoding="utf-8")
    print(f"\nResumen CSV → {csv_path}")
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
