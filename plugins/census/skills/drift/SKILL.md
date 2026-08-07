---
name: drift
description: 'Find disagreements across your Claude Code config — the same agent or command defined in several places with different content, translation mirrors that no longer match their source, and frontmatter that would misroute or fail to route at all. Use when asked "check my config for drift", "are my agents out of sync", "did the mirror fall behind", or after syncing config between repos.'
argument-hint: "[--limit N] [--out FILE] [--config PATH]"
allowed-tools: Bash, Read
---

# census:drift — what disagrees with what?

A catalog tells you what exists. This tells you where two things that should agree do not.

This skill is **read-only**. It reports; it never edits, syncs, or reconciles anything.

<br/>

## Run it

```bash
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/census.py" drift --limit 15
```

`--limit 0` shows every finding, `--json` emits the raw report. Forward whatever the user supplied in `$ARGUMENTS`.

Report from the script's output. Do not open the files to re-verify unless a finding is genuinely ambiguous.

<br/>

## The four axes

**Duplicates — one name, several definitions.** Severity comes from whether the copies *agree*, not from the fact that they are duplicated:

| Code | Meaning |
|---|---|
| 🔴 `shadowed` | defined at user level and project level with different content — the project copy wins inside that repo, so the same name behaves differently depending on where the session started |
| 🟡 `mirror-drift` | defined in several repos that disagree — one of them is stale |
| 🟢 `mirror-consistent` | duplicated but byte-identical — the intended state of a mirrored pair, **not** something to fix |

**Pairs — translation mirrors.** `pair-missing` is a real gap. `pair-structure` is more subtle, so read how it is computed before trusting it:

A mirror normally differs from its source by a *constant* — a house style might prepend a translation banner, or render the frontmatter as a fenced block so the file is never loaded as a real definition. Comparing shapes naively reports that convention once per file and drowns the real drift. So the offset is **calibrated per mirror directory**: whatever delta most of a directory's pairs share becomes its baseline, and only files that deviate **from their own siblings** are reported. A `pair-convention` finding names the baseline that was calibrated away.

This means a deviation is meaningful in *both* directions — a file missing the banner its fifteen siblings all have is as much an outlier as one that added a section.

`pair-stale` answers the question shape cannot. Two files can match structurally while the mirror is months behind, and content cannot settle it — a translation is meant to read differently, so diffing it reports translation as drift. History is the honest signal: if the source has been committed since the mirror last was, the mirror is behind, whatever language either side is in. It needs git, so an untracked root or an uncommitted file simply gets no verdict rather than a wrong one.

**Hooks.** A hook is a pointer, so it has one failure the other axes cannot express: the script it points at can be absent. `hook-missing-script` is 🔴 because nothing else reports it — the registration stays in `settings.json`, so the hook looks configured while doing nothing on every matching event. Name the guard the user thinks they have.

**Frontmatter.** `no-description` is the one that actually breaks something: the description is the only signal Claude has for when to reach for an item, so without it the item is unreachable unless named outright. `name-mismatch` and `key-typo` mislead readers or are silently ignored.

<br/>

## What to report

**1. Lead with 🔴, and say what breaks.** Shadowing and missing descriptions have concrete consequences — name them in terms of behavior, not counts.

**2. Group 🟡 by root cause, not by file.** Twenty `pair-structure` findings in one directory is one story ("this directory's translation style is inconsistent"), not twenty. Say which style dominates and which files depart from it.

**3. Treat 🟢 `mirror-consistent` as confirmation.** Identical duplicates mean the mirroring is working. Do not present them as a problem or offer to deduplicate them — for a deliberately mirrored repo pair, collapsing them is the bug.

**4. Point at the owner of the fix, do not fix.** Config drift is usually resolved by whatever sync process created the copies. Name the affected files and let the user decide.

<br/>

## Hard rules

- Read-only. The single permitted write is an explicit `--out` target the user asked for.
- Never edit, sync, copy, or delete a file to resolve a finding, and never offer to as part of this skill.
- Never write into a scanned tree.
- Do not recommend deduplicating `mirror-consistent` items — duplication across a mirrored pair is deliberate.
- Do not judge shareability here — that is `/census:portability`.
- Do not inventory or rank context cost here — that is `/census:catalog`.
