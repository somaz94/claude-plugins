#!/usr/bin/env bash
# doc-tidy bundled-script tests
#
# Run directly (`bash plugins/doc-tidy/tests/run.sh`) or through the whole
# suite (`bash tests/run.sh`). CI calls this same file.
set -euo pipefail
. "$(dirname "${BASH_SOURCE[0]:-$0}")/../../../tests/lib.sh"

DT="python3 -B plugins/doc-tidy/scripts/doctidy.py"

# A throwaway repository with a known history. Git identity and signing are
# pinned here so the suite behaves the same on a laptop with a signing key
# configured and on a CI runner with no identity at all.
make_repo() {
  local root="$1"
  git init -q "$root"
  git -C "$root" symbolic-ref HEAD refs/heads/main
}
commit_all() {
  git -C "$1" add -A
  git -C "$1" -c user.name=t -c user.email=t@example.com -c commit.gpgsign=false commit -q -m "${2:-fixture}"
}

step 'doc-tidy unit tests'
(
set -euo pipefail
python3 -B -m unittest discover -s plugins/doc-tidy/tests
echo "PASS: parser, discovery, pairs, link graph, findings, history, similarity, plans, sweep, relink and apply"
)

step 'doc-tidy shipped rules are valid'
(
set -euo pipefail
$DT --check-rules
[ -z "$(find plugins/doc-tidy/rules -name '*.local.tsv')" ] \
  || { echo "FAIL: a personal *.local.tsv overlay is committed"; exit 1; }
[ -z "$(find plugins/doc-tidy/tests -name '*.md')" ] \
  || { echo "FAIL: a Markdown fixture is committed; tests must write their own"; exit 1; }
echo "PASS: every example matches its rule, no counter-example does, no overlay or fixture shipped"
)

step 'doc-tidy scan finds a committed handoff, a finished migration and a broken link'
(
set -euo pipefail
fixture="$(mktemp -d)"
repo="$fixture/acme"
make_repo "$repo"
mkdir -p "$repo/docs/queue-migration" "$repo/_deprecated/tools/old" "$repo/tools/new"
printf '# Acme\n\n[Guide](docs/guide.md) · [Missing](docs/missing.md)\n' > "$repo/README.md"
printf '# Acme\n\n[Guide](docs/guide-ko.md)\n' > "$repo/README-ko.md"
printf '# Guide\n' > "$repo/docs/guide.md"
printf '# 가이드\n' > "$repo/docs/guide-ko.md"
printf '# Billing CI handoff\n\nPaste this into the next session.\n' > "$repo/docs/billing-ci-handoff.md"
printf '# Plan\n' > "$repo/docs/queue-migration/plan.md"
printf '# Status\n\nStatus: done\n\n| step | state |\n|---|---|\n| cutover | ✅ |\n' > "$repo/docs/queue-migration/status.md"
printf '# Phase 1\n' > "$repo/docs/queue-migration/01-phase1.md"
printf '# Old tool\n' > "$repo/_deprecated/tools/old/README.md"
printf '# New tool\n' > "$repo/tools/new/README.md"
commit_all "$repo"

$DT scan "$repo" --json --out "$fixture/run" > "$fixture/scan.json"
python3 - "$fixture/scan.json" "$fixture/run" <<'PY'
import json, sys, pathlib
report = json.load(open(sys.argv[1]))
codes = {(f["code"], f["path"]) for f in report["findings"]}
failures = []
for expected in (("broken-link", "README.md"), ("byproduct", "docs/billing-ci-handoff.md"),
                 ("migration-folder", "docs/queue-migration")):
    if expected not in codes:
        failures.append(f"missing finding {expected}; got {sorted(codes)}")
if ("orphan", "docs/guide.md") in codes:
    failures.append("a linked pair was reported as an orphan")
if report["archive"]["preferred"] != "_deprecated":
    failures.append(f"archive convention not detected: {report['archive']}")
batch = json.load(open(pathlib.Path(sys.argv[2]) / "batch-01.json"))
folder = [u for u in batch["units"] if u["id"] == "docs/queue-migration"]
if not folder or folder[0]["kind"] != "folder" or len(folder[0]["paths"]) != 3:
    failures.append(f"the migration folder is not one unit: {folder}")
for line in failures: print(f"FAIL: {line}")
sys.exit(1 if failures else 0)
PY
echo "PASS: byproduct, finished migration folder, broken link and archive convention are measured"
)

