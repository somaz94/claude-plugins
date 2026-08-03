---
name: portability
description: Grade every Claude Code agent, command and skill by how tightly it is bound to your machine, and report which ones are safe to share with a team. Use when asked "can I share this agent", "which of my config is portable", "what can I publish as a plugin", or before promoting personal config into a team marketplace.
argument-hint: "[--evidence N] [--out FILE] [--config PATH]"
allowed-tools: Bash, Read
---

# census:portability — what can I actually share?

Derives the strings that identify this machine and this owner, finds every place they appear in your agents / commands / skills, and grades each item by how deeply it is bound to the environment.

This skill is **read-only**. It grades and explains; it never edits, moves, or promotes anything.

<br/>

## Run it

```bash
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/census.py" portability --evidence 3
```

`--evidence 0` drops the per-item citations when you only want the tiers. `--json` emits the raw report. Forward whatever the user supplied in `$ARGUMENTS`.

Report from the script's output — do not re-scan files yourself, and do not re-type its tables.

<br/>

## How markers are derived

Nothing is hardcoded. Markers come from the environment being scanned, which is what lets the same check flag a different person's identifiers:

| Category | Source |
|---|---|
| `user` | `$USER`, home directory name |
| `layout` | directory names in `projectRoots` below home — the scheme the user invented to organize repos |
| `account` | the owner segment of a **public** forge remote, which is a globally unique namespace |
| `host` | a self-hosted forge hostname, plus its registrable label |
| `repo` | each repo's own name, matched **only** against items living in that repo |
| `configured` | anything in `portability.markers` in the config |

On a self-hosted forge the owner is only a group name and is often a generic word (`server`, `infra`), so the host is taken instead.

The `repo` category is scoped rather than global for the same reason. A repo-scoped agent normally names its own repo by a path relative to it — `Reviews changes inside acme-platform/storage/` — which contains no machine-wide identifier at all and would otherwise grade as perfectly portable while being one of the least portable items there is. Applied globally a repo name like `docs` or `tools` would match prose everywhere; scoped to its own repo, a match means what it says.

**The known blind spot: named infrastructure.** Markers are derived from remotes and roots, so nothing supplies the name of a cluster, environment, namespace or project codename. An item whose charter is bound to `prod-eu-1` rather than to a repo will still come back 🟢. When a 🟢 item's description names an environment, say so plainly and tell the user to add that string to `portability.markers` — that is what the key is for. Do not treat the 🟢 list as safe without reading it.

If the marker table looks wrong — something universal listed, or an obvious identifier missing — say so and suggest `portability.markers`, because every downstream grade depends on it.

<br/>

## The tiers

The grade comes from **where** a marker lands, not from how many there are. The rule is mechanical so a reviewer can re-derive it from the evidence:

| Tier | Rule | What it means |
|---|---|---|
| 🟢 `PORTABLE` | no hits | promote as-is |
| 🟡 `PARAMETERIZABLE` | hits only inside code spans or fenced blocks | the coupling is a literal — swap it for a config value or an env var |
| 🔴 `PERSONAL` | any hit in frontmatter or prose | the charter itself assumes this environment; routing or scope would have to be **rewritten**, not configured |

The frontmatter case matters most: a `description` naming a specific repo is what Claude routes on, so changing it changes when the item fires. That is a rewrite, not a setting.

**Hooks are graded through the script they run.** The registration in `settings.json` is only a pointer; the hardcoded paths live in the target file, and reading the registration alone sees none of them. Neither the command nor a shell script has frontmatter, so a hook tops out at 🟡 — a path in a script is always a literal you can lift into a variable. Report which hooks carry the most hits: a guard that only fires under one person's directory layout will silently do nothing for anyone else.

<br/>

## What to report

**1. The share-ready ratio, plainly.** A low number is the normal result for mature personal config and is not a defect — it means the items were written for a real environment rather than an imagined general one. Say that. The number is a starting map for promotion work, not a score to feel bad about.

**2. The 🟢 list in full.** This is the actionable part: these can go into a team plugin today, unchanged.

**3. The 🟡 items worth the effort.** For each, name the specific literal and what it would become — a config key, an env var, an argument. Prefer items whose value is high and whose coupling is one or two literals; skip the ones that would need a dozen substitutions.

**4. Do not propose fixing 🔴 items.** An agent whose scope *is* "the repos under this specific tree" is correctly personal. Recommending it be generalized usually produces a worse, vaguer agent. Say what it is bound to and stop.

<br/>

## Not a secret scanner

This answers "would this work for someone else?", not "would this leak something?". They overlap but neither contains the other:

- A repo layout name is unportable and harmless.
- A password is a leak and perfectly portable.

If the user is about to publish, tell them a portability pass is not a substitute for a secrets scan, and that they need both.

<br/>

## Hard rules

- Read-only. The single permitted write is an explicit `--out` target the user asked for.
- Never edit an item to make it more portable, and never offer to as part of this skill.
- Never write into a scanned tree.
- Do not promote anything into a plugin or marketplace — grading is where this skill stops.
- Do not re-derive markers by hand or second-guess the script's hit detection; if it looks wrong, fix the config, not the report.
