#!/usr/bin/env bash
#
# pre-commit-sensitive-scan.sh
# Claude Code PreToolUse(Bash) hook.
#
# Purpose:
#   Promote the "sanitize before publishing" CLAUDE.md convention from a soft
#   model-side rule into a HARD harness gate. Before a `git commit` lands in a
#   repo that opted in, run the bundled find-sensitive.sh scanner over
#   the repo. If any unsanitized sensitive value is detected, BLOCK the commit
#   (exit 2) and feed the category summary back to Claude so it sanitizes first.
#
# Scope (commit is scanned only when ALL hold):
#   - the command is a `git commit` (handles `git commit`, `git -C <dir> commit`,
#     and a leading `cd <dir> && git commit`)
#   - the resolved git repo root carries a `.sensitive-patterns` file
#   - the repo basename does NOT contain "-private" (those are sanitize-exempt;
#     this also covers the claude-private mirror)
#   - the repo is NOT an external OSS fork (an `origin` whose owner differs from
#     the `upstream` remote's owner). Such a tree is a clone of someone else's
#     project, so any flagged value is the UPSTREAM project's own example data
#     (doc example IPs, chart demo passwords), not the user's leak — and a PR
#     must leave those upstream files untouched. See is_external_oss_fork().
#
# Exit codes (Claude Code hook contract):
#   0  allow  - clean, or out of scope (silent)
#   2  block  - sensitive values found; stderr is returned to Claude
#   1  warn   - non-blocking error (scanner missing / errored); commit proceeds
#
# Input: PreToolUse JSON on stdin ({ tool_input.command, cwd, ... }).
#
# Notes:
#   - find-sensitive.sh ships in this plugin, resolved via CLAUDE_PLUGIN_ROOT.
#   - The scanner is invoked with -q so matched lines (the actual sensitive
#     values) are NOT echoed into the transcript; only category counts surface.
#   - Fails OPEN (warn, not block) if the scanner cannot run, so a broken
#     scanner never wedges every commit in every repo.
#
set -euo pipefail

# Shipped alongside this hook, so the two can never drift apart.
SCANNER="${CLAUDE_PLUGIN_ROOT:-$(cd "$(dirname "${BASH_SOURCE[0]:-$0}")/.." && pwd)}/scripts/find-sensitive.sh"

# Run a command with a hard time cap when gtimeout/timeout is available,
# otherwise run it as-is (macOS lacks `timeout` unless coreutils is installed).
_with_timeout() {
  local secs="$1"; shift
  if command -v gtimeout >/dev/null 2>&1; then
    gtimeout "$secs" "$@"
  elif command -v timeout >/dev/null 2>&1; then
    timeout "$secs" "$@"
  else
    "$@"
  fi
}

# Return 0 ONLY if the repo's GitHub origin is a PRIVATE repo. Used to exempt
# private repos from the block: a sensitive value in a non-public repo is not a
# publish leak. Mirrors find-sensitive.sh --all's GitHub-private detection, but
# is invoked only on the block path so clean commits never pay the network cost.
# Fails CLOSED — any uncertainty (no gh, no origin, non-GitHub remote, API
# error, timeout) returns non-zero, so the commit is still blocked.
is_github_private() {
  local dir="$1" url slug priv
  command -v gh >/dev/null 2>&1 || return 1
  url="$(git -C "$dir" remote get-url origin 2>/dev/null || true)"
  [[ -n "$url" ]] || return 1
  url="${url%.git}"
  # [^/:]* tolerates an SSH host alias (e.g. github.com-work:owner/repo from a
  # ~/.ssh/config multi-account setup) as well as plain github.com: / github.com/.
  [[ "$url" =~ github\.com[^/:]*[/:]([^/]+)/([^/]+)$ ]] || return 1
  slug="${BASH_REMATCH[1]}/${BASH_REMATCH[2]}"
  priv="$(_with_timeout 8 gh repo view "$slug" --json isPrivate -q .isPrivate 2>/dev/null || true)"
  [[ "$priv" == "true" ]]
}

