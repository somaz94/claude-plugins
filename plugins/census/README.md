# census

Read-only audit of scattered Claude Code configuration.

Once you have a user-level `~/.claude/` plus a `.claude/` directory in every repo, three questions get hard to answer and nothing built in answers them. `census` answers them and changes nothing.

```bash
/plugin marketplace add somaz94/claude-plugins
/plugin install census@somaz94
```

<br/>

## Skills

| Skill | Question it answers |
|---|---|
| `/census:catalog` | What do I have, and what does it cost me in context on every session? |
| `/census:drift` | Where do two things that should agree disagree? |
| `/census:portability` | Which of this would work for someone who is not me? |

<br/>

## `/census:catalog`

Collects every agent, command, skill and hook across all configured roots, then reports an inventory grouped by global versus repo-scoped.

Its most useful output is the one nothing else surfaces: **always-on context cost**. The `name` and `description` of every agent, command and skill is resident in *every* session's system prompt — bodies load only on invocation. `claude plugin details` reports that for a single plugin; nothing reports it for your whole configuration.

The cost is reported **per session**, because that is the only version of it anyone pays. A session loads the global root plus the one repo it started in — never every repo at once — so summing all roots describes a session that does not exist. The accumulated total is still shown, labelled as what it is.

Byte-identical copies of one asset across mirrored repos count once. Two files, one thing.

```
- Assets: 126 (80 agents, 34 commands, 7 hooks, 5 skills) — found in 166 files; 40 are identical copies across mirrored repos
- Per-session context: ~10,340 tokens from the global root, rising to ~16,511 in the heaviest repo
- Across all roots: ~30,115 tokens — accumulated total, not a session cost
```

<br/>

## `/census:drift`

Three axes, reported as 🔴 / 🟡 / 🟢.

**Duplicates.** Severity comes from whether the copies *agree*, not from the fact that they are duplicated. Identical copies across a mirrored repo pair are the intended state and are reported as confirmation, not as a defect. Copies that disagree are drift, and a name defined at both user and project level with different content is worse still — the project copy wins inside that repo, so the same name behaves differently depending on where the session started.

**Translation pairs.** A mirror normally differs from its source by a constant: a house style might prepend a banner, or render the frontmatter as a fenced block so the file is never loaded as a real definition. Comparing shapes naively reports that convention once per file and buries the real drift.

So the offset is **calibrated per mirror directory** — whatever delta most of a directory's pairs share becomes its baseline, and only files that deviate from their own siblings are reported. No house style is hardcoded, so a convention this tool has never seen calibrates away just the same. Deviation counts in both directions: a file missing the banner its fifteen siblings all have is as much an outlier as one that added a section.

**Frontmatter.** A missing `description` is the one that actually breaks something — it is the only signal Claude has for when to reach for an item, so without it the item is unreachable unless named outright.

<br/>

## `/census:portability`

Grades every item by how tightly it is bound to one machine.

Markers are **derived, never hardcoded** — from `$USER`, from the directory names you invented to organize repos, and from the git remotes of the repos being scanned. On a public forge the account name identifies its owner; on a self-hosted one the "owner" is just a group name and is often a generic word, so the hostname is taken instead. That is what lets the same check flag someone else's identifiers instead of yours.

Each repo's own name is also a marker, but **only within that repo**. A repo-scoped agent usually names its repo by a path relative to it — `Reviews changes inside acme-platform/storage/` — which carries no machine-wide identifier and would otherwise grade as perfectly portable while being one of the least portable items there is. Applied globally a repo called `docs` would match prose everywhere; scoped to its own repo, a match means what it says.

The grade comes from **where** a marker lands, not how many there are:

| Tier | Rule | Remedy |
|---|---|---|
| 🟢 `PORTABLE` | no hits | promote as-is |
| 🟡 `PARAMETERIZABLE` | hits only inside code spans or fenced blocks | swap the literal for a setting |
| 🔴 `PERSONAL` | any hit in frontmatter or prose | rewrite, not configure |

The frontmatter case is the important one. A `description` naming a specific repo is what Claude routes on, so changing it changes *when the item fires*. That is a rewrite, not a setting.

A low portable count is the normal result for mature personal configuration and is not a defect — it means those items were written for a real environment rather than an imagined general one.

> **Blind spot: named infrastructure.** Markers come from remotes and roots, so nothing supplies the name of a cluster, environment or project codename. An item bound to `prod-eu-1` rather than to a repo will still come back 🟢. Read the 🟢 list before acting on it, and put any such string in `portability.markers`.

> **Not a secret scanner.** This answers "would this work for someone else?", not "would this leak something?". A repo layout name is unportable and harmless; a password is a leak and perfectly portable. If you are about to publish, you need both checks.

<br/>

## Configuration

Optional. With no config, `census` scans `~/.claude` and the current directory's `.claude/`.

Resolution order, first hit wins outright — the files are not merged:

1. `./.census.json` or `./census.json`
2. `census.json` in your user-level Claude config directory
3. built-in defaults

```json
{
  "userRoots": ["~/.claude"],
  "projectRoots": ["~/code/*", "~/work/*"],
  "exclude": ["archived-*"],
  "excludeOssForks": true,
  "pairs": { "agents": "agents-ko", "commands": "commands-ko", "skills": "skills-ko" },
  "portability": { "markers": [] }
}
```

| Key | Meaning |
|---|---|
| `userRoots` | directories that **are** a Claude config dir |
| `projectRoots` | globs of **repo** directories whose `.claude/` should be scanned |
| `exclude` | repo basename patterns to skip |
| `excludeOssForks` | skip repos with an `upstream` remote — a fork ships its maintainers' `.claude/`, not yours |
| `pairs` | translation mirror directories, keyed by the directory they mirror |
| `portability.markers` | extra identifying strings; leave empty to derive them |

A generated config or catalog names real paths on the machine that produced it. Keep `.census.json` and any `--out` file out of published repositories.

<br/>

## Running the scripts directly

Every skill wraps one bundled script, which is python3 **stdlib only** — no install step, no dependencies.

```bash
python3 scripts/census.py catalog --top 10
python3 scripts/census.py drift --limit 15
python3 scripts/census.py portability --evidence 3
python3 scripts/census.py scan          # normalized asset graph as JSON
```

`--out FILE` writes to a file, `--json` emits the raw report, `--config PATH` selects a config.

The split is deliberate: the script scans and renders, the skill interprets. Reading a hundred-odd files is not model work — it is slow, expensive, and non-deterministic. Judging what the numbers mean is.

<br/>

## What it never does

- Writes into a scanned tree. The only file it writes is an explicit `--out` target.
- Edits, moves, syncs or deletes any item it found.
- Promotes anything into a plugin or marketplace.

<br/>

## License

MIT — see [LICENSE](../../LICENSE).
