#!/usr/bin/env bash
# Create or update the `main` branch ruleset from ci/ruleset.json.
#
#   ci/apply-ruleset.sh <owner/repo> [--no-merge-queue] [--approvals N] [--dry-run]
#
# Needs `gh` logged in as a repository admin. The merge queue exists only
# for repositories owned by an organization; for a personal repository pass
# --no-merge-queue, which instead requires a PR to be up to date with main
# before it merges. --approvals sets how many reviews a PR needs (default 1);
# a one-person repository needs 0, since GitHub does not let an author
# approve their own PR.
set -euo pipefail
cd "$(dirname "$0")"

repo="${1:?usage: apply-ruleset.sh <owner/repo> [--no-merge-queue] [--approvals N] [--dry-run]}"; shift
queue=1; dry=0; approvals=1
while [ $# -gt 0 ]; do
  case "$1" in
    --no-merge-queue) queue=0 ;;
    --approvals) approvals="${2:?--approvals needs a number}"; shift ;;
    --dry-run) dry=1 ;;
    *) echo "unknown flag: $1" >&2; exit 2 ;;
  esac
  shift
done

body=$(python3 - "$queue" "$approvals" <<'PY'
import json, sys
rules = json.load(open("ruleset.json"))
for r in rules["rules"]:
    if r["type"] == "pull_request":
        r["parameters"]["required_approving_review_count"] = int(sys.argv[2])
if sys.argv[1] == "0":
    rules["rules"] = [r for r in rules["rules"] if r["type"] != "merge_queue"]
    for r in rules["rules"]:
        if r["type"] == "required_status_checks":
            r["parameters"]["strict_required_status_checks_policy"] = True
print(json.dumps(rules))
PY
)

if [ "$dry" = 1 ]; then echo "$body" | python3 -m json.tool; exit 0; fi
command -v gh >/dev/null || { echo "needs the GitHub CLI: https://cli.github.com" >&2; exit 1; }

id=$(gh api "repos/$repo/rulesets" --jq '.[] | select(.name == "main") | .id' || true)
if [ -n "$id" ]; then
  echo "$body" | gh api -X PUT "repos/$repo/rulesets/$id" --input - >/dev/null
  echo "updated ruleset $id on $repo"
else
  echo "$body" | gh api -X POST "repos/$repo/rulesets" --input - >/dev/null
  echo "created ruleset on $repo"
fi
gh api -X PATCH "repos/$repo" -F allow_squash_merge=true -F allow_merge_commit=false -F allow_rebase_merge=false \
  -F delete_branch_on_merge=true -f squash_merge_commit_title=PR_TITLE -f squash_merge_commit_message=BLANK >/dev/null
echo "merges are squash-only, titled by the PR, and merged branches are deleted"
