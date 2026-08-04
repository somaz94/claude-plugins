#!/usr/bin/env bash
#
# pre-release-action-guard.sh
# Claude Code PreToolUse(Bash) hook.
#
# Purpose:
#   Turn "a release tag or release object is created only when the user asks
#   for it" from a convention the model is trusted to remember into a gate the
#   harness enforces. When a Bash command would create/modify/delete a release
#   TAG or a release OBJECT, return an "ask" decision so Claude Code surfaces a
#   confirmation prompt BEFORE the action runs.
#
#   This is an ASK gate, NOT a block. Release actions ARE legitimate on the
#   user's explicit request (a /release run, git-release-tag-runner, etc.), so
#   the gate just keeps the user in the loop each time rather than forbidding.
#   It is the command-side sibling of pre-edit-release-automation-
#   guard.sh (which gates EDITING release-automation FILES); together they cover
#   both "touching the release pipeline" and "performing a release action".
#
# Guarded commands (any simple command in a &&/||/;/newline chain; a leading
# `cd <dir> &&` is tolerated):
#   - git [global-opts] tag <create-or-delete>   (NOT list/inspect)
#       create: a tag-name positional, or -a/-s/-m/-F/-u/-f create flags
#       delete: -d / --delete
#       allowed (read-only, no ask): bare `git tag`, -l/--list, --contains,
#       --points-at, --merged/--no-merged, -n, --sort=, --format=, --column
#   - gh   release {create,edit,delete,upload}   (view/list/download allowed)
#   - glab release {create,update,delete,upload} (view/list/download allowed)
#
# Decision contract (Claude Code PreToolUse hook):
#   exit 0 + JSON {permissionDecision: "ask"}  -> user confirmation prompt
#   exit 0 + no output                         -> allow silently (not a match)
#   This hook NEVER emits "deny" and NEVER exits 2: it only asks, never blocks.
#
# Input: PreToolUse JSON on stdin ({ tool_input.command, cwd, tool_name }).
#
# Notes:
#   - Fails OPEN: any parse error / non-match -> allow silently (exit 0). An ask
#     gate is a courtesy prompt, not a security boundary.
#   - Pure-bash fast path: a payload mentioning neither "tag" nor "release"
#     skips the python3 spawn, so the common Bash call stays cheap.
#   - "ask" does not bypass permission modes; it still prompts even under an
#     auto-accept mode, which is exactly the intent for an outward-facing action.
#   - NO single-quote characters inside the python3 -c block (the wrapper is
#     single-quoted; a stray ' would leak code to bash). Backticks are used in
#     user-facing strings instead.
#
set -euo pipefail

# --- read the hook payload (entire stdin) ---
payload="$(cat)"

# Fast path: neither "tag" nor "release" present -> cannot be a guarded action.
case "$payload" in
  *tag*|*release*) : ;;
  *) exit 0 ;;
esac

# --- precise parse: is this a release tag / release object action? ---
printf '%s' "$payload" | python3 -c '
import json, re, shlex, sys

try:
    d = json.load(sys.stdin)
except Exception:
    sys.exit(0)  # fail-open

cmd = (d.get("tool_input") or {}).get("command", "") or ""
if not cmd:
    sys.exit(0)

CREATE_FLAGS_NOARG = {"-a", "--annotate", "-s", "--sign", "-f", "--force", "--create-reflog"}
CREATE_FLAGS_ARG   = {"-m", "--message", "-F", "--file", "-u", "--local-user"}
DELETE_FLAGS       = {"-d", "--delete"}
LIST_FLAGS_NOARG   = {"-l", "--list", "--contains", "--no-contains", "--points-at",
                      "--merged", "--no-merged", "--omit-empty", "--column", "--no-column",
                      "-i", "--ignore-case", "--create-reflog"}
LIST_FLAGS_ARG     = {"--sort", "--format", "--color"}

def tag_is_mutating(rest):
    list_mode = False
    create = False
    delete = False
    positionals = []
    i = 0
    while i < len(rest):
        t = rest[i]
        if t in DELETE_FLAGS:
            delete = True; i += 1; continue
        if t in CREATE_FLAGS_NOARG:
            create = True; i += 1; continue
        if t in CREATE_FLAGS_ARG:
            create = True; i += 2; continue
        if t in LIST_FLAGS_NOARG:
            list_mode = True; i += 1; continue
        if t in LIST_FLAGS_ARG:
            list_mode = True; i += 2; continue
        if t == "-n" or t.startswith("-n"):
            list_mode = True; i += 1; continue
        if t.startswith("--sort=") or t.startswith("--format=") or t.startswith("--color="):
            list_mode = True; i += 1; continue
        if t.startswith("-"):
            i += 1; continue  # unknown flag -> ignore
        positionals.append(t); i += 1
    if delete or create:
        return True
    if list_mode:
        return False
    # bare `git tag <name>` (and optional <commit>) = lightweight tag creation
    return len(positionals) > 0

GH_RELEASE_MUTATING   = {"create", "edit", "delete", "upload"}
GLAB_RELEASE_MUTATING = {"create", "update", "delete", "upload"}

reason = None
for seg in re.split(r"&&|\|\||;|\n", cmd):
    try:
        toks = shlex.split(seg)
    except ValueError:
        toks = seg.split()
    if not toks:
        continue
    prog = toks[0]
    if prog == "git":
        # walk git global options to reach the subcommand
        i = 1
        while i < len(toks):
            t = toks[i]
            if t in ("-C", "--git-dir", "--work-tree", "--namespace") and i + 1 < len(toks):
                i += 2; continue
            if t.startswith("-"):
                i += 1; continue
            break
        sub = toks[i] if i < len(toks) else ""
        if sub == "tag" and tag_is_mutating(toks[i + 1:]):
            reason = "`git tag` creates or deletes a release tag"
            break
    elif prog == "gh":
        if len(toks) >= 3 and toks[1] == "release" and toks[2] in GH_RELEASE_MUTATING:
            reason = "`gh release %s` mutates a GitHub release" % toks[2]
            break
    elif prog == "glab":
        if len(toks) >= 3 and toks[1] == "release" and toks[2] in GLAB_RELEASE_MUTATING:
            reason = "`glab release %s` mutates a GitLab release" % toks[2]
            break

if reason is None:
    sys.exit(0)  # not a guarded release action -> allow silently

msg = (
    "%s. Per the global release discipline, a release tag / release object is "
    "created or changed only on your explicit request. Confirm ONLY if you "
    "intend this release action now; otherwise decline. (Annotated tags only; "
    "let sliding major tags auto-manage; never touch release.yml / cliff.toml / "
    "RELEASE.md.)" % reason
)
json.dump({
    "hookSpecificOutput": {
        "hookEventName": "PreToolUse",
        "permissionDecision": "ask",
        "permissionDecisionReason": msg,
    }
}, sys.stdout)
'

exit 0
