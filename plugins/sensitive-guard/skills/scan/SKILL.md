---
name: scan
description: Scan a repository for values that must be sanitized before it goes public — private IPs, cloud keys, tokens, SSH private keys, and any markers you defined yourself. Use before pushing or publishing, when asked "is this safe to publish?", "scan for secrets", "check before I push", or after sanitizing to confirm the fix.
argument-hint: "[path | --all [ROOT]]"
allowed-tools: Bash, Read
---

# scan

Run the bundled scanner over a repository and return a **safe-to-publish** / **hold** verdict. Read-only: it reports, it never edits, commits, or pushes.

<br/>

## Run it

```bash
"${CLAUDE_PLUGIN_ROOT}/scripts/find-sensitive.sh" [DIR]            # one repo or directory
"${CLAUDE_PLUGIN_ROOT}/scripts/find-sensitive.sh" -q [DIR]         # counts only
"${CLAUDE_PLUGIN_ROOT}/scripts/find-sensitive.sh" --all [ROOT]     # every subdirectory of ROOT
"${CLAUDE_PLUGIN_ROOT}/scripts/find-sensitive.sh" -p FILE [DIR]    # extra categories from FILE
```

With no argument it scans the current working directory. `--all` skips repos whose basename contains `-private` and, when `gh` is available, repos whose GitHub origin is private — add `--no-remote-check` when offline.

Exit code 0 means nothing matched; 1 means at least one category did.

<br/>

## Reporting

Lead with the verdict, then the evidence.

**Clean** — say so in one line and stop. Do not pad a clean result.

**Findings** — group by category and quote the file and line. For each, say which of these it is:

- **A real value that must not ship.** A live token, a key, an internal host. Name the sanitized replacement: an RFC 5737 documentation range (`192.0.2.0/24`, `198.51.100.0/24`, `203.0.113.0/24`) for addresses, an obviously fake placeholder for a credential.
- **An example that only looks real.** Test fixtures and documentation legitimately contain `password = hunter2` shapes. Say so plainly rather than demanding a change.

Keep a replacement in the same class as the original — swapping a public address for a private one silently breaks tests that branch on whether an address is routable.

If a category fires across many files at once, that is usually one convention rather than many leaks. Report it as a single finding with a count.

<br/>

## What this is not

A **last-mile gate**, not a replacement for `gitleaks` or `trufflehog`. The regex set is small and readable by design, which is what lets it run at commit time where those tools do not. Never present a clean result as proof the repository holds no secrets — report the verdict together with what was actually checked.

The scanner ships with universal categories only. A company name, an internal domain, a real username differ per person and live in a `.sensitive-patterns` file at the repo root, or in `~/.claude/sensitive-patterns`. If a scan comes back clean on a repo with no such file, say that only the universal set ran.

<br/>

## What you do NOT do

- Edit files to sanitize them. Report what to change; the user or a follow-up applies it.
- `git add`, `git commit`, `git push`, or publish anything.
- Call a repository safe because the scanner was quiet. The verdict and its scope travel together.
