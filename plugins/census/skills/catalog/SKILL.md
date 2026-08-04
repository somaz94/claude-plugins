---
name: catalog
description: Take a full inventory of every Claude Code agent, command, skill and hook across your user-level ~/.claude and all project-level .claude/ directories, then report what you have and what it costs you in always-on context. Use when asked to "catalog my config", "what agents do I have", "inventory my .claude", or "why is my context so full".
argument-hint: "[--out FILE] [--top N] [--config PATH]"
allowed-tools: Bash, Read
---

# census:catalog — what do I actually have?

Scans every configured Claude config root, normalizes each agent / command / skill / hook into one asset graph, and renders a catalog from it.

This skill is **read-only**. It never writes into a scanned tree, never edits a found item, and never removes anything.

<br/>

## Run it

```bash
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/census.py" catalog --top 10
```

Pass `--out CENSUS.md` to write the catalog to a file instead of stdout, and `--config PATH` to point at a specific config. Forward whatever the user supplied in `$ARGUMENTS`.

The script does the scanning and the table rendering. Your job starts after it returns: **do not re-type the catalog into your reply.** Point at it, then interpret it.

<br/>

## Configuration

Config is resolved in this order, first hit wins outright (no merging):

1. `./.census.json` or `./census.json`
2. `~/.claude/.census.json` or `~/.claude/census.json`
3. Built-in defaults — `userRoots: ["~/.claude"]`, `projectRoots: ["."]`

If the run only found one root and the user clearly has more (they mention other repos, or the count looks small), tell them to create `~/.claude/census.json`:

```json
{
  "userRoots": ["~/.claude"],
  "projectRoots": ["~/code/*", "~/work/*"],
  "exclude": ["archived-*"],
  "excludeOssForks": true
}
```

`excludeOssForks` skips any repo with an `upstream` git remote — a forked repo ships its maintainers' `.claude/`, not yours, and would pollute the catalog.

<br/>

## What to report

Lead with the numbers the script printed, then add these four readings. Keep it short; the catalog itself carries the detail.

**1. Context budget — the headline.** The `name` + `description` of every agent, command and skill is resident in *every* session's system prompt; bodies load only on invocation. This is the one number nothing else in the ecosystem surfaces (`claude plugin details` covers a single plugin, not your whole config).

The script reports it two ways, and the difference matters:

- **Per-session** — the global root, plus the one repo the session started in. This is what a session actually pays, and it is the number to calibrate against. The report also names the heaviest repo, which is the worst case.
- **Across all roots** — everything found anywhere. Nobody ever pays this in one session; it answers "how much config have I accumulated", not "what is this costing me". Never quote it as a session cost.

Calibration, against the **per-session** figure: under ~5k tokens is unremarkable, ~10k is worth a trim pass, past ~25k a meaningful slice of every session is spent before the user types anything. Name the top offenders and say what a trim would buy — but recommend, never edit.

**2. Duplicate names.** Byte-identical copies of one asset across mirrored repos are already folded into a single row naming both origins — that is a mirror working, not a finding. What is worth surfacing is a name that resolves to *different* content depending on where the session started; the loser is silently shadowed. Say so and hand off to `/census:drift`, which owns that diagnosis — do not attempt the comparison here.

**3. Skipped repos.** The script lists what it excluded and why. Scan for anything skipped by accident, especially a repo excluded by pattern that the user actually wanted counted.

**4. Scope split.** Global (`~/.claude/`) versus repo-scoped tells you whether config is centralized or scattered. A large repo-scoped population with the same items repeated across repos is a promotion candidate — surface it, and let `/census:portability` decide whether promotion is actually safe.

<br/>

## Hard rules

- Read-only. The single permitted write is an explicit `--out` target the user asked for.
- Never edit, move, or delete a discovered item, and never offer to as part of this skill.
- Never write into a scanned tree. If the user asks for `--out`, default to the current directory, not a config root.
- Do not paste the whole catalog back. Reference the output; interpret it.
- Do not judge shareability here — that is `/census:portability`.
- Do not diff EN/KO or mirror pairs here — that is `/census:drift`.