# Print the owner segment of a git remote's URL (the path component just before
# the repo name), for any host. Handles:
#   git@github.com:owner/repo.git
#   git@github.com-alias:owner/repo.git   (ssh multi-account host alias)
#   https://github.com/owner/repo.git
#   https://gitlab.example.com/group/repo
# Returns 1 (no output) when the remote is missing or unparseable.
_remote_owner() {
  local dir="$1" remote="$2" url
  url="$(git -C "$dir" remote get-url "$remote" 2>/dev/null || true)"
  [[ -n "$url" ]] || return 1
  url="${url%.git}"
  url="${url%/}"
  # Match the final  <sep>owner/repo  where <sep> is ':' or '/'. owner excludes
  # both '/' and ':' so an ssh "host:owner/repo" yields owner, not the host.
  [[ "$url" =~ [:/]([^/:]+)/([^/]+)$ ]] || return 1
  printf '%s' "${BASH_REMATCH[1]}"
}

# Return 0 ONLY when the repo is an external OSS fork: it has BOTH an `origin`
# and an `upstream` remote AND their owners differ. In that case the working
# tree is a clone of someone else's project, so values find-sensitive.sh flags
# are the upstream project's own example data (RFC1918 IPs in docs, demo
# passwords in chart values), NOT the user's leaks — and a contribution PR must
# leave those upstream files byte-for-byte intact. Offline (URL parse only); no
# network. Fails CLOSED (returns 1) on any uncertainty — a missing `upstream`
# remote, an unparseable URL, or identical owners — so a normal single-remote
# opted-in repo (the thing this hook protects) is never exempted by mistake.
is_external_oss_fork() {
  local dir="$1" origin_owner upstream_owner
  origin_owner="$(_remote_owner "$dir" origin)"     || return 1
  upstream_owner="$(_remote_owner "$dir" upstream)"  || return 1
  [[ -n "$origin_owner" && -n "$upstream_owner" ]]   || return 1
  [[ "$origin_owner" != "$upstream_owner" ]]
}

# --- read the hook payload (entire stdin) ---
payload="$(cat)"

# Fast path: if the payload never mentions "commit", this is not a git commit.
# Pure-bash check keeps the common (non-commit) Bash call essentially free.
case "$payload" in
  *commit*) : ;;
  *) exit 0 ;;
esac

# --- precise parse: is this a git commit, and which repo dir does it target? ---
# python3 is used for correct shell-token + JSON handling. Prints the target
# directory to stdout, or an empty line when this is not a `git commit`.
repo_dir="$(
  printf '%s' "$payload" | python3 -c '
import json, os, re, shlex, sys

try:
    d = json.load(sys.stdin)
except Exception:
    print("")
    sys.exit(0)

cmd = (d.get("tool_input") or {}).get("command", "") or ""
cwd = d.get("cwd") or os.getcwd()

def resolve(base, path):
    path = os.path.expanduser(path)
    if not os.path.isabs(path):
        path = os.path.normpath(os.path.join(base, path))
    return path

chain_dir = cwd
# Split into simple commands on &&, ||, ;, and newlines.
for seg in re.split(r"&&|\|\||;|\n", cmd):
    try:
        toks = shlex.split(seg)
    except ValueError:
        toks = seg.split()
    if not toks:
        continue
    if toks[0] == "cd" and len(toks) >= 2:
        chain_dir = resolve(chain_dir, toks[1])
        continue
    if toks[0] == "git":
        git_dir = chain_dir
        i = 1
        # Walk git global options to find -C and reach the subcommand.
        while i < len(toks):
            t = toks[i]
            if t in ("-C", "--git-dir", "--work-tree") and i + 1 < len(toks):
                if t == "-C":
                    git_dir = resolve(chain_dir, toks[i + 1])
                i += 2
                continue
            if t.startswith("-"):
                i += 1
                continue
            break
        subcmd = toks[i] if i < len(toks) else ""
        if subcmd == "commit":
            # Detect `-a`/`--all`/`-am` so the scan range widens from the index
            # (--cached) to the worktree (HEAD), covering unstaged tracked edits
            # that `commit -a` will fold in.
            mode = "cached"
            for t in toks[i + 1:]:
                if t == "--all" or (t.startswith("-") and not t.startswith("--") and "a" in t[1:]):
                    mode = "head"
                    break
            print(git_dir + "\t" + mode)
            sys.exit(0)
