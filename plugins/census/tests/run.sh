#!/usr/bin/env bash
# census bundled-script tests
#
# The bodies below are the same text .github/workflows/ci.yml runs; the
# workflow calls this file rather than carrying them inline, so the answer
# to 'will CI pass?' is available before pushing.
set -euo pipefail
. "$(dirname "${BASH_SOURCE[0]:-$0}")/../../../tests/lib.sh"

step 'census smoke test'
(
set -euo pipefail
fixture="$(mktemp -d)"
mkdir -p "$fixture/home/.claude/agents" \
         "$fixture/home/.claude/skills/demo" \
         "$fixture/repo/.claude/commands"
printf -- '---\nname: alpha\ndescription: an agent\n---\nbody\n' \
  > "$fixture/home/.claude/agents/alpha.md"
printf -- '---\nname: demo\ndescription: a skill\n---\nbody\n' \
  > "$fixture/home/.claude/skills/demo/SKILL.md"
printf -- '---\ndescription: a command\n---\nbody\n' \
  > "$fixture/repo/.claude/commands/beta.md"
cat > "$fixture/census.json" <<EOF
{
  "userRoots": ["$fixture/home/.claude"],
  "projectRoots": ["$fixture/repo"],
  "excludeOssForks": false
}
EOF

out="$(python3 plugins/census/scripts/census.py --config "$fixture/census.json" catalog)"
echo "$out"

for expected in "1 agents" "1 commands" "1 skills" '`alpha`' '`beta`' '`demo`'; do
  if ! grep -qF -- "$expected" <<<"$out"; then
    echo "FAIL: expected '$expected' in catalog output"
    exit 1
  fi
done
echo "PASS: census discovered the planted fixture"
)

step 'census refuses to write into a scanned tree'
(
set -euo pipefail
fixture="$(mktemp -d)"
mkdir -p "$fixture/home/.claude/agents"
printf -- '---\nname: alpha\ndescription: an agent\n---\nbody\n' \
  > "$fixture/home/.claude/agents/alpha.md"
cat > "$fixture/census.json" <<EOF
{
  "userRoots": ["$fixture/home/.claude"],
  "projectRoots": [],
  "excludeOssForks": false
}
EOF

if python3 plugins/census/scripts/census.py --config "$fixture/census.json" \
     catalog --out "$fixture/home/.claude/CENSUS.md"; then
  echo "FAIL: wrote a report inside the scanned config root"
  exit 1
fi
if [ -e "$fixture/home/.claude/CENSUS.md" ]; then
  echo "FAIL: refused but the file exists anyway"
  exit 1
fi
python3 plugins/census/scripts/census.py --config "$fixture/census.json" \
  catalog --out "$fixture/outside.md"
test -s "$fixture/outside.md"
echo "PASS: refused inside the root, allowed outside it"
)

step 'census counts a mirrored copy once'
(
set -euo pipefail
fixture="$(mktemp -d)"
mkdir -p "$fixture/a/.claude/agents" "$fixture/b/.claude/agents"
printf -- '---\nname: shared\ndescription: mirrored agent\n---\nbody\n' \
  | tee "$fixture/a/.claude/agents/shared.md" \
  > "$fixture/b/.claude/agents/shared.md"
printf -- '---\nname: solo\ndescription: only here\n---\nbody\n' \
  > "$fixture/a/.claude/agents/solo.md"
cat > "$fixture/census.json" <<EOF
{
  "userRoots": [],
  "projectRoots": ["$fixture/a", "$fixture/b"],
  "excludeOssForks": false
}
EOF

out="$(python3 plugins/census/scripts/census.py --config "$fixture/census.json" catalog)"
echo "$out"
if ! grep -qF -- "Assets: 2 (2 agents)" <<<"$out"; then
  echo "FAIL: expected 2 distinct assets from 3 files"
  exit 1
fi
if ! grep -qF -- "found in 3 files" <<<"$out"; then
  echo "FAIL: expected the file count to still be reported"
  exit 1
fi
echo "PASS: mirrored copy collapsed into one asset"
)

