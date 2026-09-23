#!/usr/bin/env python3
"""Pie's CI, driven by `ci/ci.toml`.

    ci.py list                           what runs where, at which level
    ci.py run <check|level>... [-p P]    run checks here, as CI runs them
    ci.py script <platform> <check>      print the bash script CI runs
    ci.py plan <event> [--labels JSON]   the job matrix for a GitHub event
    ci.py release binaries [--targets]   the nightly binary matrix
    ci.py release server npm|pypi        the pie-server addon/wheel matrix
    ci.py validate                       check ci.toml for mistakes
    ci.py setup                          check this machine and install the git hook
    ci.py doctor                         only check this machine

CI and a laptop run the same script: `plan` embeds the output of `script`
in each matrix entry, and `run` executes that same output locally.
"""

from __future__ import annotations

import argparse
import json
import os
import platform as host
import re
import shlex
import shutil
import subprocess
import sys
from pathlib import Path

try:
    import tomllib
except ModuleNotFoundError:  # Python < 3.11, e.g. macOS's /usr/bin/python3
    if __name__ == "__main__" and shutil.which("uv") and not os.environ.get("PIE_CI_REEXEC"):
        os.environ["PIE_CI_REEXEC"] = "1"
        os.execvp("uv", ["uv", "run", "--no-project", "--python", ">=3.11", __file__, *sys.argv[1:]])
    sys.exit("ci.py needs Python 3.11 or newer (for tomllib), or `uv` on PATH to fetch one.")

ROOT = Path(__file__).resolve().parents[1]
CONFIG = ROOT / "ci" / "ci.toml"

LEVELS = ["pr", "queue", "nightly"]
EVENT_LEVEL = {
    "pull_request": "pr",
    "merge_group": "queue",
    "push": "queue",
    "schedule": "nightly",
    "workflow_dispatch": "nightly",
}
PLACEHOLDER = re.compile(r"(?<!\$)\{([a-z_]+)\}")
BENCH_KEYS = {"run", "metric", "unit", "better", "platforms", "tolerance", "about"}
CHECK_KEYS = {"about", "run", "timeout", "engines", "apt", "node", "python", "cache", "cache_paths", "env"}
PLATFORM_KEYS = {"name", "runner", "triple", "engines", "vars", "advisory", "label", "self_hosted", *LEVELS}


class ConfigError(Exception):
    pass


def load() -> dict:
    with CONFIG.open("rb") as f:
        config = tomllib.load(f)
    problems = validate(config)
    if problems:
        raise ConfigError("ci/ci.toml:\n" + "\n".join(f"  * {p}" for p in problems))
    return config


def validate(config: dict) -> list[str]:
    problems = []
    checks = config.get("checks", {})
    defaults = config.get("vars", {})
    for name, check in checks.items():
        for key in set(check) - CHECK_KEYS:
            problems.append(f"check `{name}` has an unknown key `{key}`")
        if not check.get("run"):
            problems.append(f"check `{name}` runs nothing")
        if not check.get("about"):
            problems.append(f"check `{name}` has no `about`")
    seen = set()
    listed = set()
    for p in config.get("platform", []):
        name = p.get("name", "?")
        if name in seen:
            problems.append(f"platform `{name}` is defined twice")
        seen.add(name)
        for key in set(p) - PLATFORM_KEYS:
            problems.append(f"platform `{name}` has an unknown key `{key}`")
        for key in ("runner", "triple", "engines"):
            if key not in p:
                problems.append(f"platform `{name}` has no `{key}`")
        mine = [c for level in LEVELS for c in p.get(level, [])]
        for c in mine:
            if c not in checks:
                problems.append(f"platform `{name}` lists unknown check `{c}`")
            elif mine.count(c) > 1:
                problems.append(f"platform `{name}` lists `{c}` at more than one level")
        for c in p.get("advisory", []):
            if c not in mine:
                problems.append(f"platform `{name}` marks `{c}` advisory but never runs it")
        listed.update(mine)
        known = {"engines", "triple", *defaults, *p.get("vars", {})}
        for c in mine:
            for command in checks.get(c, {}).get("run", []):
                for var in PLACEHOLDER.findall(command):
                    if var not in known:
                        problems.append(f"check `{c}` uses `{{{var}}}`, which platform `{name}` does not define")
    for name in checks:
        if name not in listed:
            problems.append(f"check `{name}` is defined but no platform runs it")
    names = {p.get("name") for p in config.get("platform", [])}
    for name, bench in config.get("bench", {}).items():
        for key in set(bench) - BENCH_KEYS:
            problems.append(f"bench `{name}` has an unknown key `{key}`")
        for key in ("run", "metric", "unit", "better", "platforms"):
            if key not in bench:
                problems.append(f"bench `{name}` has no `{key}`")
        if bench.get("better") not in (None, "higher", "lower"):
            problems.append(f"bench `{name}`: `better` is `higher` or `lower`")
        for on in bench.get("platforms", []):
            if on not in names:
                problems.append(f"bench `{name}` names unknown platform `{on}`")
    return problems


