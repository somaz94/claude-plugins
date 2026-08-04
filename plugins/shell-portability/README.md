# shell-portability

Reviews shell scripts for one thing: whether they run correctly under **both bash and zsh**.

```bash
/plugin marketplace add somaz94/claude-plugins
/plugin install shell-portability@somaz94
```

<br/>

## Why this exists

`shellcheck` is excellent and you should keep using it. It checks a script against the shell its shebang declares. What it does not check is the shell the script will actually be run by.

That gap is where the bugs live. On macOS the interactive shell is zsh while nearly every script carries `#!/usr/bin/env bash`, and `zsh ./script.sh` **ignores the shebang entirely**. The script then meets a shell where arrays start at 1, unquoted variables do not word-split, and an unmatched glob is a fatal error rather than a literal string. Each of those is silent until it is not.

<br/>

## What it catches

| Difference | Under bash | Under zsh |
|---|---|---|
| `${BASH_SOURCE[0]}` | the script path | unset — fatal under `set -u` |
| `arr=(a b c); ${arr[0]}` | `a` | empty — zsh arrays are 1-indexed |
| `for x in v*/` with no match | literal `v*/` | fatal `no matches found` |
| `for x in $LIST` | splits on spaces | one whole string, no split |
| `echo -e "a\nb"` | interprets `\n` | prints a literal `-e` |
| `declare -g` | works | not a thing — `typeset -g` |

It also flags the usual quality issues on the same pass — missing `set -euo pipefail`, unquoted expansions in test contexts, `mktemp` without a cleanup trap, `rm -rf "$VAR"` where `$VAR` may be empty, and the macOS bash 3.2 vs bash 5 split that makes `declare -A` and `${var,,}` unsafe.

<br/>

## The fix it keeps recommending

Most findings collapse into one two-line guard at the top of the script:

```bash
#!/usr/bin/env bash
if [ -n "${ZSH_VERSION:-}" ]; then
  exec bash "$0" "$@"
fi
set -euo pipefail
```

With that in place, bash-only constructs below it are safe by construction, and the review stops flagging them. Scripts that refuse the guard get portable substitutes instead — `${BASH_SOURCE[0]:-$0}`, `IFS=' ' read -r -a`, `printf` over `echo -e`.

<br/>

## Usage

The agent runs on request or proactively before you commit a shell script.

```
review my shell scripts for portability
```

Findings are grouped by file and bucketed 🔴 / 🟡 / 🟢, each citing `file:line` with a minimal patch rather than a rewritten block:

```
Shell portability review
========================
2 files scanned, 1 with issues.

── scripts/foo.sh ──
🔴 Critical
  foo.sh:7  — `${BASH_SOURCE[0]}` fails under zsh `set -u`. Add the re-exec
              guard at line 4:
              if [ -n "${ZSH_VERSION:-}" ]; then exec bash "$0" "$@"; fi
🟡 Warning
  foo.sh:23 — `echo -e` not portable; use `printf '%s\n' "$x"`.

Recommended verification:
  bash -n scripts/foo.sh && zsh --no-rcs -n scripts/foo.sh
```

<br/>

## Scope

In scope: `*.sh` / `*.bash` / `*.zsh`, any file whose shebang names a shell, inline shell in CI YAML (`run:` / `script:` blocks), and git or husky hook scripts.

Out of scope: PowerShell, fish, `.bat` / `.cmd`, and POSIX-sh rewrites — the invariant is bash + zsh, not bash + sh + dash + ash.

<br/>

## What it never does

- Runs the script under review, unless the invocation is clearly read-only (`--help`, `--dry-run`).
- Edits a script silently. Non-trivial fixes are reported for approval.
- Rewrites a `bash` shebang into `sh` or `zsh`.
- Duplicates `shellcheck`. It names it as a companion and stays on the portability axis, which shellcheck does not cover.

<br/>

## License

MIT — see [LICENSE](../../LICENSE).
