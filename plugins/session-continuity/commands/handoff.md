---
description: 'Generate a self-contained handoff prompt for the NEXT Claude Code session, drafted inline following the session-handoff-prompter spec'
argument-hint: "[plan-name | now/mid-session | free-form context]"
allowed-tools: Read, Grep, Glob, Bash
---

# /handoff

Produces a single Markdown code block the user pastes into a fresh chat so the new session can resume complex multi-step work cleanly after context reset.

**Draft it inline, in this session — do NOT delegate to a sub-agent.** The primary input for a handoff is THIS conversation, which a sub-agent cannot see. Delegating both thins the content and renders the block **twice** (the agent's report, then the caller re-printing it). The `session-handoff-prompter` agent shipped with this plugin (`agents/session-handoff-prompter.md`) stays the spec you follow, and stays directly invocable for proactive handoffs outside this command.

User invocation argument: `$ARGUMENTS`

<br/>

## Argument resolution

- empty → ask the user which plan / context to base the handoff on (interactive)
- `<plan-name>` (e.g. `inventory-gap-coverage-cobalt-stargazer`) → use `~/.claude/plans/<plan-name>.md` as the primary source (**`end-of-day` mode**)
- `now` / `mid-session` → **`mid-session` mode**: context is filling up mid-work, hand off to a fresh session while this one stays open. Synthesize the in-flight state from `git` (branch, uncommitted diff, stash, the `file:line` being edited) + this conversation — a plan file is NOT required. Produces the leaner ~30-60 line block.
- free-form text → treat as additional context to weave into the handoff (current blocker, decision the user wants locked in, etc.)

<br/>

## Step 1 — Draft the block

Follow the `session-handoff-prompter` agent definition (`agents/session-handoff-prompter.md`) as the authoring spec — it owns mode selection, the two skeletons, the content checklist, the output style, and the read-only Bash policy. Do not restate those rules here.

Inputs:

- The resolved plan file path (if any) — `Read` it in full.
- Any free-form context from `$ARGUMENTS`.
- **This session's conversation** — what shipped, what was decided, what was interrupted mid-edit. This is the input the spec cannot supply, and the reason this command runs inline instead of delegating.

The block must carry:

1. Plan file reference + "read this first" instruction
2. 1-2 sentence summary of what this session shipped
3. Immediate next action (absolute paths, priorities, decisions already locked in)
4. Hard rules + conventions the new session would not otherwise know
5. The sub-agents the new session should reach for
6. Any `<TBD>` placeholders left behind
7. First command to issue

<br/>

## Step 2 — Output (no execution)

Print a 1-line intro, then **exactly one** fenced block. No trailing commentary, and no second rendering of the block in any form — no restatement, no summary of its contents, no "here it is again".

Do NOT auto-paste into a new session — the user copies it themselves to a fresh chat.

<br/>

## Hard rules

- Read-only — do NOT edit plan files (`plan-progress-updater` owns that), do NOT create new plan files (plan-mode owns that), do NOT execute any of the pending work, no `git commit` / `git push` / any mutating operation.
- Infer the "immediate next action" from the plan's Progress table + What changed section when a plan exists; from live `git` state + this conversation when it does not.
- If a plan file was named but is missing / unreadable, bail with a clear "plan file not found at <path>" message rather than fabricating a handoff.
- The output is meant for **paste into a fresh session** — keep it self-contained (no in-conversation references like "as we discussed earlier").

<br/>

## References

- KO pair: `commands-ko/handoff.md`
- Authoring spec: `agents/session-handoff-prompter.md` — the rules this command follows; also usable directly as a sub-agent for proactive handoffs.
- Companion: `agents/plan-progress-updater.md` (for in-place plan updates, NOT handoff generation) — invoke via `/plan-update`