step 'census catches an agent bound to its own repo'
(
set -euo pipefail
fixture="$(mktemp -d)"
mkdir -p "$fixture/acme-platform/.claude/agents"
printf -- '---\nname: bound\ndescription: Reviews changes inside acme-platform/storage/.\n---\nbody\n' \
  > "$fixture/acme-platform/.claude/agents/bound.md"
printf -- '---\nname: free\ndescription: Reviews shell scripts for portability.\n---\nbody\n' \
  > "$fixture/acme-platform/.claude/agents/free.md"
cat > "$fixture/census.json" <<EOF
{
  "userRoots": [],
  "projectRoots": ["$fixture/acme-platform"],
  "excludeOssForks": false
}
EOF

python3 plugins/census/scripts/census.py --config "$fixture/census.json" \
  portability --json > "$fixture/report.json"
python3 - "$fixture/report.json" <<'PY'
import json, sys

report = json.load(open(sys.argv[1]))
tier = {i["name"]: i["tier"] for i in report["items"]}
failures = []
if tier.get("bound") != "PERSONAL":
    failures.append(f"bound: expected PERSONAL, got {tier.get('bound')!r}")
if tier.get("free") != "PORTABLE":
    failures.append(f"free: expected PORTABLE, got {tier.get('free')!r}")
if "acme-platform" not in report.get("scopedMarkers", []):
    failures.append("acme-platform missing from scopedMarkers")

for line in failures:
    print(f"FAIL: {line}")
sys.exit(1 if failures else 0)
PY
echo "PASS: repo-scoped coupling detected without false-positiving a generic agent"
)

step 'census reads a hook through the script it runs'
(
set -euo pipefail
fixture="$(mktemp -d)"
mkdir -p "$fixture/home/.claude/hooks"
printf '#!/usr/bin/env bash\ntest -d "$HOME/AcmeVault" && exit 1\n' \
  > "$fixture/home/.claude/hooks/present.sh"
chmod +x "$fixture/home/.claude/hooks/present.sh"
cat > "$fixture/home/.claude/settings.json" <<EOF
{
  "hooks": {
    "PreToolUse": [
      {
        "matcher": "Bash",
        "hooks": [
          {"type": "command", "command": "$fixture/home/.claude/hooks/present.sh"},
          {"type": "command", "command": "$fixture/home/.claude/hooks/gone.sh"}
        ]
      }
    ]
  }
}
EOF
cat > "$fixture/census.json" <<EOF
{
  "userRoots": ["$fixture/home/.claude"],
  "projectRoots": [],
  "excludeOssForks": false,
  "portability": {"markers": ["AcmeVault"]}
}
EOF

python3 plugins/census/scripts/census.py --config "$fixture/census.json" \
  drift --json > "$fixture/drift.json"
python3 plugins/census/scripts/census.py --config "$fixture/census.json" \
  portability --json > "$fixture/port.json"
python3 - "$fixture/drift.json" "$fixture/port.json" <<'PY'
import json, sys

drift = json.load(open(sys.argv[1]))
port = json.load(open(sys.argv[2]))
failures = []

missing = [f for f in drift["findings"] if f["code"] == "hook-missing-script"]
if len(missing) != 1:
    failures.append(f"expected 1 hook-missing-script finding, got {len(missing)}")
elif "gone.sh" not in missing[0]["title"]:
    failures.append(f"wrong hook flagged: {missing[0]['title']}")

present = next((i for i in port["items"] if i["name"] == "present.sh"), None)
if present is None:
    failures.append("hook was not graded for portability at all")
else:
    if present["tier"] != "PARAMETERIZABLE":
        failures.append(f"present.sh: expected PARAMETERIZABLE, got {present['tier']}")
    if "AcmeVault" not in present["markers"]:
        failures.append("marker inside the hook script was not found")

for line in failures:
    print(f"FAIL: {line}")
sys.exit(1 if failures else 0)
PY
echo "PASS: hook script read for coupling, missing target reported"
)