print("")
'
)"

# Not a git commit -> allow silently.
[[ -z "$repo_dir" ]] && exit 0

# Split "<dir>\t<mode>" (mode = cached | head). Default to cached if no tab.
commit_mode="cached"
if [[ "$repo_dir" == *$'\t'* ]]; then
  commit_mode="${repo_dir#*$'\t'}"
  repo_dir="${repo_dir%%$'\t'*}"
fi

# Resolve the git repo root; bail (allow) if it is not a git work tree.
repo_root="$(git -C "$repo_dir" rev-parse --show-toplevel 2>/dev/null || true)"
[[ -z "$repo_root" ]] && exit 0

# Scope guard: the repo opts IN by carrying a `.sensitive-patterns` file at its
# root. Nothing is gated by path, because one person's "public mirror" directory
# is another's ordinary checkout — and a guard that fires in repos nobody asked
# it to fire in is a guard that gets uninstalled. The file is where your own
# markers live anyway, so opting in and configuring are the same act. An empty
# file is valid: it arms the universal categories and adds nothing.
if [[ ! -f "$repo_root/.sensitive-patterns" ]]; then
  exit 0
fi

# -private repos are sanitize-exempt (covers claude-private too) -> allow.
# *.wiki repos are GitHub wiki docs (study/educational notes whose networking
# and Kubernetes pages are legitimately full of RFC1918 example IPs). The
# scanner's --all mode already skips them (SKIP_BASENAMES_RE='...|\.wiki$'),
# so exempt them here too for consistency.
case "$(basename "$repo_root")" in
  *-private*) exit 0 ;;
  *.wiki) exit 0 ;;
esac

# External OSS fork (origin owner != upstream owner) -> allow. The flagged
# values belong to the upstream project, not the user; a PR must not touch them.
# Offline check, so it costs nothing and runs before the scanner.
if is_external_oss_fork "$repo_root"; then
  exit 0
fi

# Locate the scanner; fail OPEN (warn) if missing.
if [[ ! -x "$SCANNER" ]]; then
  echo "WARN: sensitive-data scanner not found at $SCANNER - skipping pre-commit scan." >&2
  exit 1
fi

# find-sensitive.sh needs bash >= 4; prefer Homebrew bash when present.
BASH_BIN="bash"
[[ -x /opt/homebrew/bin/bash ]] && BASH_BIN="/opt/homebrew/bin/bash"

# Diff-scoped scan: materialize ONLY the lines this commit ADDS into a temp tree
# (preserving each file's relative path + extension so the scanner's include
# filters and per-category false-positive filters apply unchanged), then scan
# that tree instead of the whole repo. This gates what the commit INTRODUCES,
# not pre-existing state: upstream chart default passwords and demo IPs sitting
# on UNCHANGED lines (even inside a modified file) no longer block the commit,
# while a genuinely new secret in an added line still does. `commit -a` widens
# the range to HEAD so unstaged tracked edits are covered. Falls back to a
# whole-repo scan if the diff cannot be computed (e.g. mktemp/python failure).
scan_dir="$repo_root"
scan_scope="diff"
tmp_added=""
_cleanup_tmp_added() { [[ -n "$tmp_added" && -d "$tmp_added" ]] && rm -rf "$tmp_added"; }
trap _cleanup_tmp_added EXIT

