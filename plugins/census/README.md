# census

Read-only audit of scattered Claude Code configuration.

> 한국어 문서는 [README-ko.md](README-ko.md)를 참고하세요.

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

## Usage

Type a skill name in any Claude Code session. The skill runs the bundled script across your configured roots and reads the result back to you — the script scans and renders, the skill interprets.

Every excerpt below comes from one small configuration, shown here so the numbers mean something:

```
~/.claude/                        ~/code/acme-platform/.claude/
  agents/shell-reviewer.md          agents/shell-reviewer.md    ← same name as the global one,
  agents/db-migrator.md             agents/storage-reviewer.md    different content
  commands/ship.md
  skills/changelog/SKILL.md       ~/code/billing-api/.claude/
  hooks/guard.sh                    commands/deploy.md          ← no description
  settings.json
```

<br/>

### `/census:catalog` — what do I have, and what does it cost?

Start here. It is the only one that needs no setup and the only one whose answer you cannot get anywhere else.

```
- Assets: 8 (4 agents, 2 commands, 1 hooks, 1 skills)
- Per-session context: ~65 tokens from the global root, rising to ~105 in the heaviest repo (`acme-platform`)
- Across all roots: ~106 tokens (427 chars) — accumulated total, not a session cost

## Global
### Agents (2)
| Name | Origin | Description |
|---|---|---|
| `db-migrator` | user | Plans and reviews schema migrations before they are applied. |
| `shell-reviewer` | user | Reviews shell scripts for portability between bash and zsh. |
…
## Context budget — top 7 by description size
| Item | Kind | Chars | ~Tokens |
|---|---|---|---|
| `storage-reviewer` | agent | 67 | 16 |
| `shell-reviewer` | agent | 64 | 16 |
```

Read the **per-session** figure, not the all-roots one. The budget table at the bottom is the actionable part: it ranks by `description` length, which is what you actually pay for on every session.

<br/>

### `/census:drift` — where do two things that should agree disagree?

```
- Checked: 7 assets across 7 files, plus 1 hook registrations
- 🔴 2  ·  🟡 0  ·  🟢 0

## 🔴 Behavior differs or routing is broken (2)

**[shadowed]** `shell-reviewer` (agent) exists at both user and project level with different content

Origins: acme-platform, user. A project-level definition overrides the user-level one inside
that repo, so the same name behaves differently depending on where the session is started —
and nothing reports which copy won.

- `~/code/acme-platform/.claude/agents/shell-reviewer.md`
- `~/.claude/agents/shell-reviewer.md`

**[no-description]** `deploy` (command) declares no description
```

🔴 findings are the ones with a consequence you can name — one of two same-named agents silently wins, or an item can never be reached. 🟢 includes confirmations, such as mirrored copies that agree exactly; those are the intended state, not a defect.

<br/>

### `/census:portability` — which of this would work for someone else?

```
- 🟢 PORTABLE: 6  ·  🟡 PARAMETERIZABLE: 1  ·  🔴 PERSONAL: 1
- Share-ready without edits: 6/8 (75%)

Derived markers — strings that identify this machine or owner:

| Marker | Category | Derived from |
|---|---|---|
| `alex` | user | $USER |
| `code` | layout | projectRoots ~/code/* |

Plus 2 repo-scoped markers: `acme-platform`, `billing-api`

## 🟡 PARAMETERIZABLE (1)
**`guard.sh`** — ~/.claude/hooks/guard.sh
- `~/.claude/hooks/guard.sh:3` [code/layout] `code` — case "$1" in "$HOME/code/acme-platform/vendor"/*) exit 1 ;; esac

## 🔴 PERSONAL (1)
**`storage-reviewer`** — ~/code/acme-platform/.claude/agents/storage-reviewer.md
- `…/storage-reviewer.md:3` [frontmatter/repo] `acme-platform` — description: Reviews changes inside acme-platform/storage/ for retention policy.
```

