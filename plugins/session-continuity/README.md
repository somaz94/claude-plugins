# session-continuity

Carries long-running work across a context reset.

> 한국어 문서는 [README-ko.md](README-ko.md)를 참고하세요.

```bash
/plugin marketplace add somaz94/claude-plugins
/plugin install session-continuity@somaz94
```

<br/>

## Why this exists

Work that spans days does not fit in one context window. The window fills, the session compacts or ends, and the next one starts blind — re-deriving decisions that were already made, re-reading files that were already understood, and sometimes redoing work that was already committed.

The fix is not a longer window. It is writing down the two things a fresh session actually needs: **what was decided** and **what to do next**. This plugin owns both halves.

<br/>

## The two halves

| Skill | When | What it produces |
|---|---|---|
| `/session-continuity:plan-update` | after a meaningful chunk of work lands | the plan file, updated in place — progress flipped, decisions appended, history preserved |
| `/session-continuity:handoff` | when the window is filling, or the day ends | one Markdown block to paste into a fresh session |

`plan-update` keeps the record honest as you go. `handoff` reads that record and turns it into an entry point. Use the first often and the second once, and the next session starts working instead of archaeologising.

<br/>

## The nudge that fires it

The plugin ships a `PreCompact` hook. When the window is about to be compacted — automatically at the limit, or because you ran `/compact` — it says so at that exact moment and offers `/session-continuity:handoff now`.

That moment is the point. Compaction summarizes older turns, so the half-finished edit and the reason behind a decision are precisely what is about to blur. Claude Code exposes no hook that fires at a fixed context percentage, so an imminent compaction is the only "filling up" signal available.

It is advisory: it never cancels a compaction, never opens a session, and never runs the handoff itself. A parse error fails open and stays silent — a nudge is a courtesy, not a boundary.

<br/>

## `/session-continuity:handoff`

Produces a single fenced block — copy once, paste once. It runs in one of two modes.

**Plan-based** (the default): a plan file under `~/.claude/plans/` is the anchor. The prompt opens by telling the new session to read it, then lists the next actions, the decisions already locked in, the sub-agents worth reaching for, and any `<TBD>` the previous session left behind.

**Mid-session**: the window is filling *mid-work* and there may be no plan yet. The prompt leads with the in-flight state instead — branch, the `file:line` left half-edited, uncommitted diff, anything running in the background — plus what is already committed and must not be redone.

The agent never invents a plan path that is not on disk, and never guesses an answer the user has not given: open questions are surfaced as `<needs user input>` rather than resolved on your behalf.

<br/>

## `/session-continuity:plan-update`

Edits a plan file in place after a phase completes, a blocker clears, or scope changes. Progress markers flip, a one-line "what changed" entry is appended, and work products are cited concretely — a link to what shipped beats the word "completed".

Overridden decisions are **appended to, never deleted**. A plan's value to a future session includes the turns it did not take, so a reversed decision gets an `→ **overridden, YYYY-MM-DD**: <reason>` trail rather than a rewrite.

It edits plan files and nothing else — no repo working tree, no `CLAUDE.md`, no agent definitions, no memory files.

<br/>

## Language

Both write in whatever language the plan and your own messages are in. The templates ship in English because something has to be the default; they are a *shape*, not a mandate. A handoff read in a second language is a handoff half-read.

<br/>

## What it never does

- Executes the work itself. `handoff` describes the next action; it does not take it.
- Creates a plan file. That is plan mode's job — these two maintain and read one.
- Commits, pushes, tags, or runs a build.
- Fabricates a decision, a file path, or a plan that is not on disk.

<br/>

## Releases

Each plugin in this marketplace is versioned and released on its own. Every change to `session-continuity` — with the commits scoped to this directory — is at [session-continuity releases](https://github.com/somaz94/claude-plugins/releases?q=session-continuity&expanded=true).

<br/>

## License

MIT — see [LICENSE](../../LICENSE).