def platform_named(config: dict, name: str) -> dict:
    for p in config["platform"]:
        if p["name"] == name:
            return p
    raise SystemExit(f"no platform `{name}` in ci/ci.toml; known: {', '.join(p['name'] for p in config['platform'])}")


def this_platform(config: dict) -> dict:
    system, machine = host.system(), host.machine().lower()
    arch = {"amd64": "x64", "x86_64": "x64", "arm64": "arm64", "aarch64": "arm64"}.get(machine, machine)
    name = {"Linux": "linux", "Darwin": "macos", "Windows": "windows"}.get(system, system.lower()) + "-" + arch
    return platform_named(config, name)


def checks_at(platform: dict, level: str) -> list[str]:
    upto = LEVELS[: LEVELS.index(level) + 1]
    return [c for lv in upto for c in platform.get(lv, [])]


def commands(config: dict, platform: dict, check_name: str) -> list[str]:
    """A check's commands with this platform's placeholders filled in."""
    values = {
        **{k: str(v) for k, v in config.get("vars", {}).items()},
        **{k: str(v) for k, v in platform.get("vars", {}).items()},
        "engines": ",".join(platform["engines"]),
        "triple": platform["triple"],
    }
    out = []
    for command in config["checks"][check_name]["run"]:
        command = PLACEHOLDER.sub(lambda m: values[m.group(1)], command)
        out.append(re.sub(r"  +", " ", command).strip())
    return out


def render(config: dict, platform: dict, check_name: str) -> str:
    """The bash script that runs one check on one platform."""
    check = config["checks"][check_name]
    lines = [
        f"# {check_name} on {platform['name']}: {check['about']}",
        "set -euo pipefail",
        'cd "$(git rev-parse --show-toplevel)"',
    ]
    for key, value in check.get("env", {}).items():
        lines.append(f"export {key}={shlex.quote(str(value))}")
    for command in commands(config, platform, check_name):
        # Each command in a subshell, so a `cd` does not leak into the next.
        lines.append(f"printf '\\n\\033[1m+ %s\\033[0m\\n' {shlex.quote(command)}")
        lines.append(f"( {command} )")
    return "\n".join(lines) + "\n"


def leg(config: dict, platform: dict, check_name: str) -> dict:
    """One GitHub Actions matrix entry."""
    check = config["checks"][check_name]
    cached = check.get("cache", True) and not platform.get("self_hosted")
    linux = "linux" in platform["triple"]
    return {
        "name": f"{platform['name']} / {check_name}",
        "platform": platform["name"],
        "check": check_name,
        "runner": platform["runner"],
        "self_hosted": bool(platform.get("self_hosted")),
        "triple": platform["triple"],
        "features": ",".join(platform["engines"]) if check.get("engines") else "",
        "apt": " ".join(check.get("apt", [])) if linux and not platform.get("self_hosted") else "",
        "node": bool(check.get("node")),
        "python": bool(check.get("python")),
        "cache_key": f"{platform['name']}-{check_name}" if cached else "",
        "cache_paths": "\n".join(check.get("cache_paths", [])),
        "timeout": int(check.get("timeout", 30)),
        "script": render(config, platform, check_name),
    }