step 'census sees a mirror its source has moved past'
(
set -euo pipefail
fixture="$(mktemp -d)"
mkdir -p "$fixture/repo/.claude/agents" "$fixture/repo/.claude/agents-ko"
cd "$fixture/repo"
git init -q
git config user.email ci@example.com
git config user.name ci
for name in stale fresh; do
  printf -- '---\nname: %s\ndescription: an agent\n---\nbody\n' "$name" \
    > ".claude/agents/$name.md"
  printf -- '---\nname: %s\ndescription: translated\n---\ntranslated body\n' "$name" \
    > ".claude/agents-ko/$name.md"
done
git add -A
GIT_COMMITTER_DATE="2026-01-01T00:00:00" \
  git commit -q --date="2026-01-01T00:00:00" -m init

# Only the source moves. Both files still have the same shape.
printf -- '---\nname: stale\ndescription: an agent\n---\nbody, revised\n' \
  > .claude/agents/stale.md
git add -A
GIT_COMMITTER_DATE="2026-03-01T00:00:00" \
  git commit -q --date="2026-03-01T00:00:00" -m "revise source only"

cd "$ROOT"
cat > "$fixture/census.json" <<EOF
{
  "userRoots": [],
  "projectRoots": ["$fixture/repo"],
  "excludeOssForks": false,
  "pairs": {"agents": "agents-ko"}
}
EOF

python3 plugins/census/scripts/census.py --config "$fixture/census.json" \
  drift --json > "$fixture/drift.json"
python3 - "$fixture/drift.json" <<'PY'
import json, sys

findings = json.load(open(sys.argv[1]))["findings"]
stale = [f for f in findings if f["code"] == "pair-stale"]
failures = []

if len(stale) != 1:
    failures.append(f"expected 1 pair-stale finding, got {len(stale)}")
elif "`stale`" not in stale[0]["title"]:
    failures.append(f"wrong pair flagged: {stale[0]['title']}")

for line in failures:
    print(f"FAIL: {line}")
sys.exit(1 if failures else 0)
PY
echo "PASS: stale mirror caught, co-committed pair left alone"
)

step 'census tells an inline-shell hook from a missing script'
(
set -euo pipefail
fixture="$(mktemp -d)"
mkdir -p "$fixture/home/.claude/hooks"
printf '#!/usr/bin/env python3\nprint("guard")\n' \
  > "$fixture/home/.claude/hooks/guard.py"
cat > "$fixture/home/.claude/settings.json" <<EOF
{
  "hooks": {
    "PreToolUse": [
      {
        "matcher": "Bash",
        "hooks": [
          {"type": "command", "command": "echo inline-shell-guard"},
          {"type": "command", "command": "jq -r .tool_name"},
          {"type": "command", "command": "python3 $fixture/home/.claude/hooks/guard.py --strict"},
          {"type": "command", "command": "$fixture/home/.claude/hooks/gone.sh"}
        ]
      }
    ]
  }
}
EOF
cat > "$fixture/census.json" <<EOF
{
  "userRoots": ["$fixture/home/.claude"],
  "projectRoots": [],
  "excludeOssForks": false
}
EOF

python3 plugins/census/scripts/census.py --config "$fixture/census.json" \
  drift --json > "$fixture/drift.json"
python3 plugins/census/scripts/census.py --config "$fixture/census.json" \
  scan > "$fixture/scan.json"
python3 - "$fixture/drift.json" "$fixture/scan.json" <<'PY'
import json, sys

drift = json.load(open(sys.argv[1]))
hooks = {h["name"]: h for h in json.load(open(sys.argv[2]))["items"]
         if h["kind"] == "hook"}
failures = []

# Every registration is still catalogued; only the verdict changed.
if len(hooks) != 4:
    failures.append(f"expected 4 hook registrations, got {sorted(hooks)}")

for inline in ("echo", "jq"):
    hook = hooks.get(inline)
    if hook is None:
        failures.append(f"{inline}: inline-shell hook was dropped entirely")
    elif hook["script"] is not None:
        failures.append(f"{inline}: resolved a script it never had: {hook['script']}")

behind = hooks.get("guard.py")
if behind is None:
    failures.append("script behind an interpreter prefix was not found")
elif not behind["scriptExists"]:
    failures.append(f"guard.py resolved to a missing file: {behind['script']}")

missing = [f for f in drift["findings"] if f["code"] == "hook-missing-script"]
if len(missing) != 1:
    failures.append(
        f"expected exactly 1 hook-missing-script, got "
        f"{[f['title'] for f in missing]}"
    )
elif "gone.sh" not in missing[0]["title"]:
    failures.append(f"wrong hook flagged: {missing[0]['title']}")

for line in failures:
    print(f"FAIL: {line}")
sys.exit(1 if failures else 0)
PY
echo "PASS: inline shell left alone, interpreter prefix followed, real gap reported"
)