Check the marker table first — every grade below it follows from those strings, so a wrong grade is almost always a missing marker rather than a wrong verdict. Each hit cites a file, a line and where on that line it landed, so you can confirm any verdict without re-reading anything.

<br/>

### On your first run

Two knobs decide whether the first report is signal or noise.

**`projectRoots`** defaults to `["."]` — the current directory only. That gives you your global root plus whatever `.claude/` happens to sit in the directory you ran from, and nothing else. Point it at where your repos actually live (`["~/code/*"]`) to see the rest.

**`pairs`** is empty by default, which switches the whole translation-pair axis off. If you do keep mirrors, name them — `{"agents": "agents-ko"}` — and `/census:drift` starts reporting mirrors that are missing, misshapen, or behind their source. Both settings live in the config file described below.

<br/>

## `/census:catalog` in detail

Collects every agent, command, skill and hook across all configured roots, then reports an inventory grouped by global versus repo-scoped.

Its most useful output is the one nothing else surfaces: **always-on context cost**. The `name` and `description` of every agent, command and skill is resident in *every* session's system prompt — bodies load only on invocation. `claude plugin details` reports that for a single plugin; nothing reports it for your whole configuration.

The cost is reported **per session**, because that is the only version of it anyone pays. A session loads the global root plus the one repo it started in — never every repo at once — so summing all roots describes a session that does not exist. The accumulated total is still shown, labelled as what it is.

Byte-identical copies of one asset across mirrored repos count once. Two files, one thing.

At the scale of a configuration that has been growing for a while, rather than the small example above:

```
- Assets: 126 (80 agents, 34 commands, 7 hooks, 5 skills) — found in 166 files; 40 are identical copies across mirrored repos
- Per-session context: ~10,340 tokens from the global root, rising to ~16,511 in the heaviest repo
- Across all roots: ~30,115 tokens — accumulated total, not a session cost
```

<br/>

## `/census:drift` in detail

Four axes, reported as 🔴 / 🟡 / 🟢.

**Duplicates.** Severity comes from whether the copies *agree*, not from the fact that they are duplicated. Identical copies across a mirrored repo pair are the intended state and are reported as confirmation, not as a defect. Copies that disagree are drift, and a name defined at both user and project level with different content is worse still — the project copy wins inside that repo, so the same name behaves differently depending on where the session started.

**Translation pairs.** Off unless `pairs` names your mirror directories — keeping mirrors is one workflow among many, and a tool that assumes it would report a missing mirror for every item you own.

A mirror normally differs from its source by a constant: a house style might prepend a banner, or render the frontmatter as a fenced block so the file is never loaded as a real definition. Comparing shapes naively reports that convention once per file and buries the real drift.

So the offset is **calibrated per mirror directory** — whatever delta most of a directory's pairs share becomes its baseline, and only files that deviate from their own siblings are reported. No house style is hardcoded, so a convention this tool has never seen calibrates away just the same. Deviation counts in both directions: a file missing the banner its fifteen siblings all have is as much an outlier as one that added a section.

Shape is not the whole story, though. Two files can match structurally while the mirror is months behind, and content cannot settle it — a translation is *supposed* to read differently, so diffing it reports translation as drift. Git history answers it instead: a source committed since its mirror last was means the mirror is behind, in any language. Untracked or uncommitted files get no verdict rather than a wrong one.

**Hooks.** A hook is a pointer, and the file it points at can be absent — the registration stays in `settings.json`, so the hook looks configured while doing nothing. Nothing else reports this.

**Frontmatter.** A missing `description` is the one that actually breaks something — it is the only signal Claude has for when to reach for an item, so without it the item is unreachable unless named outright.

<br/>

## `/census:portability` in detail

Grades every item by how tightly it is bound to one machine. Hooks are graded through the script they run — the registration is only a pointer, and the hardcoded paths live in the target file.