def plan(config: dict, event: str, labels: list[str], level: str | None, only_labeled: bool = False) -> dict:
    """The legs an event runs. A PR carrying a platform's `label` runs every
    check that platform lists; `only_labeled` keeps just those platforms."""
    level = level or EVENT_LEVEL.get(event, "pr")
    required, advisory = [], []
    for p in config["platform"]:
        labeled = event == "pull_request" and p.get("label") in labels
        if only_labeled and not labeled:
            continue
        names = checks_at(p, "nightly") if labeled else checks_at(p, level)
        for name in names:
            (advisory if name in p.get("advisory", []) else required).append(leg(config, p, name))
    return {"level": level, "required": required, "advisory": advisory}


def write_outputs(result: dict) -> None:
    out = os.environ.get("GITHUB_OUTPUT")
    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if out:
        with open(out, "a") as f:
            for key in ("required", "advisory"):
                f.write(f"{key}={json.dumps(result[key])}\n")
            f.write(f"level={result['level']}\n")
    if summary:
        with open(summary, "a") as f:
            f.write(f"### CI plan: `{result['level']}` level\n\n| job | blocks merge |\n|---|---|\n")
            for entry in result["required"]:
                f.write(f"| {entry['name']} | yes |\n")
            for entry in result["advisory"]:
                f.write(f"| {entry['name']} | no (advisory) |\n")
    if not out:
        print(json.dumps({k: [e["name"] for e in v] if isinstance(v, list) else v for k, v in result.items()}, indent=2))


def release(config: dict, kind: str, target: str | None, filters: str) -> list[dict]:
    rel = config["release"]
    if kind == "binaries":
        rows = []
        for b in rel["binary"]:
            row = {k: b[k] for k in ("label", "runner", "triple", "features")}
            if b.get("cuda_container"):
                row["container"] = rel["cuda_container"]
            rows.append(row)
        want = [w.strip() for w in filters.split(",") if w.strip()]
        if want == ["all"]:
            return rows
        if want == ["none"]:
            return []
        return [r for r in rows if any(w in r["label"] for w in want)]
    if kind == "server":
        if target == "npm":
            return [{"npm": s["npm"], "runner": s["runner"], "triple": s["triple"], "features": s["features"], "lib": s["lib"]}
                    for s in rel["server"]]
        if target == "pypi":
            rows = []
            for s in rel["server"]:
                row = {"label": s["wheel"], "runner": s["runner"], "triple": s["triple"], "features": s["features"]}
                if s.get("manylinux"):
                    row["manylinux"] = s["manylinux"]
                rows.append(row)
            return rows
        raise SystemExit("release server: say `npm` or `pypi`")
    raise SystemExit("release: say `binaries` or `server`")


def bench_legs(config: dict) -> list[dict]:
    wanted = {on for bench in config.get("bench", {}).values() for on in bench["platforms"]}
    legs = []
    for p in config["platform"]:
        if p["name"] in wanted:
            legs.append({
                "name": f"{p['name']} / bench",
                "platform": p["name"],
                "runner": p["runner"],
                "triple": p["triple"],
                "features": ",".join(p["engines"]),
                "self_hosted": bool(p.get("self_hosted")),
            })
    return legs