step 'doc-tidy relink rewrites both halves of a pair, leaves code alone, and flags plain-text references'
(
set -euo pipefail
fixture="$(mktemp -d)"
repo="$fixture/acme"
make_repo "$repo"
mkdir -p "$repo/docs" "$repo/archive/docs"
printf '# Acme\n\n[Rollout](docs/rollout.md#steps)\n\n```md\n[Rollout](docs/rollout.md#steps)\n```\n' > "$repo/README.md"
printf '# Acme\n\n[Rollout](docs/rollout-ko.md#steps)\n' > "$repo/README-ko.md"
printf '# Rollout\n\n[Back](../README.md)\n' > "$repo/docs/rollout.md"
printf '# 롤아웃\n\n[Back](../README-ko.md)\n' > "$repo/docs/rollout-ko.md"
printf '# Old\n' > "$repo/archive/docs/old-notes.md"
printf 'lint:\n\tmarkdownlint docs/rollout.md\n' > "$repo/Makefile"
commit_all "$repo"

$DT relink "$repo" --archive docs/rollout.md --json --save "$fixture/plan.json" > /dev/null
python3 - "$fixture/plan.json" <<'PY'
import json, sys
plan = json.load(open(sys.argv[1]))
failures = []
moves = sorted((o["from"], o["to"]) for o in plan["operations"])
if moves != [("docs/rollout-ko.md", "archive/docs/rollout-ko.md"), ("docs/rollout.md", "archive/docs/rollout.md")]:
    failures.append(f"the pair did not move as one unit: {moves}")
edits = {(e["file"], e["new"]) for e in plan["edits"]}
for expected in (("README-ko.md", "(archive/docs/rollout-ko.md#steps)"), ("docs/rollout.md", "(../../README.md)"),
                 ("docs/rollout-ko.md", "(../../README-ko.md)")):
    if expected not in edits:
        failures.append(f"missing edit {expected}; got {sorted(edits)}")
if not plan["manual"] and not any(e["file"] == "README.md" for e in plan["edits"]):
    failures.append("README.md's link was neither rewritten nor handed to a manual edit")
refs = {(t["file"], t["line"]) for t in plan["textRefs"]}
if ("Makefile", 2) not in refs:
    failures.append(f"the Makefile reference was not flagged: {plan['textRefs']}")
if ("README.md", 3) in refs:
    failures.append("a link already handled as an edit was reported again as plain text")
if plan["refusals"]:
    failures.append(f"unexpected refusals: {plan['refusals']}")
for line in failures: print(f"FAIL: {line}")
sys.exit(1 if failures else 0)
PY
echo "PASS: both halves and their own links are rewritten, the fenced copy is not, the Makefile is reported"
)

