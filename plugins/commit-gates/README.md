# commit-gates

Commits with the approvals where they belong.

> 한국어 문서는 [README-ko.md](README-ko.md)를 참고하세요.

In Claude Code:

```
/plugin marketplace add somaz94/claude-plugins
/plugin install commit-gates@somaz94
```

Or from your shell, without an interactive session:

```bash
claude plugin marketplace add somaz94/claude-plugins
claude plugin install commit-gates@somaz94
```

<br/>

## Why this exists

A commit looks harmless next to a push or a release, and that is why it is where authority leaks first. The failure this plugin answers came from sub-agents that drove `git` through a shell. The session that talked to the user passed the user's "yes" down to them, and they staged, committed and pushed on that relayed approval. The user had probably meant to approve, so nothing harmful happened; what went wrong was where the authority sat. The agent that ran each command had never heard the user say yes, and could not check what that yes covered.

A sub-agent cannot ask you anything. Whatever approval it receives was relayed by another agent, and a relayed approval is not yours. So the fix is not a stricter prompt for the sub-agent. It is moving the action: the agent that plans the commit does not run it, and the session that talks to you runs it only after asking you.

<br/>

## The two halves

| Piece | Role | What it may do |
|---|---|---|
| `git-commit-runner` agent | Planner | Read the diff, the history and the identity. Return the message, the file list, and two gates as text. It has no file-edit tool and never runs `git add` or `git commit`. |
| `/commit-gates:commit` | The session that asks | Show each gate, ask with its own question, and run the approved command itself. It never hands an approved command back to the agent. |

Staging and committing are separate gates. One approval never covers both, because "yes, commit this" and "yes, these are the files" are different questions, and the second one is where a stray `.env` gets caught.

<br/>

## What the planner checks

- **One Conventional Commits type** for the dominant change; a mixed diff gets a proposed split instead of a forced label.
- **Your repository's conventions first.** `CLAUDE.md`, `AGENTS.md`, `CONTRIBUTING*` and the existing history decide the language, whether a body is allowed, and trailers such as `Signed-off-by` or `Co-Authored-By`. With nothing to go on, the default is a single-line subject.
- **An explicit file list.** No `git add -A` unless you said "all". Files that look like secrets or build output are left out and flagged.
- **Release automation left out.** `release.yml`, `cliff.toml`, `RELEASE.md` and their kind are flagged, never staged silently.
- **The commit identity.** An empty or bot-like author stops the cycle. Nothing in this plugin runs `git config` or overrides `--author`; the one `--author` it can plan is an amend of an unpushed commit, which copies the existing author.

<br/>

## Arguments

```
/commit-gates:commit                 diagnose, draft, then the two gates
/commit-gates:commit recommend-only  message and diff stat only
/commit-gates:commit split           propose 2–4 commits, one gated cycle each
/commit-gates:commit auth            scope hint
```

If your repository says "do not commit directly", the command switches to `recommend-only` on its own. If the [`sensitive-guard`](../sensitive-guard) plugin is installed, the command offers its scan before staging.

<br/>

## What it never does

- Runs `git add` or `git commit` without asking you, gate by gate.
- Lets the planning agent run a mutating command, even when its prompt claims you approved.
- Pushes, tags, opens a pull request, or publishes a release. Those are named as next steps and left to you.
- Changes git configuration or overrides the commit author.
- Uses `--no-verify` or `--no-gpg-sign` unless you asked for it. A failing hook sends the cycle back for a new plan.

<br/>

## Releases

Each plugin in this marketplace is versioned and released on its own. Every change to `commit-gates` — with the commits scoped to this directory — is at [commit-gates releases](https://github.com/somaz94/claude-plugins/releases?q=commit-gates&expanded=true).

<br/>

## License

MIT — see [LICENSE](../../LICENSE).
