---
description: 'Update ~/.claude/plans/<name>.md in place via plan-progress-updater after a meaningful chunk of work'
argument-hint: "[plan-name | free-form what-changed summary]"
allowed-tools: Read, Grep, Glob, Bash
---

# /plan-update

Thin wrapper around the `plan-progress-updater` agent. Keeps `~/.claude/plans/*.md` plan files current as work progresses — refreshes the Progress table, "What changed" section, and lessons-learned tail so the **next session** (after context reset) can resume cleanly without re-deriving context.

User invocation argument: `$ARGUMENTS`

<br/>

## Argument resolution

- empty → inspect open plan references in recent conversation; if exactly one plan is in play, use it; otherwise ask the user which plan
- `<plan-name>` → use `~/.claude/plans/<plan-name>.md` directly (omit `.md` suffix; agent resolves)
- free-form text → treat as the "what changed" summary the agent should weave into the update

<br/>

## When to use

- A phase / sub-task of an active plan finished.
- A blocker was resolved.
- Scope changed mid-plan (a new constraint, decision override, surprise edge case).
- The user says "update the plan" / "reflect this in the plan".

When NOT to use:

- The work isn't tied to a plan file — use a memory write or just continue.
- The plan is finished — use `~/.claude/agents/session-handoff-prompter.md` instead (via `/handoff`) if a fresh session needs to pick it up.
- Brand-new plan creation — that's plan-mode's job, not this command.

<br/>

## Step 1 — Delegate to `plan-progress-updater`

Invoke the agent with:

- Plan file path (absolute).
- The "what changed" summary from `$ARGUMENTS` or recent conversation.
- Optional: pointer to the relevant Progress table row to flip (`A1`, `B2`, `C3`, etc.).

The agent edits the plan in place — typically:

- Flips Progress row status (⬜ pending → 🟢 in-progress → ✅ completed) and adds a date/note.
- Appends to "What changed" section with today's date + the user's summary.
- Appends to "Lessons / Conventions to preserve" when a non-obvious decision was made.
- Updates the "next session prompt" section if the next step changes.

<br/>

## Step 2 — Surface the diff (no commit)

Show the user the diff against the plan file (one-screen summary). Do NOT auto-commit the plan change — the user decides when to commit the plan along with whatever the plan tracks.

If the user wants to commit the plan change immediately, delegate to `/commit` (separate cycle).

<br/>

## Hard rules

- `plan-progress-updater` edits `~/.claude/plans/*.md` files **in place**. It does NOT create new plan files (plan-mode owns that), does NOT touch repo code, does NOT run `git commit` / `git push`.
- Plan file write requires user approval per `plan-progress-updater`'s own discipline — this command does not bypass.
- Keep plan edits surgical: progress row + what-changed line + lessons line. Do not rewrite the entire plan unless the user explicitly asks for a restructure.
- Preserve the plan's existing language and tone; a plan is read by the next session, not rewritten for a new audience.

<br/>

## References

- KO pair: `~/.claude/commands-ko/plan-update.md`
- Primary agent: `~/.claude/agents/plan-progress-updater.md`
- Companion: `~/.claude/agents/session-handoff-prompter.md` (for next-session handoff, NOT in-place update) — invoke via `/handoff`
