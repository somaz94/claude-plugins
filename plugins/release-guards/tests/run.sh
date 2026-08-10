#!/usr/bin/env bash
# release-guards bundled-script tests
#
# Both hooks ASK, never block, so a mistake here is quiet in both directions: a
# missed case lets an irreversible action run unprompted, and an over-broad match
# puts a confirmation in front of `git tag -l` until the prompts stop being read.
# The suite therefore pins the read-only commands as hard as the mutating ones.
#
# Run directly (`bash plugins/release-guards/tests/run.sh`) or through the whole
# suite (`bash tests/run.sh`). CI calls this same file.
set -euo pipefail
. "$(dirname "${BASH_SOURCE[0]:-$0}")/../../../tests/lib.sh"

PLUGIN="$ROOT/plugins/release-guards"
ACTION_HOOK="$PLUGIN/hooks/pre-release-action-guard.sh"
EDIT_HOOK="$PLUGIN/hooks/pre-edit-release-automation-guard.sh"

# Feed a hook one payload and report the decision it made: `ask` or `allow`.
# Anything else — a non-zero exit, a "deny", malformed JSON — is a defect, since
# these hooks are contractually incapable of blocking.
decision() {
  local hook="$1" payload="$2" out rc
  set +e
  out="$(printf '%s' "$payload" | bash "$hook" 2>/dev/null)"
  rc=$?
  set -e
  if [ "$rc" != 0 ]; then
    printf 'exit-%s' "$rc"
    return 0
  fi
  if [ -z "$out" ]; then
    printf 'allow'
    return 0
  fi
  printf '%s' "$out" | python3 -c '
import json, sys
try:
    d = json.load(sys.stdin)
except Exception:
    print("malformed-json"); sys.exit(0)
print((d.get("hookSpecificOutput") or {}).get("permissionDecision") or "no-decision")
'
}

bash_payload() {
  python3 -c '
import json, sys
print(json.dumps({"tool_name": "Bash", "tool_input": {"command": sys.argv[1]}, "cwd": "/tmp"}))
' "$1"
}

edit_payload() {
  python3 -c '
import json, sys
print(json.dumps({"tool_name": "Edit", "tool_input": {"file_path": sys.argv[1]}, "cwd": "/tmp"}))
' "$1"
}

failures=0
check() {
  local want="$1" got="$2" what="$3"
  if [ "$want" != "$got" ]; then
    echo "FAIL: $what — expected $want, got $got"
    failures=$((failures + 1))
  fi
}

step 'the action guard asks before a tag or release that cannot be undone'
(
set -euo pipefail
failures=0
for command in \
  "git tag -a v1.2.3 -m 'release'" \
  "git tag v1.2.3" \
  "git tag -d v1.2.3" \
  "git tag --delete v1.2.3" \
  "git tag -s v1.2.3 -m signed" \
  "git tag -f v1 abc1234" \
  "git -C /some/repo tag -a v0.1.0 -m x" \
  "cd /some/repo && git tag -a v0.1.0 -m x" \
  "gh release create v1.2.3 --notes x" \
  "gh release delete v1.2.3" \
  "gh release upload v1.2.3 dist.tgz" \
  "glab release create v1.2.3" \
  "glab release delete v1.2.3" \
  "make build && git tag -a v2.0.0 -m ship"
do
  check ask "$(decision "$ACTION_HOOK" "$(bash_payload "$command")")" "$command"
done
[ "$failures" -eq 0 ] || exit 1
echo "PASS: creation, deletion, -C, cd-chains and both forges all prompt"
)

step 'the action guard stays silent for reading tags and releases'
(
set -euo pipefail
failures=0
# A gate that prompts on `git tag -l` is a gate whose prompts get clicked
# through, which costs more than having no gate at all.
for command in \
  "git tag" \
  "git tag -l" \
  "git tag --list 'v*'" \
  "git tag -l --sort=-creatordate" \
  "git tag --points-at HEAD" \
  "git tag --contains abc1234" \
  "git tag -n5 v1.0.0" \
  "git tag --format='%(refname)'" \
  "gh release view v1.2.3" \
  "gh release list --limit 5" \
  "gh release download v1.2.3" \
  "glab release view v1.2.3" \
  "glab release list" \
  "git log --oneline" \
  "git push origin main" \
  "echo 'tag and release are only words here'"
do
  check allow "$(decision "$ACTION_HOOK" "$(bash_payload "$command")")" "$command"
done
[ "$failures" -eq 0 ] || exit 1
echo "PASS: listing, inspecting and downloading never prompt"
)

step 'the edit guard asks before the files that regenerate themselves'
(
set -euo pipefail
failures=0
for path in \
  "/repo/cliff.toml" \
  "/repo/RELEASE.md" \
  "/repo/.goreleaser.yaml" \
  "/repo/.goreleaser.yml" \
  "/repo/release-please-config.json" \
  "/repo/.github/workflows/release.yml" \
  "/repo/.github/workflows/release.yaml"
do
  check ask "$(decision "$EDIT_HOOK" "$(edit_payload "$path")")" "$path"
done
[ "$failures" -eq 0 ] || exit 1
echo "PASS: every generated release artefact prompts before an edit"
)

step 'the edit guard leaves files that merely sound like releases alone'
(
set -euo pipefail
failures=0
# `release.yml` is guarded as a workflow, not as a name. A chart values file or
# a docs page that happens to be called release.yml is ordinary work.
for path in \
  "/repo/docs/release.md" \
  "/repo/RELEASE-NOTES.md" \
  "/repo/charts/app/templates/release.yml" \
  "/repo/.github/workflows/ci.yml" \
  "/repo/scripts/release.sh" \
  "/repo/cliff-notes.md" \
  "/repo/src/release.go"
do
  check allow "$(decision "$EDIT_HOOK" "$(edit_payload "$path")")" "$path"
done
[ "$failures" -eq 0 ] || exit 1
echo "PASS: a release-shaped name is not enough to trigger the gate"
)

step 'both guards fail open rather than getting in the way'
(
set -euo pipefail
failures=0
# An ask gate is a courtesy prompt, not a security boundary. Anything it cannot
# understand must pass through silently rather than wedge the tool call.
for hook in "$ACTION_HOOK" "$EDIT_HOOK"; do
  name="$(basename "$hook")"
  check allow "$(decision "$hook" 'this is not json at all, but it says release')" \
    "$name on unparseable input"
  check allow "$(decision "$hook" '{}')" "$name on an empty object"
  check allow "$(decision "$hook" '{"tool_input":{}}')" "$name on an empty tool_input"
  check allow "$(decision "$hook" '')" "$name on empty stdin"
done
[ "$failures" -eq 0 ] || exit 1
echo "PASS: malformed and empty payloads allow silently, never deny or error"
)
