---
name: plans
description: 'Find Claude Code plan files that automatic cleanup is about to delete, durable references to plans that are already gone, and loose Markdown left in workspace roots, then help carry the durable parts somewhere permanent. Claude Code removes top-level plan files older than cleanupPeriodDays during startup housekeeping, so a CLAUDE.md, agent, skill or memory file that points at a plan quietly starts pointing at nothing. Use when asked "my plan file disappeared", "which plans are about to expire", "find references to deleted plans", or "save what matters from my plans". Never edits a plan to keep it alive, and never commits.'
argument-hint: '[report] [--workspace <root>]… [--recover-from <config repo>]'
allowed-tools: Read, Grep, Glob, Bash(python3 ${CLAUDE_PLUGIN_ROOT}/scripts/doctidy.py plans:*), Bash(python3 ${CLAUDE_PLUGIN_ROOT}/scripts/doctidy.py --check-rules:*)
---

# doc-tidy:plans — plans expire; the pointers to them do not

Claude Code keeps plan files in `~/.claude/plans/` (or `$CLAUDE_CONFIG_DIR/plans/`). During startup
housekeeping it deletes the top-level ones whose modification time is older than `cleanupPeriodDays`.
The default is 30 days.

A plan is meant to be disposable, but things outside it are not:
- a `CLAUDE.md` line that says "catalog in `~/.claude/plans/<name>.md`";
- an agent that cites a plan as its source;
- a plan template that links a reference plan;
- a memory note.

All of them keep reading like references after the file is gone. This skill finds them before and
after.

<br/>

## Run it

```bash
python3 ${CLAUDE_PLUGIN_ROOT}/scripts/doctidy.py plans --save <run>/plans.json
```

| Flag | When |
|---|---|
| `--workspace DIR` (repeatable) | also list loose `.md` files sitting in `DIR` itself, outside every repository, with a guess at which repository each one is about |
| `--refs-root DIR` (repeatable) | also search a repository's `CLAUDE.md`, `AGENTS.md` and `.claude/**` for plan references, or those of every non-fork repository under `DIR` |
| `--recover-from REPO[:SUBDIR]` | if you keep your Claude config in git, name that repository: every dangling reference gets the commit that deleted the plan, so it can be restored |
| `--project DIR` | read that project's `.claude/settings*.json` too, for a shorter `cleanupPeriodDays` or a custom `plansDirectory` |
| `--expiring-days N` | how soon counts as expiring (default 7) |

`<run>` is the session scratchpad when one is available, otherwise
`${CLAUDE_PLUGIN_DATA}/runs/plans-<YYYYmmdd-HHMMSS>`.

The script reads only config files and memory notes, never session transcripts.

<br/>

## What to report

**🔴 `dangling-plan-ref`.** References to a plan that no longer exists, grouped by plan. List each
referencing file and line, and the restore command when `recoverable` is present.

**🔴 / 🟡 `plan-expiring`.** A plan within `--expiring-days` of deletion. It is critical when a
durable file points at it.

**🟡 `plan-referenced`.** A live plan that durable files point at. It is fine today, and it becomes
the next dangling reference.

**🟡 `loose-doc`.** A document in a workspace root that belongs to no repository.

Lead with the retention line the script printed: days, and where the value came from. A
surprising source (a project setting shortening it) is itself worth saying.

<br/>

## Judgment

Dispatch the `doc-tidy-triager` agent with lane `plans` and the saved `plans.json`. Validate its
`doc-tidy-verdicts` block before showing anything. Then, for each finding:

- **Expiring plans.**
  - **EXTRACT** copies durable parts (decisions, lessons learned) to where they will last:
    - the user's `CLAUDE.md` for cross-project rules;
    - a repository's `CLAUDE.md` or `docs/` for one project's rules and history;
    - memory for preferences.
    Show every excerpt with its destination before writing.
  - **EXPIRE** means nothing durable is left, and the plan can go.
  - **Never edit a plan's decision text, and never `touch` it to extend its life.** Updating a plan
    for real work resets its clock naturally; doing it to dodge cleanup only hides the problem.
- **Dangling references.** Pick one strategy per deleted plan:
  - **REPLACE** points at the durable successor the agent found.
  - **REMOVE** takes the pointer out of the sentence.
  - **RESTORE** runs `git -C <repo> show <recoverable.show>`. Show the first 20 lines and confirm the
    topic matches before using anything, because plan names are random words. Place the part that
    still matters **inline** at the reference, or at a durable path you name. **Never** restore
    into the top level of the plans directory, where it would expire again.
  - A reference that is only an example becomes a `<plan-name>.md` placeholder.
- **Memory files.** References inside memory notes are fixed only when you opt in **per file**. Keep
  the memory index in step.
- **Loose documents.**
  - MOVE into the repository they are about: they arrive untracked, and the original goes to
    `${CLAUDE_PLUGIN_DATA}/trash/`.
  - Delete after showing the first 20 lines.
  - Leave in place.

Files outside git (your config directory, a workspace root) are copied to `<run>/backup/` before any
edit. `report` stops after the findings and verdicts.

<br/>

## Hard rules

- **Approval is an explicit answer in this conversation.**
- **Only `plans` and `--check-rules` run unasked.** Every edit, move and delete is shown first.
- **Never `rm` a loose document.** It has no git history. Move it to the trash directory.
- **Never commit, push or tag.**
- The retention behaviour described here matches Claude Code at the time of writing. The script
  prints the same caveat; re-check after upgrading Claude Code.

<br/>

## References

- Script: `scripts/doctidy.py` (`plans` subcommand). Agent: `agents/doc-tidy-triager.md`.
- The repository lane: `/doc-tidy:triage`.
- Keeping a live plan current is `session-continuity`'s job. Deciding what outlives a plan is this
  one's.