step 'census survives a malformed config'
(
set -euo pipefail
fixture="$(mktemp -d)"
mkdir -p "$fixture/home/.claude/agents"
printf -- '---\nname: alpha\ndescription: an agent\n---\nbody\n' \
  > "$fixture/home/.claude/agents/alpha.md"
cat > "$fixture/census.json" <<EOF
{
  "userRoots": ["$fixture/home/.claude"],
  "projectRoots": [],
  "excludeOssForks": false,
  "portability": []
}
EOF

for shape in \
  '{"hooks": {"PreToolUse": "not-a-list"}}' \
  '{"hooks": {"PreToolUse": [{"hooks": "not-a-list"}]}}' \
  '{"hooks": {"PreToolUse": [{"hooks": ["not-an-object"]}]}}' \
  '{"hooks": [1, 2]}' \
  '{"hooks": {"PreToolUse": [{"matcher": 5, "hooks": [{"command": 123}]}]}}'
do
  echo "$shape" > "$fixture/home/.claude/settings.json"
  for command in catalog drift portability; do
    if ! out="$(python3 plugins/census/scripts/census.py \
         --config "$fixture/census.json" "$command" 2>&1)"; then
      echo "FAIL: $command aborted on settings.json shape $shape"
      echo "$out" | tail -3
      exit 1
    fi
  done
  # The agent alongside the broken file must still be found.
  if ! grep -qF -- '`alpha`' <<<"$out"; then
    echo "FAIL: a malformed settings.json suppressed the rest of the root"
    exit 1
  fi
done
echo "PASS: malformed shapes skipped, surrounding config still audited"
)

step 'census does not invent translation mirrors by default'
(
set -euo pipefail
fixture="$(mktemp -d)"
mkdir -p "$fixture/home/.claude/agents" "$fixture/home/.claude/agents-ko"
for name in alpha beta; do
  printf -- '---\nname: %s\ndescription: an agent\n---\nbody\n' "$name" \
    > "$fixture/home/.claude/agents/$name.md"
done
# Only `alpha` has a mirror, so opting in must report exactly one gap.
printf -- '---\nname: alpha\ndescription: translated\n---\nbody\n' \
  > "$fixture/home/.claude/agents-ko/alpha.md"

emit() {
  cat > "$fixture/census.json" <<EOF
{
  "userRoots": ["$fixture/home/.claude"],
  "projectRoots": [],
  "excludeOssForks": false$1
}
EOF
}

emit ""
python3 plugins/census/scripts/census.py --config "$fixture/census.json" \
  drift --json > "$fixture/default.json"
emit ', "pairs": {"agents": "agents-ko"}'
python3 plugins/census/scripts/census.py --config "$fixture/census.json" \
  drift --json > "$fixture/opted-in.json"

python3 - "$fixture/default.json" "$fixture/opted-in.json" <<'PY'
import json, sys

def pair_gaps(path):
    findings = json.load(open(path))["findings"]
    return [f for f in findings if f["code"] == "pair-missing"]

failures = []
default = pair_gaps(sys.argv[1])
if default:
    failures.append(
        f"default config invented {len(default)} missing mirrors: "
        f"{[f['title'] for f in default]}"
    )

opted = pair_gaps(sys.argv[2])
if len(opted) != 1:
    failures.append(f"opting in should report 1 gap, got {[f['title'] for f in opted]}")
elif "`beta`" not in opted[0]["title"]:
    failures.append(f"wrong item flagged: {opted[0]['title']}")

for line in failures:
    print(f"FAIL: {line}")
sys.exit(1 if failures else 0)
PY
echo "PASS: silent by default, still reports the real gap when opted in"
)

