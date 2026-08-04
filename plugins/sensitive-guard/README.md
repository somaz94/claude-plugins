# sensitive-guard

Stops a secret at the moment of commit.

> 한국어 문서는 [README-ko.md](README-ko.md)를 참고하세요.

```bash
/plugin marketplace add somaz94/claude-plugins
/plugin install sensitive-guard@somaz94
```

<br/>

## What this is, and is not

`gitleaks` and `trufflehog` are better scanners than this one, and you should run them. They run in CI — after the commit exists, after the push, sometimes after the repo went public.

This runs **before `git commit` returns**, inside the session that is writing the code. That is the whole point: not detection depth, but gate placement. The regex set is deliberately small and readable, because a gate you cannot read is a gate you cannot trust at the moment it blocks you.

Never read a clean result here as proof a repository holds no secrets. It is the last mile, not the whole road.

<br/>

## Opt in per repository

Nothing is gated by path. A repository opts in by carrying a `.sensitive-patterns` file at its root:

```
# .sensitive-patterns
internal_marker|acme-corp|acme-internal
personal_email|me@example\.com
real_username_path|/home/rjones\b
```

One `name|regex` per line; blank lines and `#` comments ignored. An empty file is valid — it arms the universal categories and adds nothing.

This is deliberate. One person's "public mirror" directory is another's ordinary checkout, so a guard that fires wherever it guesses it should is a guard that gets uninstalled. And your own markers have to live somewhere anyway, so opting in and configuring are the same act.

A global fallback at `~/.claude/sensitive-patterns` applies to every scan you run by hand.

<br/>

## The two halves

| Piece | When | What it does |
|---|---|---|
| `pre-commit-sensitive-scan` hook | `git commit`, in an opted-in repo | Scans **only the lines the commit adds** and blocks on a match |
| `/sensitive-guard:scan` | on request | Scans a whole repository, or every repo under a root, and reports a verdict |

The hook gating the diff rather than the tree is what makes it usable. A repository with pre-existing findings would otherwise wedge every commit forever, and a gate that always fires gets overridden by reflex.

It fails **open**: an unreadable payload, a missing scanner, a repo it cannot resolve — all allow the commit. A guard that breaks the workflow when it malfunctions gets switched off, and then it guards nothing.

<br/>

## Caught out of the box

Private IP blocks (RFC 5737 documentation ranges are allowed), AWS keys, GitLab PATs and runner and OAuth tokens, GitHub tokens, Slack tokens, OIDC client secrets, generic `password=` / `api_key=` assignments, SSH private keys, and home paths carrying a real username.

Bare `~/.claude` is deliberately **not** flagged: it is Claude Code's own documented path, identical on every machine, so flagging it would fire on every repo that legitimately documents it.

<br/>

## What it never does

- Edits a file to sanitize it. It reports; you decide.
- Commits, pushes, or publishes.
- Reaches the network, except the optional `gh` lookup that skips private repos during `--all`.

<br/>

## Releases

Each plugin in this marketplace is versioned and released on its own. Every change to `sensitive-guard` — with the commits scoped to this directory — is at [sensitive-guard releases](https://github.com/somaz94/claude-plugins/releases?q=sensitive-guard&expanded=true).

<br/>

## License

MIT — see [LICENSE](../../LICENSE).
