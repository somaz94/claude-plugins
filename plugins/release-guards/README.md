# release-guards

Puts a confirmation in front of the release actions you cannot take back.

> 한국어 문서는 [README-ko.md](README-ko.md)를 참고하세요.

```bash
/plugin marketplace add somaz94/claude-plugins
/plugin install release-guards@somaz94
```

<br/>

## Why this exists

"Only cut a release when I ask for one" is the kind of rule a model agrees to and then, forty tool calls later, quietly steps over — not out of malice, but because tagging looks like the natural next step after a green build.

A convention the model is trusted to remember is not a boundary. A hook is. These two turn the rule into something the harness enforces at the moment it matters, rather than something you discover afterwards in the tag list.

<br/>

## What it gates

| Hook | Fires on | Catches |
|---|---|---|
| `pre-release-action-guard` | `Bash` | `git tag` create or delete, `gh release` / `glab release` create, edit, delete, upload |
| `pre-edit-release-automation-guard` | `Edit` · `Write` · `MultiEdit` | `cliff.toml`, `RELEASE.md`, `release.yml`, `.goreleaser.yaml`, `release-please-config.json` |

The second one matters more than it looks. Release automation is *generated-adjacent*: a `RELEASE.md` that is empty in the repo is normal, because the pipeline rewrites it on every tag push. An assistant reading that file cold sees a bug and fixes it — and silently changes what the next release publishes.

<br/>

## Ask, never block

Both return `ask`. Neither ever returns `deny`, and neither ever exits non-zero to force a refusal.

That distinction is the whole design. Releasing is legitimate — it is *the point* of most of these repos. What is not legitimate is releasing without you having said so. A blocking gate would make the assistant useless for the task; an asking gate just moves the decision back to you and costs one keystroke when the answer is yes.

Read-only commands pass in silence. `git status`, `git log`, and `git tag -l` never prompt.

<br/>

## What it never does

- Blocks, denies, or cancels an action.
- Runs, amends, or rewrites any command it inspected.
- Reaches the network, or touches the repo.

A hook that fails — bad payload, unreadable input — **fails open**: it stays silent and lets the action through. A guard that breaks your workflow when it malfunctions gets switched off, and then it guards nothing.

<br/>

## Releases

Each plugin in this marketplace is versioned and released on its own. Every change to `release-guards` — with the commits scoped to this directory — is at [release-guards releases](https://github.com/somaz94/claude-plugins/releases?q=release-guards&expanded=true).

<br/>

## License

MIT — see [LICENSE](../../LICENSE).
