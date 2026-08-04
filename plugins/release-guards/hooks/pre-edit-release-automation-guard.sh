#!/usr/bin/env bash
#
# pre-edit-release-automation-guard.sh
# Claude Code PreToolUse(Edit|Write|MultiEdit) hook.
#
# Purpose:
#   Turn "never modify release/changelog automation on your own — ask first"
#   from a convention the model is trusted to remember into a harness gate. When Claude attempts to Edit / Write /
#   MultiEdit a release/changelog automation file, return an "ask" decision so
#   Claude Code surfaces a confirmation prompt to the user BEFORE the edit lands.
#
#   This is an ASK gate, NOT a hard block. These files ARE legitimately editable
#   on the user's explicit request (a workflow tweak, a cliff template change),
#   so the gate just guarantees the user is in the loop each time rather than
#   forbidding the edit outright. It is the softer sibling of the hard-blocking
#   pre-commit-sensitive-scan.sh, sharing the same defense-in-depth philosophy.
#
# Guarded files (repo-agnostic — these names in any repo):
#   - .github/workflows/release.yml  (and release.yaml)   [path-scoped]
#   - cliff.toml                                           [basename]
#   - RELEASE.md                                           [basename]
#   - .goreleaser.yaml / .goreleaser.yml                   [basename]
#   - release-please-config.json                           [basename]
#   release.yml/.yaml is matched ONLY under .github/workflows/ so an unrelated
#   file that happens to be named release.yml is not flagged.
#
# Decision contract (Claude Code PreToolUse hook):
#   exit 0 + JSON {permissionDecision: "ask"}  -> user confirmation prompt
#   exit 0 + no output                         -> allow silently (not a match)
#   This hook NEVER emits "deny" and NEVER exits 2: it only asks, never blocks.
#
# Input: PreToolUse JSON on stdin ({ tool_input.file_path, tool_name, cwd, ... }).
#
# Notes:
#   - Fails OPEN: any parse error / missing file_path -> allow silently (exit 0).
#     An ask gate is a courtesy prompt, not a security boundary, so a broken
#     parse must never wedge every Edit/Write in every repo.
#   - Pure-bash fast path: a payload mentioning none of the trigger tokens skips
#     the python3 spawn, so the common (non-release) Edit/Write stays cheap.
#   - "ask" does not bypass permission modes; it still prompts even under an
#     auto-accept edit mode, which is exactly the intent.
#
set -euo pipefail

# --- read the hook payload (entire stdin) ---
payload="$(cat)"

# Fast path: if the payload mentions none of the trigger tokens, this cannot be
# one of the guarded files. "release" (lowercase) also covers release-please and
# .goreleaser (both contain the substring "release"); "RELEASE" covers
# RELEASE.md; "cliff" covers cliff.toml.
case "$payload" in
  *release*|*RELEASE*|*cliff*) : ;;
  *) exit 0 ;;
esac

# --- precise parse: is the target file a guarded release-automation file? ---
# python3 handles JSON robustly plus the path/basename matching. Prints the ask
# decision JSON to stdout when matched, nothing otherwise. Always exits 0.
printf '%s' "$payload" | python3 -c '
import json, os, sys

try:
    d = json.load(sys.stdin)
except Exception:
    sys.exit(0)  # fail-open: unparseable payload -> allow silently

fp = (d.get("tool_input") or {}).get("file_path") or ""
if not fp:
    sys.exit(0)

norm = os.path.normpath(fp)
base = os.path.basename(norm)

BASENAMES = {
    "cliff.toml",
    "RELEASE.md",
    ".goreleaser.yaml",
    ".goreleaser.yml",
    "release-please-config.json",
}
# release.yml / release.yaml only when it is the GitHub Actions release workflow.
WORKFLOW_SUFFIXES = (
    os.path.join(".github", "workflows", "release.yml"),
    os.path.join(".github", "workflows", "release.yaml"),
)

matched = base in BASENAMES or any(
    norm == s or norm.endswith(os.sep + s) for s in WORKFLOW_SUFFIXES
)
if not matched:
    sys.exit(0)

reason = (
    "%s is a release/changelog automation file. The rule is to "
    "never modify these on your own -- git-cliff / release.yml / goreleaser / "
    "release-please regenerate them. Confirm ONLY if the user explicitly asked "
    "for this edit; otherwise decline and ask first." % base
)
json.dump({
    "hookSpecificOutput": {
        "hookEventName": "PreToolUse",
        "permissionDecision": "ask",
        "permissionDecisionReason": reason,
    }
}, sys.stdout)
'

exit 0