step 'census grades a user-level item by the repos it names'
(
set -euo pipefail
fixture="$(mktemp -d)"
mkdir -p "$fixture/home/.claude/agents" \
         "$fixture/code/acme-billing-api" \
         "$fixture/code/docs" \
         "$fixture/code/carrier/.claude/agents"
printf -- '---\nname: bound\ndescription: Runs the migration suite for acme-billing-api.\n---\nbody\n' \
  > "$fixture/home/.claude/agents/bound.md"
printf -- '---\nname: free\ndescription: Reviews shell scripts for portability.\n---\nRefer to the docs when unsure.\n' \
  > "$fixture/home/.claude/agents/free.md"
printf -- '---\nname: local\ndescription: A repo-scoped agent.\n---\nbody\n' \
  > "$fixture/code/carrier/.claude/agents/local.md"
cat > "$fixture/census.json" <<EOF
{
  "userRoots": ["$fixture/home/.claude"],
  "projectRoots": ["$fixture/code/*"],
  "excludeOssForks": false,
  "pairs": {}
}
EOF

python3 plugins/census/scripts/census.py --config "$fixture/census.json" \
  portability --json > "$fixture/port.json"
python3 - "$fixture/port.json" <<'PY'
import json, sys

report = json.load(open(sys.argv[1]))
tier = {i["name"]: i for i in report["items"]}
failures = []

bound = tier.get("bound")
if bound is None:
    failures.append("the bound agent was not graded at all")
else:
    if bound["tier"] != "PERSONAL":
        failures.append(f"bound: expected PERSONAL, got {bound['tier']}")
    if "acme-billing-api" not in bound["markers"]:
        failures.append(f"bound: repo name not matched, markers={bound['markers']}")

# `docs` is a real directory here. A single-word repo name is also an
# ordinary word, and matching it would fire on the prose in `free`.
free = tier.get("free")
if free is None:
    failures.append("the free agent was not graded at all")
elif free["tier"] != "PORTABLE":
    failures.append(f"free: expected PORTABLE, got {free['tier']} ({free['markers']})")
if "docs" in report.get("namedRepoMarkers", []):
    failures.append("a single-word repo name became a marker — generic-word trap")

for line in failures:
    print(f"FAIL: {line}")
sys.exit(1 if failures else 0)
PY
echo "PASS: named repo caught, single-word repo name left alone"
)

step 'census reports a portable item blocked by its dependency'
(
set -euo pipefail
fixture="$(mktemp -d)"
mkdir -p "$fixture/home/.claude/agents" "$fixture/home/.claude/commands" \
         "$fixture/code/acme-billing-api"
printf -- '---\nname: bound-helper\ndescription: Reviews acme-billing-api migrations.\n---\nbody\n' \
  > "$fixture/home/.claude/agents/bound-helper.md"
printf -- '---\nname: free-helper\ndescription: Reviews shell scripts.\n---\nbody\n' \
  > "$fixture/home/.claude/agents/free-helper.md"
printf -- '---\nname: wrapper\ndescription: Thin wrapper.\n---\nDelegate to `bound-helper` for the review.\n' \
  > "$fixture/home/.claude/commands/wrapper.md"
printf -- '---\nname: clean\ndescription: Thin wrapper.\n---\nDelegate to `free-helper` for the review.\n' \
  > "$fixture/home/.claude/commands/clean.md"
cat > "$fixture/census.json" <<EOF
{
  "userRoots": ["$fixture/home/.claude"],
  "projectRoots": ["$fixture/code/*"],
  "excludeOssForks": false,
  "pairs": {}
}
EOF

python3 plugins/census/scripts/census.py --config "$fixture/census.json" \
  portability --json > "$fixture/port.json"
python3 - "$fixture/port.json" <<'PY'
import json, sys

report = json.load(open(sys.argv[1]))
items = {i["name"]: i for i in report["items"]}
failures = []

# .get() throughout: a build that predates the dependency axis has no
# such keys, and "the field is missing" is the finding — worth saying
# in one line rather than as a KeyError traceback.
wrapper = items.get("wrapper")
if wrapper is None:
    failures.append("wrapper was not graded")
else:
    if wrapper.get("tier") != "PORTABLE":
        failures.append(f"wrapper: own contents should grade PORTABLE, got {wrapper.get('tier')}")
    if wrapper.get("blockedBy") != ["bound-helper"]:
        failures.append(f"wrapper: expected blockedBy [bound-helper], got {wrapper.get('blockedBy')!r}")
    if wrapper.get("shareable", True):
        failures.append("wrapper: counted as share-ready despite a blocked dependency")

clean = items.get("clean")
if clean is None:
    failures.append("clean was not graded")
else:
    if clean.get("dependsOn") != ["free-helper"]:
        failures.append(f"clean: dependency not detected, got {clean.get('dependsOn')!r}")
    if not clean.get("shareable", False):
        failures.append(f"clean: should be share-ready, blockedBy={clean.get('blockedBy')!r}")

if report.get("blocked") != ["wrapper"]:
    failures.append(f"expected exactly ['wrapper'] blocked, got {report.get('blocked')!r}")

for line in failures:
    print(f"FAIL: {line}")
sys.exit(1 if failures else 0)
PY
echo "PASS: blocked wrapper flagged, clean wrapper still share-ready"
)