def cmd_list(config: dict) -> None:
    here = None
    try:
        here = this_platform(config)["name"]
    except SystemExit:
        pass
    for p in config["platform"]:
        runner = p["runner"] if isinstance(p["runner"], str) else "+".join(p["runner"])
        mark = "  <- this machine" if p["name"] == here else ""
        print(f"\n{p['name']}  ({runner}, engines: {', '.join(p['engines'])}){mark}")
        if p.get("label"):
            print(f"  a PR with the `{p['label']}` label runs everything below")
        for level in LEVELS:
            names = p.get(level, [])
            if names:
                shown = [n + (" (advisory)" if n in p.get("advisory", []) else "") for n in names]
                print(f"  {level:<8} {', '.join(shown)}")
    print("\nchecks:")
    for name, check in config["checks"].items():
        print(f"  {name:<13} {check['about']}")


def cmd_run(config: dict, names: list[str], platform_name: str | None, dry_run: bool) -> int:
    p = platform_named(config, platform_name) if platform_name else this_platform(config)
    todo = []
    for name in names:
        if name in LEVELS:
            todo += checks_at(p, name)
        elif name in config["checks"]:
            todo.append(name)
        else:
            raise SystemExit(f"`{name}` is neither a check nor a level ({', '.join(LEVELS)}); see `ci.py list`")
    bash = shutil.which("bash")
    if not bash:
        raise SystemExit("ci.py run needs bash on PATH")
    failed = []
    for name in dict.fromkeys(todo):
        check = config["checks"][name]
        needs = [t for t, on in (("Node.js", check.get("node")), ("Python", check.get("python"))) if on]
        needs += [f"apt: {' '.join(check['apt'])}"] if check.get("apt") and "linux" in p["triple"] else []
        print(f"\n\033[1;34m== {name} on {p['name']}\033[0m" + (f"  (needs {'; '.join(needs)})" if needs else ""), flush=True)
        script = render(config, p, name)
        if dry_run:
            print(script)
            continue
        if subprocess.run([bash, "-c", script], cwd=ROOT).returncode != 0:
            failed.append(name)
    if failed:
        print(f"\n\033[1;31mfailed: {', '.join(failed)}\033[0m")
        return 1
    if not dry_run:
        print("\n\033[1;32mall passed\033[0m")
    return 0


def cmd_doctor() -> int:
    """Whether this machine can run the checks, with a fix for each gap."""
    problems = 0

    def report(ok: bool, what: str, fix: str = "") -> None:
        nonlocal problems
        print(f"  {'ok  ' if ok else 'FAIL'}  {what}" + ("" if ok else f"\n        fix: {fix}"))
        problems += 0 if ok else 1

    def output(*cmd: str) -> str:
        try:
            return subprocess.run(cmd, capture_output=True, text=True, cwd=ROOT).stdout.strip()
        except OSError:
            return ""

    want = tomllib.loads((ROOT / "rust-toolchain.toml").read_text())["toolchain"]["channel"]
    have = output("cargo", "--version")
    report(want in have, f"cargo {want} (have: {have or 'none'})",
           "install rustup from https://rustup.rs; it reads rust-toolchain.toml")
    report(sys.version_info >= (3, 11), f"Python 3.11+ (running {sys.version.split()[0]})",
           "install a newer Python, or `uv`")
    node = output("node", "--version")
    report(bool(node), f"Node.js for the javascript checks ({node or 'none'})", "install Node 22")
    report(bool(shutil.which("bash")), "bash", "install bash")

    # A C toolchain that links: build scripts need it. On macOS a Command
    # Line Tools update can ship an SDK its own linker cannot read.
    import tempfile
    with tempfile.TemporaryDirectory() as tmp:
        src = Path(tmp, "t.c")
        src.write_text("int main(void){return 0;}\n")
        linked = subprocess.run(["cc", str(src), "-o", str(Path(tmp, "t"))], capture_output=True, text=True)
        fix = "install a C compiler (build-essential, or Xcode Command Line Tools)"
        if linked.returncode != 0 and host.system() == "Darwin":
            sdks = sorted(Path("/Library/Developer/CommandLineTools/SDKs").glob("MacOSX[0-9]*.*.sdk"))
            good = [sdk for sdk in reversed(sdks) if subprocess.run(
                ["cc", str(src), "-o", str(Path(tmp, "t"))], capture_output=True, env={**os.environ, "SDKROOT": str(sdk)}
            ).returncode == 0]
            fix = ("reinstall the Command Line Tools (`sudo rm -rf /Library/Developer/CommandLineTools && "
                   "xcode-select --install`)")
            if good:
                fix += f", or for now `export SDKROOT={good[0]}` in your shell profile"
        report(linked.returncode == 0, "the C compiler links a program", fix)

    hooks = output("git", "config", "--get", "core.hooksPath")
    report(hooks == "ci/hooks", "git hook: no direct pushes to main, fmt before push",
           "python3 ci/ci.py setup")
    print("\nready." if problems == 0 else f"\n{problems} problem(s).")
    return 1 if problems else 0


