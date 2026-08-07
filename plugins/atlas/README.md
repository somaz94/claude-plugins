# atlas

A browsable map of every Claude Code resource a project can reach.

> 한국어 문서는 [README-ko.md](README-ko.md)를 참고하세요.

A session resolves four layers at once — your user config, this repo's `.claude/`, every installed plugin, and the hooks registered by the settings files in between. Nothing shows you all four together. `/help` lists commands without saying where they came from, `claude plugin details` covers one plugin, and after a `/plugin install` there is no answer at all to *what did that just add here*.

```bash
/plugin marketplace add somaz94/claude-plugins
/plugin install atlas@somaz94
```

Then, in any session:

```
/atlas:view
```

<br/>

## What it produces

One self-contained HTML file, opened in your browser. No server, no CDN, no build step — every byte of CSS and JS is inline, so it works offline and can be handed to a colleague as a single attachment.

```
Project: ~/code/acme-platform
Resources: 41 (12 commands, 18 agents, 5 skills, 4 hooks, 1 MCP servers, 1 memory files)
By scope: Global 21 (~5,100t) · Project 11 (~3,240t) · Plugins 9 (~780t)
Always-on context: ~9,120 tokens (36,481 chars, estimate)
Name conflicts: 1
  - shell-reviewer (agent) in ~/.claude, acme-platform — the project-level definition wins
Hooks pointing at a missing script: 1
Items with no description: 1
Viewer: /tmp/claude-atlas-acme-platform.html
Removed stale viewer: /tmp/claude-atlas-old-project.html
```

The page groups every resource by **where it came from** — Global, Project, Plugins — with each scope carrying its own share of the always-on bill. Each row expands to the file's own source: the Markdown Claude Code actually reads, not a paraphrase of it.

- **Search** across name, description, path and body at once — and across every translation, so a Korean term finds an item whose source description is English.
- **Group** by scope or by kind, one toggle. Scope answers *what does this repo add on top of my global config*; kind answers *what commands do I have here*.
- **Filter** by kind (commands, agents, skills, hooks, MCP, memory, plugins) and by scope (Global, Project, Plugins).
- **Needs attention** — one toggle that narrows to just the shadowed names, the dead hooks, and the items with no description.
- **Language** — switch the whole page between the source and any translation mirror you keep. See below.
- Every row leads with **where it lives** — the repo name for a repo-scoped item, the config directory for a global one, the plugin for a plugin's — then the always-on cost of its description. Grouped by kind it carries its scope as a chip too; grouped by scope the heading above already says it.

Light and dark both, following the browser.

<br/>

## Reading what is on disk

Every item is Markdown, and the viewer renders it as Markdown — headings, tables, lists, blockquotes, fenced code, and the `` `backticks` `` that fill a description. The `raw source` toggle in the toolbar switches the whole page back to the unrendered text when you want to see exactly what the file says, byte for byte.

Rendering means the page now interprets text it did not write, so the rule is absolute: **a document can never contribute markup.** Everything is escaped first and only then formatted, a `<script>` in an agent body renders as the visible text `<script>`, and a link becomes its text plus a muted target rather than a live anchor — this page promises no external reference, and an `href` is one. A CI check lifts the renderer out of the built page and runs it against hostile input to keep that true.

A `description` is a single unbroken line on disk, and a mature one runs past two thousand characters. So the viewer also inserts a line break at each sentence end — the only reformatting it does to the text itself — and clamps the collapsed row to three lines. Open the row to read the whole thing.

Each routable row also carries what its description costs: `1,329c · ~332t always on`. That is the number to look at when one row is visibly longer than its neighbours.

<br/>

## Translation mirrors

If you keep translations as sibling directories — `agents-ko` next to `agents`, `commands-ja` next to `commands` — atlas pairs them up and the viewer gets a language selector. Any two- or three-letter suffix works; there is no list of known languages to be added to.

Pairing is **by path, never by the `name:` field**. A mirror whose frontmatter name was translated too would otherwise fail to pair with the file it is plainly a translation of.

Two things follow from how Claude Code actually loads config, and the viewer reflects both:

- A mirror is **not a second resource**. Only the source directory is loaded, so the mirror is attached to the item it translates rather than counted as another agent — the resource count and the context figure stay honest.
- A mirror therefore **costs nothing at runtime**. Trimming a bloated Korean description saves zero tokens; it is worth doing to keep the pair aligned, not to buy back context.

Switch to a language and any item that was *expected* to have a mirror but does not is tagged `no ko mirror`. Expected means the directory it came from keeps mirrors in that language at all — a plugin that keeps no translations is not incomplete, so it is never tagged.

<br/>

## The four layers

| Layer | What is read |
|---|---|
| user | `~/.claude/` (or `$CLAUDE_CONFIG_DIR`) — `agents/`, `commands/`, `skills/`, `CLAUDE.md`, and the hooks in `settings.json` / `settings.local.json` |
| project | this repo's `.claude/` — the same shape, plus `CLAUDE.md` and `.mcp.json` at the repo root |
| plugin | every plugin in `~/.claude/plugins/installed_plugins.json`, read at its real install path, including its `hooks/hooks.json` |
| — | which plugins are enabled here, merged from user and project settings with the project winning |

The viewer calls the `user` layer **Global**, which is what it is next to a repo's own `.claude/`. The JSON from `scan` keeps `"layer": "user"` — the key Claude Code's own documentation uses — so nothing downstream has to change.

A command in a subdirectory is reported by the name you actually type — `commands/git/ship.md` is `/git:ship` — and a plugin's commands and skills carry their plugin namespace, `/atlas:view`. That distinction is not cosmetic: it is exactly why a plugin's command can never collide with one of yours, and why an agent can.

<br/>

