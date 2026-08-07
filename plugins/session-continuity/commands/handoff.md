---
description: 'Generate a self-contained handoff prompt for the NEXT Claude Code session via session-handoff-prompter'
argument-hint: "[plan-name | now/mid-session | free-form context]"
allowed-tools: Read, Grep, Glob, Bash
---

# /handoff

Thin wrapper around the `session-handoff-prompter` agent. Produces a single Markdown code block the user pastes into a fresh chat so the new session can resume complex multi-step work cleanly after context reset.

User invocation argument: `$ARGUMENTS`

<br/>

## Argument resolution

- empty → ask the user which plan / context to base the handoff on (interactive)
- `<plan-name>` (e.g. `inventory-gap-coverage-cobalt-stargazer`) → use `~/.claude/plans/<plan-name>.md` as the primary source (**`end-of-day` mode**)
- `now` / `mid-session` → **`mid-session` mode**: context is filling up mid-work, hand off to a fresh session while this one stays open. The agent synthesizes the in-flight state from `git` (branch, uncommitted diff, stash, the `file:line` being edited) + conversation — a plan file is NOT required. Produces the leaner ~30-60 line block.
- free-form text → treat as additional context the agent should weave into the handoff (current blocker, decision the user wants locked in, etc.)

<br/>

## Step 1 — Delegate to `session-handoff-prompter`

Invoke the agent with:

- The resolved plan file path (if any) — agent reads it directly.
- Any free-form context from `$ARGUMENTS`.
- The user's last 2-3 turns of conversation context (let the agent infer the immediate next action).

The agent produces a single markdown code block with:

1. Plan file reference + "read this first" instruction
2. 1-2 sentence summary of what the previous session shipped
3. Immediate next action (absolute paths, priorities, decisions already locked in)
4. Hard rules + conventions the new session would not otherwise know
5. The sub-agents the new session should reach for
6. Any `<TBD>` placeholders left behind
7. First command to issue

<br/>

## Step 2 — Surface (no execution)

The agent's output is the handoff prompt itself. **Show it verbatim** so the user can copy-paste in one go.

Do NOT auto-paste into a new session — the user copies it themselves to a fresh chat.

<br/>

## Hard rules

- Read-only — `session-handoff-prompter` does NOT edit plan files (`plan-progress-updater` owns that), does NOT create new plan files (plan-mode owns that), does NOT execute any work, does NOT `git commit` / `git push` / any mutating operation.
- The agent infers "immediate next action" from the plan's Progress table + What changed section — do not duplicate that work yourself.
- If the plan file is missing / unreadable, the agent should bail with a clear "plan file not found at <path>" message rather than fabricating a handoff.
- The output is meant for **paste into a fresh session** — keep it self-contained (no in-conversation references like "as we discussed earlier").

<br/>

## References

- KO pair: `~/.claude/commands-ko/handoff.md`
- Primary agent: `~/.claude/agents/session-handoff-prompter.md`
- Companion: `~/.claude/agents/plan-progress-updater.md` (for in-place plan updates, NOT handoff generation)
