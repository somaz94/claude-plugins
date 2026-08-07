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
By layer: 9 plugin, 11 project, 21 user
Always-on context: ~9,120 tokens (36,481 chars, estimate)
Name conflicts: 1
  - shell-reviewer (agent) in ~/.claude, acme-platform — the project-level definition wins
Hooks pointing at a missing script: 1
Items with no description: 1
Viewer: /tmp/claude-atlas-acme-platform.html
```

The page itself groups every resource by kind, and each row expands to the file's own source — the Markdown Claude Code actually reads, not a paraphrase of it.

- **Search** across name, description, path and body at once.
- **Filter** by kind (commands, agents, skills, hooks, MCP, memory, plugins) and by layer (user, project, plugin).
- **Needs attention** — one toggle that narrows to just the shadowed names, the dead hooks, and the items with no description.
- Every row carries its layer, the plugin that supplied it, and the path it came from.

Light and dark both, following the browser.

<br/>

## The four layers

| Layer | What is read |
|---|---|
| user | `~/.claude/` (or `$CLAUDE_CONFIG_DIR`) — `agents/`, `commands/`, `skills/`, `CLAUDE.md`, and the hooks in `settings.json` / `settings.local.json` |
| project | this repo's `.claude/` — the same shape, plus `CLAUDE.md` and `.mcp.json` at the repo root |
| plugin | every plugin in `~/.claude/plugins/installed_plugins.json`, read at its real install path, including its `hooks/hooks.json` |
| — | which plugins are enabled here, merged from user and project settings with the project winning |

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

> For the same figure across *every* root you own rather than the one project you are standing in, plus drift and portability grading, see [`census`](../census). `atlas` answers "what can this project reach, and what does the page look like"; `census` answers "what have I accumulated everywhere, and where does it disagree with itself".

<br/>

## Running the script directly

The skill wraps one bundled script — python3, **stdlib only**, no install step.

```bash
python3 scripts/atlas.py view --open              # build and open in a browser
python3 scripts/atlas.py view --out setup.html    # write it somewhere specific
python3 scripts/atlas.py view --no-bodies         # index only, much smaller file
python3 scripts/atlas.py --project ~/code/api view
python3 scripts/atlas.py scan                     # the same graph, as JSON
```

| Flag | Default | Meaning |
|---|---|---|
| `--project DIR` | cwd | the repo to map |
| `--user-root DIR` | `$CLAUDE_CONFIG_DIR` or `~/.claude` | the user config directory |
| `--plugins-root DIR` | `<user root>/plugins` | where the plugin registry lives |
| `--out FILE` | `<tmp>/claude-atlas-<project>.html` | where to write |
| `--open` | off | open a browser afterwards |
| `--no-bodies` | off | omit file bodies |
| `--max-body N` | 20000 | per-item body character cap |

The split is deliberate: the script scans and renders, the skill interprets. Reading a hundred-odd files is not model work — it is slow, expensive and non-deterministic. Saying what the result means is.

<br/>

## Where the file lands

The default output is the temp directory, not your repo. A viewer dropped into a working tree is one `git add .` away from being committed, and it names real paths from the machine that produced it.

Pass `--out` to put it anywhere else — and if you keep it in a repo on purpose, add it to `.gitignore`.

Writing into a directory that was scanned is refused outright. A tool that reports on your config must not become part of what it reports.

<br/>

## What it never does

- Edits, moves, or deletes anything it found.
- Writes into a directory it scanned.
- Makes a network request. The viewer has no external reference of any kind.
- Reads MCP secrets into the page. An `env` block is reported by its **variable names only** — never its values.

<br/>

## Releases

Each plugin in this marketplace is versioned and released on its own. Every change to `atlas` — with the commits scoped to this directory — is at [atlas releases](https://github.com/somaz94/claude-plugins/releases?q=atlas&expanded=true).

<br/>

## License

MIT — see [LICENSE](../../LICENSE).