step 'doc-tidy apply writes exactly the plan, verifies, and refuses to run twice'
(
set -euo pipefail
fixture="$(mktemp -d)"
repo="$fixture/acme"
make_repo "$repo"
mkdir -p "$repo/docs" "$repo/archive/docs"
printf '# Acme\n\n[Rollout](docs/rollout.md)\n' > "$repo/README.md"
printf '# Rollout\n\n[Back](../README.md)\n' > "$repo/docs/rollout.md"
printf '# Old\n' > "$repo/archive/docs/old-notes.md"
commit_all "$repo"

$DT scan "$repo" --out "$fixture/run" > /dev/null
$DT relink "$repo" --archive docs/rollout.md --json --save "$fixture/plan.json" > /dev/null
$DT apply --plan "$fixture/plan.json" > "$fixture/apply.txt"
grep -q 'doc-tidy apply: done' "$fixture/apply.txt" || { echo "FAIL: apply did not finish"; cat "$fixture/apply.txt"; exit 1; }
git -C "$repo" status --porcelain | grep -q 'docs/rollout.md -> archive/docs/rollout.md' \
  || { echo "FAIL: the move was not staged as a rename"; git -C "$repo" status --porcelain; exit 1; }
grep -q '(archive/docs/rollout.md)' "$repo/README.md" || { echo "FAIL: the inbound link was not rewritten"; exit 1; }
$DT scan "$repo" --baseline "$fixture/run/scan.json" > /dev/null \
  || { echo "FAIL: the re-scan found a new broken link or a split pair"; exit 1; }

before="$(git -C "$repo" status --porcelain)"
if $DT apply --plan "$fixture/plan.json" > "$fixture/again.txt"; then
  echo "FAIL: a plan applied twice was accepted"; exit 1
fi
grep -q 'nothing changed' "$fixture/again.txt" || { echo "FAIL: the second run did not report a refusal"; exit 1; }
[ "$before" = "$(git -C "$repo" status --porcelain)" ] || { echo "FAIL: the refused run changed files"; exit 1; }
echo "PASS: renames staged, links rewritten, re-scan clean, second run refused with no writes"
)

step 'doc-tidy plans finds an expiring plan and a reference to a deleted one'
(
set -euo pipefail
fixture="$(mktemp -d)"
config="$fixture/claude"
mkdir -p "$config/plans"
printf '{"cleanupPeriodDays": 10}\n' > "$config/settings.json"
printf '# Aging plan\n\n## Decisions\n' > "$config/plans/brisk-folding-lamport.md"
python3 - "$config/plans/brisk-folding-lamport.md" <<'PY'
import os, sys, time
nine_days = time.time() - 9 * 86400
os.utime(sys.argv[1], (nine_days, nine_days))
PY
printf 'Catalog: ~/.claude/plans/gone-catalog-plan.md\nNow: ~/.claude/plans/brisk-folding-lamport.md\nExample: plans/foo.md\n' > "$config/CLAUDE.md"

$DT plans --config-dir "$config" --json > "$fixture/plans.json"
python3 - "$fixture/plans.json" <<'PY'
import json, sys
report = json.load(open(sys.argv[1]))
failures = []
if report["retention"]["days"] != 10:
    failures.append(f"cleanupPeriodDays not read: {report['retention']}")
if [d["slug"] for d in report["dangling"]] != ["gone-catalog-plan"]:
    failures.append(f"dangling references wrong (the example must be ignored): {report['dangling']}")
expiring = [f for f in report["findings"] if f["code"] == "plan-expiring"]
if len(expiring) != 1 or expiring[0]["severity"] != "critical":
    failures.append(f"a referenced plan one day from deletion should be critical: {expiring}")
for line in failures: print(f"FAIL: {line}")
sys.exit(1 if failures else 0)
PY
echo "PASS: retention, the dangling reference and the referenced expiring plan are reported"
)

step 'doc-tidy ships no machine-specific home paths'
(
set -euo pipefail
python3 - <<'PY'
import pathlib, re, sys
# Assembled at run time so this file does not itself contain a path of that shape.
home = re.compile("/(?:" + "Us" + "ers|" + "ho" + "me)/(?!you/)[A-Za-z0-9_.-]+/")
hits = []
for path in sorted(pathlib.Path("plugins/doc-tidy").rglob("*")):
    if path.is_file() and path.suffix in {".py", ".tsv", ".md", ".json", ".sh"}:
        for number, line in enumerate(path.read_text(encoding="utf-8", errors="replace").splitlines(), 1):
            if home.search(line):
                hits.append(f"{path}:{number}: {line.strip()[:120]}")
for hit in hits:
    print(f"FAIL: {hit}")
sys.exit(1 if hits else 0)
PY
echo "PASS: only placeholder home paths"
)
