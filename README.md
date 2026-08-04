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

Docs — `census`: [English](plugins/census/README.md) · [한국어](plugins/census/README-ko.md) · `shell-portability`: [English](plugins/shell-portability/README.md) · [한국어](plugins/shell-portability/README-ko.md) · `session-continuity`: [English](plugins/session-continuity/README.md) · [한국어](plugins/session-continuity/README-ko.md)

<br/>

## Why these exist

Claude Code already distributes plugins well: a marketplace hosts them, `.claude/settings.json` can prompt a whole team to install one, and versions are pinned per entry.

What it does not do is help you get *to* that point. Once your configuration is spread across a user-level `~/.claude/` and a `.claude/` directory in every repo, nothing answers the questions that decide what you can publish:

- What do I actually have, and what does it cost me in context on every single session?
- Is the same agent defined in two places with different content?
- Which of these would work for anyone other than me?

`census` answers those three. It reads; it never moves, edits, or deletes anything.

<br/>

## Repository layout

```
.claude-plugin/marketplace.json   catalog consumed by /plugin marketplace add
plugins/<name>/                   one directory per plugin
  .claude-plugin/plugin.json      plugin manifest (name, version)
  skills/<skill>/SKILL.md         skills, invoked as /<plugin>:<skill>
  agents/<agent>.md               subagents, dispatched by their description
  commands/<command>.md           slash commands, invoked as /<plugin>:<command>
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