## The three things it points at

Most of the page is an index. These three are findings.

**A name that resolves to more than one definition.** Agents share one flat namespace across all three layers. Define `shell-reviewer` at user level and again in a repo, and inside that repo the project copy wins — the same name behaves differently depending on where you started the session, and nothing reports which copy answered. Commands and skills from a plugin are namespaced and cannot collide, so they are never reported here.

Where precedence is documented, the winner is named. Where it is not — a plugin agent sharing a name with yours — it says *ambiguous* rather than guessing. A confident wrong answer is worse than an honest one.

**A hook pointing at a script that is not there.** A hook registration is a pointer. Delete or rename the script and the registration stays valid-looking in `settings.json`: it loads, it never fires, and nothing tells you. Each hook row resolves its target — expanding `${CLAUDE_PLUGIN_ROOT}` and `$HOME` the way the runtime does — and says whether the file exists.

An inline hook is not a missing script. `jq -r .tool_name` has no file to find, and is reported as what it is rather than as a defect.

**An item with no description.** The description is the only signal Claude has for when to reach for something. Without one the item is installed, listed, and unreachable unless you name it outright.

<br/>

## Always-on context

The `name` and `description` of every command, agent and skill sits in the system prompt of *every* session; bodies load only when something is invoked. Every `CLAUDE.md` in scope is resident in full. That sum is what a session costs before you type anything, and it is reported per project, because that is the only version of it you ever pay.

It is an estimate — 4 characters per token — and it is labelled as one. Under ~5k tokens is unremarkable. Past ~25k a meaningful slice of every session is spent before work starts, and a `CLAUDE.md` that has been growing for a year is usually most of it.

The figure is also broken out per scope — in the summary cards, on every scope heading, and on the `By scope:` line the script prints. That is the actionable form. One total tells you the bill is large; the split tells you which directory to go open, and whether the weight is something you carry into every repository or something this one repository added.

> For the same figure across *every* root you own rather than the one project you are standing in, plus drift and portability grading, see [`census`](../census). `atlas` answers "what can this project reach, and what does the page look like"; `census` answers "what have I accumulated everywhere, and where does it disagree with itself".

<br/>

## Comparing projects

Name another project and `view` produces three files instead of one:

```
/atlas:view acme-platform
```

1. the session project you are standing in
2. the **global layer on its own** — your user config and installed plugins, with no project layer scanned at all
3. the project you named

The middle one is the point. Two project maps each contain the whole global layer, so setting them side by side mostly shows you what they have in common. The global-only map is the shared term you subtract to see what each repository actually adds on top. Name as many projects as you like; each gets its own file, and the global map is still produced once.

A bare name is looked for next to the session project first — sibling checkouts are how repositories are usually laid out — then under your home directory. A path is used exactly as given.

<br/>

## Running the script directly

The skill wraps one bundled script — python3, **stdlib only**, no install step.

```bash
python3 scripts/atlas.py view --open              # build and open in a browser
python3 scripts/atlas.py view --out setup.html    # write it somewhere specific
python3 scripts/atlas.py view --no-bodies         # index only, much smaller file
python3 scripts/atlas.py view ~/code/api          # three maps: here, global, and that repo
python3 scripts/atlas.py --project ~/code/api view
python3 scripts/atlas.py scan                     # the same graph, as JSON
```

| Flag | Default | Meaning |
|---|---|---|
| `PROJECT …` | — | `view` only — extra projects to map; naming any of them also emits a global-only map |
| `--project DIR` | cwd | the repo to map |
| `--user-root DIR` | `$CLAUDE_CONFIG_DIR` or `~/.claude` | the user config directory |
| `--plugins-root DIR` | `<user root>/plugins` | where the plugin registry lives |
| `--out FILE` | `<tmp>/claude-atlas-<subject>.html` | where to write — a **directory** when more than one map is produced |
| `--open` | off | open a browser afterwards |
| `--no-bodies` | off | omit file bodies |
| `--max-body N` | 20000 | per-item body character cap |
| `--keep-old` | off | keep viewers from earlier runs instead of clearing atlas's own stale files out of the temp directory |

The split is deliberate: the script scans and renders, the skill interprets. Reading a hundred-odd files is not model work — it is slow, expensive and non-deterministic. Saying what the result means is.

<br/>

## Where the file lands

The default output is the temp directory, not your repo. A viewer dropped into a working tree is one `git add .` away from being committed, and it names real paths from the machine that produced it.

Pass `--out` to put it anywhere else — and if you keep it in a repo on purpose, add it to `.gitignore`. When more than one map is produced, `--out` is the directory they go into rather than a file name.

Writing into a directory that was scanned is refused outright. A tool that reports on your config must not become part of what it reports.

The file is named after its subject, not the time it was made, so re-running in the same project overwrites rather than piling up. What did pile up was one multi-megabyte file per project you had ever mapped, sitting in a directory you never open — so each run also clears out the viewers earlier runs left there. It is deliberately narrow: only the temp directory, only files matching atlas's own name, only after reading atlas's own title back out of the file, and never one just written. `--keep-old` turns it off.

<br/>

## What it never does

- Edits, moves, or deletes anything it found. It does delete its own earlier viewers out of the temp directory — its own output, never yours.
- Writes into a directory it scanned.
- Makes a network request. The viewer has no external reference of any kind.
- Reads MCP secrets into the page. An `env` block is reported by its **variable names only** — never its values.

<br/>

## Releases

Each plugin in this marketplace is versioned and released on its own. Every change to `atlas` — with the commits scoped to this directory — is at [atlas releases](https://github.com/somaz94/claude-plugins/releases?q=atlas&expanded=true).

<br/>

## License

MIT — see [LICENSE](../../LICENSE).
