#!/usr/bin/env bash
#
# pre-compact-handoff-nudge.sh
#
# Claude Code PreCompact hook shipped with the session-continuity plugin.
#
# Purpose:
#   The session's context window is about to be compacted — either automatically
#   (context near the limit) or manually (`/compact`). Compaction summarizes older
#   turns, so fine-grained in-flight detail (the exact half-done edit, the reason a
#   decision was made) is at risk of being lost. This hook surfaces, at that exact
#   moment, a reminder to hand the work off to a FRESH session via
#   `/session-continuity:handoff now` (mid-session mode) — which captures the in-flight git state before the detail
#   is summarized away. The current session is NOT closed; the user opens a new one
#   alongside it (Claude Code cannot spawn a session programmatically).
#
#   This is the "context is full" signal, standing in for a percentage threshold: Claude Code exposes NO hook that fires at a fixed context
#   fill level (e.g. 70%), and no hook receives a context-percentage field. The
#   PreCompact event (auto-compact about to run) is the only available "filling up"
#   signal, so this is a near-limit nudge, not a 70% nudge.
#
#   Advisory only — it never blocks or cancels compaction (PreCompact cannot),
#   never opens a session, and never runs the handoff itself. It only emits:
#     - a user-visible systemMessage (the nudge), and
#     - an additionalContext line carried into the post-compact context so the
#       model proactively offers the handoff if the work is multi-step.
#
# Input: PreCompact JSON on stdin ({ session_id, transcript_path, cwd,
#        hook_event_name, trigger, custom_instructions }). `trigger` is "auto"
#        (context near the limit) or "manual" (user ran /compact); the message is
#        tailored per trigger. No field is required — a missing trigger degrades to
#        a generic nudge.
#
# Notes:
#   - Fails OPEN: any parse error / unreadable payload -> silent allow (exit 0). A
#     nudge is a courtesy, never a boundary, so a hiccup must never disrupt a
#     compaction the user needs.
#   - No single quotes inside the python3 -c block (a single quote would close the
#     wrapper and leak to bash); any literal is double-quoted. Verify: the python
#     region has 0 of "'".
#   - Fast: one small stdin read + one python3 spawn, no git, no network.
#
set -euo pipefail

# --- read the hook payload (entire stdin) ---
payload="$(cat)"

# --- build the handoff nudge (python3) ---
# Always exits 0 (warn-only; fail-open on uncertainty).
printf '%s' "$payload" | python3 -c '
import json, sys

def quit_silent():
    sys.exit(0)

try:
    d = json.load(sys.stdin)
except Exception:
    quit_silent()

if not isinstance(d, dict):
    quit_silent()

trigger = (d.get("trigger") or "").strip().lower()

if trigger == "manual":
    sysmsg = (
        "Compacting now (/compact). If this is complex multi-step work, consider "
        "`/session-continuity:handoff now` first to capture the in-flight state (branch, uncommitted "
        "diff, the file:line being edited) into a paste-once block for a fresh "
        "session — this session stays open."
    )
    ctx = (
        "The user just ran /compact, so older turns are being summarized. If the "
        "current work is a multi-step task with uncommitted in-flight state, "
        "proactively offer to run `/session-continuity:handoff now` (session-handoff-prompter, "
        "mid-session mode) so the exact in-flight state is preserved for a fresh "
        "session before detail is lost. Do not run it unprompted — offer it."
    )
else:
    # auto (or unknown/missing) -> context is filling up on its own: strongest case.
    sysmsg = (
        "Context is near the limit — auto-compacting now. Older turns will be "
        "summarized and fine-grained detail may be lost. If you are mid multi-step "
        "work, run `/session-continuity:handoff now` to hand off to a FRESH session (this one stays "
        "open): it captures the branch, uncommitted diff, stash, and the file:line "
        "you were editing into a single paste-once block. There is no 70%-threshold "
        "hook in Claude Code; this near-limit compaction is the fill signal."
    )
    ctx = (
        "Context just hit the auto-compact threshold, so older turns are being "
        "summarized and in-flight detail is at risk. If the current work is a "
        "multi-step task, proactively offer to run `/session-continuity:handoff now` "
        "(session-handoff-prompter, mid-session mode) to capture the in-flight git "
        "state for a fresh session while this session stays open. Offer it; do not "
        "run it unprompted."
    )

json.dump({
    "systemMessage": sysmsg,
    "hookSpecificOutput": {
        "hookEventName": "PreCompact",
        "additionalContext": ctx,
    },
}, sys.stdout)
sys.exit(0)
'

exit 0