tmp_added="$(mktemp -d 2>/dev/null || true)"
if [[ -n "$tmp_added" && -d "$tmp_added" ]] && \
   printf '%s' "$repo_root" | TMP_ADDED="$tmp_added" COMMIT_MODE="$commit_mode" python3 -c '
import os, subprocess, sys
repo = sys.stdin.read()
tmp  = os.environ["TMP_ADDED"]
mode = os.environ.get("COMMIT_MODE", "cached")
rng  = ["HEAD"] if mode == "head" else ["--cached"]
try:
    diff = subprocess.run(
        ["git", "-C", repo, "diff", "-U0", "--no-color", "--diff-filter=ACMR"] + rng,
        capture_output=True, text=True, check=True).stdout
except Exception:
    sys.exit(3)  # signal: cannot compute diff -> caller falls back to whole-repo
cur = None
buf = {}
for ln in diff.splitlines():
    if ln.startswith("+++ "):
        p = ln[4:].strip()
        if p.startswith("b/"):
            p = p[2:]
        cur = None if p == "/dev/null" else p
        continue
    if ln.startswith("+") and not ln.startswith("+++") and cur:
        buf.setdefault(cur, []).append(ln[1:])
for p, lines in buf.items():
    dst = os.path.join(tmp, p)
    os.makedirs(os.path.dirname(dst) or tmp, exist_ok=True)
    with open(dst, "w") as f:
        f.write("\n".join(lines) + "\n")
sys.exit(0)
'; then
  scan_dir="$tmp_added"
else
  scan_scope="whole-repo (diff unavailable)"
fi

# Run the scanner in quiet mode (category counts only, no matched values).
set +e
scan_out="$("$BASH_BIN" "$SCANNER" -q "$scan_dir" 2>&1)"
scan_rc=$?
set -e

case "$scan_rc" in
  0)
    exit 0  # clean -> allow
    ;;
  1)
    # Matches found. Exempt GitHub-private repos before blocking: a sensitive
    # value in a non-public repo is not a publish leak. This gh check runs ONLY
    # here (the rare block path), so clean commits never pay the network cost.
    if is_github_private "$repo_root"; then
      exit 0  # private GitHub repo -> allow despite matches
    fi
    # Downgrade: if the ONLY category that fired is private_ip, warn but allow.
    # RFC1918 (10/8, 172.16/12, 192.168/16) is non-routable; a hardcoded private
    # IP is at most an internal-topology hint, not a public-endpoint/credential
    # leak. Real leaks (tokens, public domains, SSH keys, passwords) are separate
    # categories and still hard-block below. The scanner still REPORTS private_ip
    # in /scan and --all, so audit visibility is preserved.
    fired=$(printf '%s\n' "$scan_out" | sed -nE 's/^\[([a-z_]+)\] [0-9]+ match.*/\1/p' | sort -u)
    if [[ "$fired" == "private_ip" ]]; then
      echo "WARN: only RFC1918 private IPs matched in ${repo_root} (non-routable, low-signal); allowing commit." >&2
      printf '%s\n' "$scan_out" >&2
      exit 1   # non-blocking warn (same convention as the '*)' branch)
    fi
    {
      echo "BLOCKED: sensitive values detected in the lines this commit ADDS to ${repo_root}."
      echo "Sanitize the flagged values (replace with example values), then re-commit."
      echo "Scan scope: ${scan_scope} (only added/changed lines are gated, not pre-existing repo state)."
      echo "Inspect the added lines with: git -C ${repo_root} diff --cached"
      echo "(This repo is public or its GitHub visibility could not be confirmed.)"
      echo "---"
      printf '%s\n' "$scan_out"
    } >&2
    exit 2  # matches found in a public/unknown repo -> BLOCK
    ;;
  *)
    echo "WARN: sensitive-data scanner exited ${scan_rc} - allowing commit (could not verify):" >&2
    printf '%s\n' "$scan_out" >&2
    exit 1  # non-blocking
    ;;
esac