Markers are **derived, never hardcoded** — from `$USER`, from the directory names you invented to organize repos, and from the git remotes of the repos being scanned. On a public forge the account name identifies its owner; on a self-hosted one the "owner" is just a group name and is often a generic word, so the hostname is taken instead. That is what lets the same check flag someone else's identifiers instead of yours.

Each repo's own name is also a marker, but **only within that repo**. A repo-scoped agent usually names its repo by a path relative to it — `Reviews changes inside acme-platform/storage/` — which carries no machine-wide identifier and would otherwise grade as perfectly portable while being one of the least portable items there is. Applied globally a repo called `docs` would match prose everywhere; scoped to its own repo, a match means what it says.

A **user-level** item is the mirror image, and needs the opposite rule. It belongs to no repo, so the rule above never reaches it — yet those are exactly the items written about a named handful of repos (`Runs the migration suite for acme-billing-api`). Every repo `projectRoots` resolves to therefore becomes a marker for user-level items, whether or not that repo carries a `.claude/` of its own: a repo identifies this machine by existing. Only **multi-token** names qualify (`acme-billing-api`, not `docs`), which is what keeps the generic-word trap shut.

<br/>

## Shareable is not the same as portable

A tier describes one file. Shipping something is not a one-file question.

A thin wrapper — a command whose body is *delegate to `some-reviewer`* — contains no machine-specific string at all and grades 🟢 on its own contents, correctly. Publish it alone and you have shipped a name that resolves to nothing. So `census` reads each item for **backticked references to other catalogued items** and reports the ones whose dependency is not itself portable under **⛔ Blocked by a dependency**.

Only items that are 🟢 *and* unblocked count toward *share-ready*. That number is usually much smaller than the 🟢 count, and it is the honest one.

References are matched only inside backticks. Every item that delegates writes the target that way, and requiring the code span is what stops short names like `release` or `scan` from firing on ordinary prose. A missed reference is far cheaper here than an invented one.

There is a second kind of dependency the asset graph cannot name. A helper script is not an agent, command, skill or hook, so nothing in the catalogue represents it — yet a hook that shells out to one, or an agent whose workflow runs one, is just as broken when published alone. Those are reported under **🧩 Calls a script it does not contain**.

Only scripts that **actually exist in a config root** count. An example filename in prose never matches, and comment lines are dropped before a script is read, so a sibling merely *named* in a header comment is not mistaken for a call.

The grade comes from **where** a marker lands, not how many there are:

| Tier | Rule | Remedy |
|---|---|---|
| 🟢 `PORTABLE` | no hits | promote as-is |
| 🟡 `PARAMETERIZABLE` | hits only inside code spans or fenced blocks | swap the literal for a setting |
| 🔴 `PERSONAL` | any hit in frontmatter or prose | rewrite, not configure |

The frontmatter case is the important one. A `description` naming a specific repo is what Claude routes on, so changing it changes *when the item fires*. That is a rewrite, not a setting.

A low portable count is the normal result for mature personal configuration and is not a defect — it means those items were written for a real environment rather than an imagined general one.

> **Blind spot: named infrastructure.** Markers come from remotes, roots and repo names, so nothing supplies the name of a cluster, environment or project codename. An item bound to `prod-eu-1` rather than to a repo will still come back 🟢. Read the 🟢 list before acting on it, and put any such string in `portability.markers`.

> **Blind spot: a dependency named without backticks.** References are only detected inside code spans. An item that says *hand this to the shell reviewer* in plain prose has a real dependency that no report will show. Deliberate — matching prose would invent dependencies far more often than it found them — but it means an unblocked 🟢 is evidence, not proof.

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
| `pairs` | translation mirror directories, keyed by the directory they mirror; empty turns the pair axis off |
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

## Releases

Each plugin in this marketplace is versioned and released on its own. Every change to `census` — with the commits scoped to this directory — is at [census releases](https://github.com/somaz94/claude-plugins/releases?q=census&expanded=true).

<br/>

## License

MIT — see [LICENSE](../../LICENSE).
