# claude-plugins

A [Claude Code](https://code.claude.com/docs) plugin marketplace.

```bash
/plugin marketplace add somaz94/claude-plugins
/plugin install census@somaz94
```

<br/>

## Plugins

| Plugin | What it does |
|---|---|
| [`census`](plugins/census) | Read-only audit of scattered `.claude/` configs — catalog what you have, detect drift, and triage which items are portable enough to share with a team |
| [`shell-portability`](plugins/shell-portability) | Reviews shell scripts for bash/zsh portability — the axis `shellcheck` does not cover, because it checks the shell the shebang declares, not the one the script gets run by |
| [`session-continuity`](plugins/session-continuity) | Carries long-running work across a context reset — keeps a plan file current as you go, and generates the handoff prompt a fresh session starts from |
| [`release-guards`](plugins/release-guards) | Puts a confirmation in front of the release actions you cannot take back — tag create/delete, release publish, and edits to the automation that generates them |
| [`sensitive-guard`](plugins/sensitive-guard) | Stops a secret at the moment of commit — a last-mile gate over the lines a commit adds, plus an on-demand scan before you publish |

Docs — `census`: [English](plugins/census/README.md) · [한국어](plugins/census/README-ko.md) · `shell-portability`: [English](plugins/shell-portability/README.md) · [한국어](plugins/shell-portability/README-ko.md) · `session-continuity`: [English](plugins/session-continuity/README.md) · [한국어](plugins/session-continuity/README-ko.md) · `release-guards`: [English](plugins/release-guards/README.md) · [한국어](plugins/release-guards/README-ko.md) · `sensitive-guard`: [English](plugins/sensitive-guard/README.md) · [한국어](plugins/sensitive-guard/README-ko.md)

<br/>

## Why these exist

Every plugin here addresses something that goes wrong **quietly** while an assistant works at speed. Not the failures that stop you — those announce themselves. The ones that pass every check and are wrong anyway:

- Your config drifts. The same agent exists twice with different content, and nothing reports which copy won. — [`census`](plugins/census)
- A script runs under the other shell. `shellcheck` cleared it, because it checked the shell the shebang declares, not the one it got run by. — [`shell-portability`](plugins/shell-portability)
- The context window compacts. Older turns are summarized, and the half-finished edit and the reason behind a decision blur away. — [`session-continuity`](plugins/session-continuity)
- A tag gets pushed because it looked like the next step after a green build. — [`release-guards`](plugins/release-guards)
- A secret rides along in a diff nobody re-read. — [`sensitive-guard`](plugins/sensitive-guard)

Each is a gate or a report at the point the mistake is cheap to catch, not a summary delivered after it landed. Where a better tool already exists — `gitleaks` for secret detection, `shellcheck` for shell linting — these do not replace it; they run where it does not, which is inside the session, before the commit returns.

Nothing here edits your work without asking. `census` is read-only by contract, the guards ask rather than block, and every hook fails open: a guard that breaks the workflow when it malfunctions gets switched off, and then it guards nothing.

<br/>

## Repository layout

```
.claude-plugin/marketplace.json   catalog consumed by /plugin marketplace add
plugins/<name>/                   one directory per plugin
  .claude-plugin/plugin.json      plugin manifest (name, version)
  skills/<skill>/SKILL.md         skills, invoked as /<plugin>:<skill>
  agents/<agent>.md               subagents, dispatched by their description
  commands/<command>.md           slash commands, invoked as /<plugin>:<command>
  hooks/hooks.json                hook registrations, rooted at ${CLAUDE_PLUGIN_ROOT}
  scripts/                        bundled executables, referenced via ${CLAUDE_PLUGIN_ROOT}
```

Every plugin here is versioned in **two** places that must agree — its own `plugin.json` and its entry in `marketplace.json`. The marketplace entry is the version users actually receive, so CI fails the build when the two disagree.

Plugins version **independently**, and so do their releases: a tag is `<plugin>-v<X.Y.Z>` — `census-v0.3.1`, `shell-portability-v0.1.0`. Pushing one releases that plugin alone, with notes built from the commits under its own directory since its own previous tag. A repo-wide tag would drag every plugin's version up whenever any one of them shipped. Tags of the retired repo-wide form (`v0.3.0` and earlier) remain in history.

<br/>

## Development

```bash
claude --plugin-dir ./plugins/census    # load a plugin without installing it
claude plugin validate .                # validate the marketplace
claude plugin validate ./plugins/census # validate one plugin
```

Run `/reload-plugins` inside a session to pick up edits without restarting.

<br/>

## License

MIT — see [LICENSE](LICENSE).
