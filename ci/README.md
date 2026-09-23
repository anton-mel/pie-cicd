# CI

Everything CI runs is defined in [`ci.toml`](ci.toml). The workflows in
`.github/workflows` only turn that file into jobs, so changing what runs
where never means editing YAML.

## How a change lands

1. Push a branch and open a pull request. CI runs the `pr` level: every
   check in parallel, on Linux and macOS.
2. A reviewer approves. Pushing new commits resets the approval.
3. Press **Merge when ready**. The merge queue rebases the PR onto the
   current main and runs the `queue` level, which adds Windows and Linux
   arm64. Only if that passes does GitHub squash it onto main.

Nobody pushes to main directly, admins included. That is what keeps main
green: every commit on it passed CI exactly as it exists on main.

The PR title becomes the commit title on main, so write it the way the
history reads: `area: what changed`, for example
`engine-metal: reserve windows for capped fire pieces`.

## The levels

| Level | Runs on | Blocks |
|---|---|---|
| `pr` | every push to a pull request | the merge |
| `queue` | the merge queue and every push to main; adds to `pr` | the merge |
| `nightly` | 06:00 UTC daily, or by hand; adds to `queue` | nothing, but a red nightly is a bug |

A check listed in a platform's `advisory` runs and reports, but does not
block. It is for a platform being brought up; once it is reliably green,
remove it from `advisory`.

The only required status check is `ci-ok`. It passes when every blocking
job passed, so adding, renaming or removing a check never touches the
repository settings.

## Running checks locally

Once per clone:

```sh
python3 ci/ci.py setup
```

It checks that your machine can run the checks (Rust version, Python,
Node, a C compiler that links) and says how to fix each gap. It also
installs a git pre-push hook that refuses a direct push to main and runs
`fmt` before every push; skip it once with `git push --no-verify`.
`python3 ci/ci.py doctor` repeats the machine check.

Then the same script CI runs, on your machine:

```sh
python3 ci/ci.py list              # every platform, level and check
python3 ci/ci.py run clippy test   # some checks
python3 ci/ci.py run pr            # everything a PR runs on this platform
python3 ci/ci.py run -n build      # print the script instead of running it
```

It needs Python 3.11 or newer; with an older `python3` it fetches one
through [`uv`](https://docs.astral.sh/uv/) if that is installed. It runs
the commands but does not install system packages, Node or Python
packages for you: `list` says what each check needs. Checks that
`pip install` do so into whichever Python is first on your PATH, so use a
virtual environment.

## Benchmarks

`ci/ci.toml` declares them under `[bench.*]`: a command, a regex that pulls one
number out of its output, a unit, and which direction is better. `bench.yml`
runs them, compares against `ci/baselines/<platform>.json`, and posts a table
on the PR:

| benchmark | baseline | this run | change |
| --- | ---: | ---: | ---: |
| decode | 57.8 tok/s | 57.9 tok/s | +0.2% |
| prefill | 227.0 tok/s | 330.0 tok/s | +45.4% |

A metric that moves the wrong way by more than its `tolerance` fails the job.

```sh
python3 ci/bench.py run macos-arm64 --out results.json
python3 ci/bench.py report results.json
```

Add the `benchmark` label to measure a PR; the nightly run always measures.
Update a baseline by copying a green run's `results.json` over
`ci/baselines/<platform>.json` in the same PR that earns the change.

## The GPU

`cuda-gpu` is a self-hosted machine with an NVIDIA GPU. It never runs a
PR's code unless a maintainer adds the `ci-cuda` label: the self-hosted
runner must not execute code nobody reviewed. Adding the label runs the
GPU checks immediately (`gpu.yml`), and every later push runs them as
blocking checks. The nightly run always includes them. If the machine is
offline, those jobs wait in the queue, for up to a day, until it returns.

## Changing CI

- **A new check:** add a `[checks.<name>]` table with `about` and `run`,
  and list it on the platforms that should run it.
- **A new platform:** add a `[[platform]]` with a GitHub runner label, its
  Rust target and engines, and the checks it runs at each level. Start its
  checks as `advisory`.
- **A different release machine:** edit `[[release.binary]]` or
  `[[release.server]]`. `build.yml`, `release-npm.yml` and
  `release-pypi.yml` all read them.

`python3 ci/ci.py validate` catches unknown checks, typos in keys and
placeholders a platform does not define. The `audits` check also runs
`scripts/workflow-ref-audit.py`, which resolves every cargo command here
against the workspace, so a misspelled crate or feature fails CI instead
of silently skipping.

Checks run under `bash -euo pipefail` from the repository root, each
command in its own subshell, so a `cd` in one does not affect the next.

## Repository settings

`ci/ruleset.json` is the branch protection for main. Apply it with the
GitHub CLI as an admin:

```sh
ci/apply-ruleset.sh pie-project/pie
ci/apply-ruleset.sh <you>/<repo> --no-merge-queue --approvals 0   # a personal demo
```

It requires a pull request with one approval, the `ci-ok` check,
resolved review threads and a linear history, and it blocks force pushes
and deleting main. Nobody can bypass it. The merge queue exists only for
repositories owned by an organization; `--no-merge-queue` instead requires
a PR to be up to date with main before it merges. A one-person repository
needs `--approvals 0`, because GitHub does not let authors approve their
own PRs. The script also makes
merges squash-only, titled by the PR, and deletes merged branches.

Publishing is gated separately: the `crates-io`, `npm` and `pypi`
environments should each list required reviewers under
**Settings > Environments**, so a release needs a second person.

## Files

| Path | What it is |
|---|---|
| `ci/ci.toml` | checks, platforms, levels and release machines |
| `ci/ci.py` | plans the job matrix and runs checks locally |
| `ci/bench.py`, `ci/baselines/` | measures the benchmarks and compares them |
| `.github/workflows/bench.yml` | runs the benchmarks and posts the table |
| `ci/ruleset.json`, `ci/apply-ruleset.sh` | branch protection for main |
| `ci/hooks/pre-push` | the local hook `ci.py setup` installs |
| `ci/lib/` | helpers the workflows call: crate lists, release uploads, the manylinux container setup |
| `.github/workflows/ci.yml` | plans, runs the checks, reports `ci-ok` |
| `.github/workflows/checks.yml` | runs a list of checks in parallel; shared by `ci.yml` and `gpu.yml` |
| `.github/workflows/gpu.yml` | runs the GPU checks when the `ci-cuda` label is added |
| `.github/actions/toolchain` | installs Rust and what a check needs, and restores the cache |
| `.github/workflows/build.yml` | the nightly binaries |
| `.github/workflows/release-*.yml` | publishing to crates.io, npm and PyPI |