def cmd_setup() -> int:
    subprocess.run(["git", "config", "core.hooksPath", "ci/hooks"], cwd=ROOT, check=True)
    print("installed the pre-push hook (git config core.hooksPath ci/hooks)\n")
    return cmd_doctor()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("list")
    sub.add_parser("validate")
    sub.add_parser("setup")
    sub.add_parser("doctor")
    r = sub.add_parser("run")
    r.add_argument("names", nargs="+")
    r.add_argument("-p", "--platform")
    r.add_argument("-n", "--dry-run", action="store_true", help="print the scripts instead of running them")
    s = sub.add_parser("script")
    s.add_argument("platform")
    s.add_argument("check")
    pl = sub.add_parser("plan")
    pl.add_argument("event")
    pl.add_argument("--labels", default="[]", help="JSON list of the PR's label names")
    pl.add_argument("--level", choices=LEVELS, help="override the level the event implies")
    pl.add_argument("--only-labeled", action="store_true", help="only the platforms whose label the PR carries")
    sub.add_parser("bench-plan")
    rel = sub.add_parser("release")
    rel.add_argument("kind", choices=["binaries", "server"])
    rel.add_argument("target", nargs="?")
    rel.add_argument("--targets", default="all", help="binaries: comma-separated label substrings, `all` or `none`")
    args = parser.parse_args()

    try:
        config = load()
    except ConfigError as e:
        print(e, file=sys.stderr)
        return 1

    if args.command == "setup":
        return cmd_setup()
    if args.command == "doctor":
        return cmd_doctor()
    if args.command == "validate":
        print(f"ci/ci.toml: {len(config['checks'])} checks on {len(config['platform'])} platforms, no problems")
        return 0
    if args.command == "list":
        cmd_list(config)
        return 0
    if args.command == "run":
        return cmd_run(config, args.names, args.platform, args.dry_run)
    if args.command == "script":
        p = platform_named(config, args.platform)
        if args.check not in config["checks"]:
            raise SystemExit(f"no check `{args.check}`")
        sys.stdout.write(render(config, p, args.check))
        return 0
    if args.command == "bench-plan":
        legs = bench_legs(config)
        out = os.environ.get("GITHUB_OUTPUT")
        if out:
            with open(out, "a") as f:
                f.write(f"legs={json.dumps(legs)}\n")
        print(json.dumps(legs, indent=2))
        return 0
    if args.command == "plan":
        labels = json.loads(args.labels or "[]") or []
        write_outputs(plan(config, args.event, labels, args.level, args.only_labeled))
        return 0
    if args.command == "release":
        rows = release(config, args.kind, args.target, args.targets)
        text = json.dumps(rows)
        out = os.environ.get("GITHUB_OUTPUT")
        if out:
            with open(out, "a") as f:
                f.write(f"matrix={text}\n")
        print(json.dumps(rows, indent=2))
        return 0
    return 1


if __name__ == "__main__":
    sys.exit(main())
