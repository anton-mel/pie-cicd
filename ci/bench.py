#!/usr/bin/env python3
"""Run the benchmarks in ci/ci.toml and compare them against the baselines.

    bench.py run <platform> --out results.json
    bench.py report <results.json> [--baseline ci/baselines/<platform>.json]

A benchmark is a command plus a regex with one capture group, the number.
`better` says which direction is an improvement. `report` prints a markdown
table and exits non-zero when a metric regresses past its tolerance.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

try:
    import tomllib  # noqa: F401
except ModuleNotFoundError:
    if shutil.which("uv") and not os.environ.get("PIE_CI_REEXEC"):
        os.environ["PIE_CI_REEXEC"] = "1"
        os.execvp("uv", ["uv", "run", "--no-project", "--python", ">=3.11", __file__, *sys.argv[1:]])
    sys.exit("bench.py needs Python 3.11 or newer, or `uv` on PATH to fetch one.")

sys.path.insert(0, str(Path(__file__).resolve().parent))
import ci

ROOT = ci.ROOT
BASELINES = ROOT / "ci" / "baselines"


def benchmarks(config: dict, platform: str) -> dict:
    return {
        name: bench
        for name, bench in config.get("bench", {}).items()
        if platform in bench.get("platforms", [])
    }


def measure(bench: dict) -> float | None:
    done = subprocess.run(
        ["bash", "-c", bench["run"]], cwd=ROOT, capture_output=True, text=True
    )
    found = re.search(bench["metric"], done.stdout + done.stderr)
    if done.returncode != 0 or not found:
        print(f"  failed: {bench['run']}", file=sys.stderr)
        print((done.stdout + done.stderr)[-800:], file=sys.stderr)
        return None
    return float(found.group(1))


def cmd_run(config: dict, platform: str, out: str) -> int:
    results = {}
    for name, bench in benchmarks(config, platform).items():
        print(f"== {name}", flush=True)
        value = measure(bench)
        if value is None:
            return 1
        print(f"   {value} {bench['unit']}", flush=True)
        results[name] = {"value": value, "unit": bench["unit"], "better": bench["better"]}
    payload = {"platform": platform, "results": results}
    Path(out).write_text(json.dumps(payload, indent=2))
    return 0


def delta(current: float, base: float, better: str) -> tuple[float, bool]:
    change = (current - base) / base * 100 if base else 0.0
    gained = change >= 0 if better == "higher" else change <= 0
    return change, gained


def cmd_report(config: dict, results: str, baseline: str | None) -> int:
    payload = json.loads(Path(results).read_text())
    platform = payload["platform"]
    base_path = Path(baseline) if baseline else BASELINES / f"{platform}.json"
    base = json.loads(base_path.read_text())["results"] if base_path.exists() else {}
    tolerance = {name: b.get("tolerance", 5.0) for name, b in config.get("bench", {}).items()}

    rows = ["| benchmark | baseline | this run | change |", "| --- | ---: | ---: | ---: |"]
    regressions = []
    for name, got in payload["results"].items():
        unit = got["unit"]
        was = base.get(name, {}).get("value")
        if was is None:
            rows.append(f"| {name} | new | {got['value']:.1f} {unit} | |")
            continue
        change, gained = delta(got["value"], was, got["better"])
        mark = "" if gained else " ⚠️"
        rows.append(
            f"| {name} | {was:.1f} {unit} | {got['value']:.1f} {unit} | {change:+.1f}%{mark} |"
        )
        if not gained and abs(change) > tolerance.get(name, 5.0):
            regressions.append(f"{name}: {change:+.1f}%")

    table = f"### Benchmarks on `{platform}`\n\n" + "\n".join(rows) + "\n"
    print(table)
    if summary := os.environ.get("GITHUB_STEP_SUMMARY"):
        with open(summary, "a") as f:
            f.write(table + "\n")
    if regressions:
        for line in regressions:
            print(f"::error::benchmark regressed, {line}")
        return 1
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)
    run = sub.add_parser("run")
    run.add_argument("platform")
    run.add_argument("--out", default="bench-results.json")
    report = sub.add_parser("report")
    report.add_argument("results")
    report.add_argument("--baseline")
    args = parser.parse_args()

    config = ci.load()
    if args.command == "run":
        return cmd_run(config, args.platform, args.out)
    return cmd_report(config, args.results, args.baseline)


if __name__ == "__main__":
    sys.exit(main())