step 'census sees a script an item shells out to'
(
set -euo pipefail
fixture="$(mktemp -d)"
mkdir -p "$fixture/home/.claude/agents" "$fixture/home/.claude/helpers" \
         "$fixture/home/.claude/hooks"
printf '#!/usr/bin/env bash\necho shared\n' > "$fixture/home/.claude/helpers/shared-helper.sh"
chmod +x "$fixture/home/.claude/helpers/shared-helper.sh"

printf -- '---\nname: caller\ndescription: Runs the shared helper.\n---\nRun `shared-helper.sh` before reporting.\n' \
  > "$fixture/home/.claude/agents/caller.md"
# `missing-helper.sh` does not exist anywhere, so it is an example.
printf -- '---\nname: illustrator\ndescription: Shows example output.\n---\nOutput looks like `missing-helper.sh: 3 issues`.\n' \
  > "$fixture/home/.claude/agents/illustrator.md"

# A hook that only NAMES a sibling in a comment calls nothing.
printf '#!/usr/bin/env bash\n# sibling of shared-helper.sh, which it does not run\nexit 0\n' \
  > "$fixture/home/.claude/hooks/commenter.sh"
chmod +x "$fixture/home/.claude/hooks/commenter.sh"
cat > "$fixture/home/.claude/settings.json" <<EOF
{"hooks": {"PreToolUse": [{"matcher": "Bash", "hooks": [
  {"type": "command", "command": "$fixture/home/.claude/hooks/commenter.sh"}]}]}}
EOF
cat > "$fixture/census.json" <<EOF
{
  "userRoots": ["$fixture/home/.claude"],
  "projectRoots": [],
  "excludeOssForks": false,
  "pairs": {}
}
EOF

python3 plugins/census/scripts/census.py --config "$fixture/census.json" \
  portability --json > "$fixture/port.json"
python3 - "$fixture/port.json" <<'PY'
import json, sys

report = json.load(open(sys.argv[1]))
items = {i["name"]: i for i in report["items"]}
failures = []

caller = items.get("caller")
if caller is None:
    failures.append("caller was not graded")
else:
    names = [s["name"] for s in caller.get("externalScripts", [])]
    if names != ["shared-helper.sh"]:
        failures.append(f"caller: expected [shared-helper.sh], got {names!r}")
    if caller.get("shareable", True):
        failures.append("caller: share-ready despite calling a script it does not carry")

illustrator = items.get("illustrator")
if illustrator is None:
    failures.append("illustrator was not graded")
elif illustrator.get("externalScripts"):
    failures.append(
        f"illustrator: an example filename was counted: "
        f"{illustrator['externalScripts']!r}"
    )

commenter = items.get("commenter.sh")
if commenter is None:
    failures.append("the hook was not graded")
elif commenter.get("externalScripts"):
    failures.append(
        f"commenter.sh: a sibling named only in a comment was counted: "
        f"{commenter['externalScripts']!r}"
    )

if report.get("callsScripts") != ["caller"]:
    failures.append(f"expected exactly ['caller'], got {report.get('callsScripts')!r}")

for line in failures:
    print(f"FAIL: {line}")
sys.exit(1 if failures else 0)
PY
echo "PASS: real call flagged, example and comment left alone"
)
