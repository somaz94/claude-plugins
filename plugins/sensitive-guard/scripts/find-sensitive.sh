#!/usr/bin/env bash
#
# find-sensitive.sh
#
# Scan a directory for values that look sensitive and should be sanitized
# before publishing to a public repo.
#
# This is a LAST-MILE GATE, not a replacement for a dedicated secret scanner.
# Run gitleaks or trufflehog in CI for detection depth; this exists to stop a
# leak at the moment of commit, where those tools do not run.
#
#   Usage:
#     find-sensitive.sh [DIR]            # defaults to CWD
#     find-sensitive.sh -q [DIR]         # quiet: only show counts per category
#     find-sensitive.sh -x "pat" [DIR]   # add one extra regex (repeatable)
#     find-sensitive.sh -p FILE [DIR]    # read extra categories from FILE
#     find-sensitive.sh --all [ROOT]     # scan every immediate subdir of ROOT
#                                        # (default: CWD), skipping any whose
#                                        # basename contains "-private", any
#                                        # whose GitHub origin is a private repo
#                                        # (auto-detected via `gh`), and common
#                                        # junk dirs (venv, node_modules).
#                                        # Prints a per-repo summary at the end.
#     find-sensitive.sh --all --no-remote-check [ROOT]
#                                        # skip the GitHub private-repo lookup
#                                        # (useful when offline or gh not set up)
#
# Your own markers — a company name, an internal domain, a real username — are
# NOT built in, because they differ per person. Put them in a patterns file:
#
#     # .sensitive-patterns   (repo root, or ~/.claude/sensitive-patterns)
#     internal_marker|acme-corp|acme-internal
#     personal_email|me@example\.com
#
# One `name|regex` per line; blank lines and `#` comments are ignored. In a repo
# the file also acts as the opt-in signal for the pre-commit hook.
#
# What it catches out of the box (per-category regex):
#   - Private IP blocks      : 10.x.x.x, 172.16-31.x.x, 192.168.x.x
#     (RFC 5737 doc ranges 192.0.2/24, 198.51.100/24, 203.0.113/24 are allowed)
#   - Secrets                : AWS keys (AKIA...), GitLab PATs (glpat-),
#                              GitLab runner tokens (glrt-),
#                              GitHub tokens (ghp_/gho_/ghs_/ghu_/ghr_),
#                              Slack tokens (xox[abprs]-...),
#                              GitLab OAuth secret (gloas-<64 hex>),
#                              OIDC client secrets (client_secret = <value>),
#                              generic "password=..."/"api_key=..." assignments
#   - SSH private keys       : -----BEGIN ... PRIVATE KEY----- followed by a
#                              base64 body line (header-only examples skipped)
#   - Leaked home paths      : /Users/<name>/.{claude,ssh,aws,kube} and the
#                              /home/<name>/ equivalent. Bare `~/.claude` is
#                              NOT flagged — it is identical on every machine.
#
# Exit code:
#   0 if nothing found, 1 if any category matched.
#
# File scope:
#   *.sh *.bash *.zsh *.py *.go *.yaml *.yml *.json *.tpl *.md *.env
#   *.tf *.hcl Jenkinsfile* Dockerfile*
#   Skips: .git/, node_modules/, vendor/, backup/, _backup/, *.lock, *.min.*
#
set -euo pipefail

if ((BASH_VERSINFO[0] < 4)); then
  echo "ERROR: this script needs bash >= 4 (use brew's bash: /opt/homebrew/bin/bash)" >&2
  exit 3
fi

QUIET=0
ALL_MODE=0
REMOTE_CHECK=1
EXTRA_PATTERNS=()

while [[ $# -gt 0 ]]; do
  case "$1" in
    -q|--quiet) QUIET=1; shift ;;
    -x|--extra) EXTRA_PATTERNS+=("$2"); shift 2 ;;
    -p|--patterns) PATTERN_FILE="$2"; shift 2 ;;
    --all) ALL_MODE=1; shift ;;
    --no-remote-check) REMOTE_CHECK=0; shift ;;
    -h|--help)
      sed -n '3,60p' "$0" | sed 's/^# \{0,1\}//'
      exit 0 ;;
    *) break ;;
  esac
done

if [[ "$ALL_MODE" -eq 1 ]]; then
  ROOT="${1:-.}"
  if [[ ! -d "$ROOT" ]]; then
    echo "ERROR: not a directory: $ROOT" >&2
    exit 2
  fi

  SELF="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/$(basename "${BASH_SOURCE[0]}")"
  SKIP_BASENAMES_RE='(^venv$|^node_modules$|^\.|\.wiki$)'

  # Pass 1: filter by basename rule.
  declare -a CANDIDATES=()
  declare -a EXCL_NAME_PRIVATE=()
  declare -a EXCL_JUNK=()
  while IFS= read -r -d '' d; do
    name="$(basename "$d")"
    if [[ "$name" == *-private* ]]; then
      EXCL_NAME_PRIVATE+=("$name"); continue
    fi
    if [[ "$name" =~ $SKIP_BASENAMES_RE ]]; then
      EXCL_JUNK+=("$name"); continue
    fi
    CANDIDATES+=("$d")
  done < <(find "$ROOT" -mindepth 1 -maxdepth 1 -type d -print0 | LC_ALL=C sort -z)

  # Pass 2: optionally drop GitHub private repos (origin remote → gh API).
  declare -a REPOS=()
  declare -a EXCL_GH_PRIVATE=()
  declare -a EXCL_GH_UNKNOWN=()  # informational only, still scanned

  github_slug() {
    # Print "owner/repo" for a github origin remote, or empty.
    local dir="$1" url
    [[ -d "$dir/.git" || -f "$dir/.git" ]] || return 0
    url=$(git -C "$dir" remote get-url origin 2>/dev/null || true)
    [[ -z "$url" ]] && return 0
    local stripped="${url%.git}"
    if [[ "$stripped" =~ github\.com[/:]([^/]+)/([^/]+)$ ]]; then
      printf '%s/%s' "${BASH_REMATCH[1]}" "${BASH_REMATCH[2]}"
    fi
  }

  if [[ "$REMOTE_CHECK" -eq 1 ]] && command -v gh >/dev/null 2>&1; then
    # Collect slugs and unique owners.
    declare -a SLUGS=()
    declare -A REPO_SLUG=()       # path -> slug
    declare -A OWNERS_SEEN=()
    for d in "${CANDIDATES[@]}"; do
      slug=$(github_slug "$d")
      REPO_SLUG["$d"]="$slug"
      [[ -z "$slug" ]] && continue
      SLUGS+=("$slug")
      OWNERS_SEEN["${slug%%/*}"]=1
    done

    # Bulk fetch isPrivate per owner (one API call per owner).
    declare -A IS_PRIVATE=()  # "owner/repo" -> "true"/"false"
    for owner in "${!OWNERS_SEEN[@]}"; do
      while IFS=$'\t' read -r repo_name priv; do
        [[ -z "$repo_name" ]] && continue
        IS_PRIVATE["$owner/$repo_name"]="$priv"
      done < <(gh repo list "$owner" --limit 1000 --json name,isPrivate \
                 -q '.[] | [.name, (.isPrivate|tostring)] | @tsv' 2>/dev/null || true)
    done

    for d in "${CANDIDATES[@]}"; do
      name="$(basename "$d")"
      slug="${REPO_SLUG[$d]}"
      if [[ -z "$slug" ]]; then
        # No github remote — keep, mark as unknown source.
        REPOS+=("$d")
        continue
      fi
      priv="${IS_PRIVATE[$slug]:-}"
      if [[ -z "$priv" ]]; then
        # Owner-list miss (fork/transferred/etc.) — fall back to per-repo view.
        priv=$(gh repo view "$slug" --json isPrivate -q .isPrivate 2>/dev/null || echo "")
      fi
      case "$priv" in
        true)  EXCL_GH_PRIVATE+=("$name ($slug)") ;;
        false) REPOS+=("$d") ;;
        *)     EXCL_GH_UNKNOWN+=("$name ($slug)"); REPOS+=("$d") ;;
      esac
    done
  else
    REPOS=("${CANDIDATES[@]}")
    if [[ "$REMOTE_CHECK" -eq 1 ]]; then
      echo "WARN: gh CLI not found — skipping GitHub private-repo check." >&2
    fi
  fi

  printf '=== Scanning %d repo(s) under %s ===\n' "${#REPOS[@]}" "$ROOT"
  printf 'Excluded by name *-private : %d\n' "${#EXCL_NAME_PRIVATE[@]}"
  printf 'Excluded by junk basename  : %d\n' "${#EXCL_JUNK[@]}"
  printf 'Excluded as GitHub private : %d\n' "${#EXCL_GH_PRIVATE[@]}"
  if [[ "${#EXCL_GH_PRIVATE[@]}" -gt 0 && "$QUIET" -eq 0 ]]; then
    for n in "${EXCL_GH_PRIVATE[@]}"; do printf '    - %s\n' "$n"; done
  fi
  if [[ "${#EXCL_GH_UNKNOWN[@]}" -gt 0 ]]; then
    printf 'GitHub status unknown (kept, please verify): %d\n' "${#EXCL_GH_UNKNOWN[@]}"
    for n in "${EXCL_GH_UNKNOWN[@]}"; do printf '    - %s\n' "$n"; done
  fi
  echo ""

  q_arg=()
  [[ "$QUIET" -eq 1 ]] && q_arg+=(-q)
  x_args=()
  for p in "${EXTRA_PATTERNS[@]}"; do x_args+=(-x "$p"); done

  dirty_repos=()
  clean_count=0
  total_hits=0
  for repo in "${REPOS[@]}"; do
    name="$(basename "$repo")"
    printf '### %s\n' "$name"
    out=$(bash "$SELF" "${q_arg[@]}" "${x_args[@]}" "$repo" 2>&1 || true)
    printf '%s\n\n' "$out"
    hits_line=$(printf '%s\n' "$out" | grep -E '^Total matches: [0-9]+' | tail -1 || true)
    if [[ -n "$hits_line" ]]; then
      n="${hits_line##Total matches: }"
      total_hits=$((total_hits + n))
      dirty_repos+=("$name|$n")
    else
      clean_count=$((clean_count + 1))
    fi
  done

  echo "======================================"
  printf 'Summary: %d dirty / %d clean / %d total repos scanned\n' \
    "${#dirty_repos[@]}" "$clean_count" "${#REPOS[@]}"
  printf 'Aggregate matches: %d\n' "$total_hits"
  if [[ "${#dirty_repos[@]}" -gt 0 ]]; then
    echo ""
    echo "Repos with matches:"
    for e in "${dirty_repos[@]}"; do
      printf '  %-45s %s\n' "${e%%|*}" "${e##*|} match(es)"
    done
  fi
  echo "======================================"

  [[ "${#dirty_repos[@]}" -gt 0 ]] && exit 1 || exit 0
fi

TARGET="${1:-.}"
if [[ ! -d "$TARGET" ]]; then
  echo "ERROR: not a directory: $TARGET" >&2
  exit 2
fi

INCLUDES=(
  --include='*.sh' --include='*.bash' --include='*.zsh'
  --include='*.py' --include='*.go'
  --include='*.yaml' --include='*.yml' --include='*.json'
  --include='*.tpl' --include='*.md' --include='*.env'
  --include='*.tf'  --include='*.hcl'
  --include='*.ini' --include='*.cfg' --include='*.conf'
  --include='Jenkinsfile*' --include='*Jenkinsfile*'
  --include='*jenkinsfile*'
  --include='Dockerfile*'
)
EXCLUDES=(
  --exclude-dir=.git --exclude-dir=node_modules --exclude-dir=vendor
  --exclude-dir=backup --exclude-dir=_backup --exclude-dir=dist
  --exclude-dir=venv --exclude-dir=.venv --exclude-dir=__pycache__
  --exclude='*.lock' --exclude='*.min.*'
)

# category_name|regex
CATEGORIES=(
  # Universal categories only. Anything that identifies a PARTICULAR person,
  # company or machine belongs in a patterns file, not in here — see
  # `load_pattern_file` below and the README.
  'private_ip|(^|[^0-9])10(\.[0-9]{1,3}){3}([^0-9]|$)|(^|[^0-9])192\.168(\.[0-9]{1,3}){2}([^0-9]|$)|(^|[^0-9])172\.(1[6-9]|2[0-9]|3[0-1])(\.[0-9]{1,3}){2}([^0-9]|$)'
  'aws_key|AKIA[0-9A-Z]{16}'
  'gitlab_pat|glpat-[A-Za-z0-9_-]{20,}'
  'gitlab_runner_token|glrt-[A-Za-z0-9_.-]{20,}'
  'gitlab_oauth_secret|gloas-[0-9a-f]{64}'
  'github_token|gh[pousr]_[A-Za-z0-9]{36,}'
  'slack_token|xox[abprs]-[0-9A-Za-z-]{10,}'
  'generic_secret_assignment|(password|passwd|api[_-]?key|secret|token)\s*[:=]\s*["'"'"']?[A-Za-z0-9][A-Za-z0-9_!@#$%^&*-]{7,}'
  'oidc_client_secret|(client[_-]?secret|clientSecret|CLIENT_SECRET)["'"'"']?\s*[:=]\s*["'"'"']?[A-Za-z0-9]{20,}'
  'ssh_private_key|-----BEGIN [A-Z ]*PRIVATE KEY-----'
  # A home path carrying a real username leaks who ran the command. Bare
  # `~/.claude` is deliberately NOT flagged: it is Claude Code's own documented
  # path, identical on every machine, so a repo that legitimately documents it
  # would cry wolf on every commit — and a gate that always fires gets
  # overridden by reflex.
  'leaked_home_path|/(Users|home)/[A-Za-z0-9_.-]+/\.(claude|ssh|aws|kube)'
)

# Extra categories come from a patterns file, so the universal set above stays
# the same for everyone. Format is one `name|regex` per line; blank lines and
# `#` comments are ignored. The repo-root file doubles as the opt-in signal the
# commit hook looks for.
load_pattern_file() {
  local file="$1" line name rest
  [[ -f "$file" ]] || return 0
  while IFS= read -r line || [[ -n "$line" ]]; do
    line="${line%%$'\r'}"
    [[ -z "${line// /}" || "${line#\#}" != "$line" ]] && continue
    name="${line%%|*}"; rest="${line#*|}"
    [[ "$name" == "$line" || -z "$rest" ]] && continue
    CATEGORIES+=("$line")
  done < "$file"
}

if [[ -n "${PATTERN_FILE:-}" ]]; then
  load_pattern_file "$PATTERN_FILE"
else
  load_pattern_file "./.sensitive-patterns"
  load_pattern_file "$HOME/.claude/sensitive-patterns"
fi

i=0
for p in "${EXTRA_PATTERNS[@]}"; do
  CATEGORIES+=("user_extra_$i|$p")
  i=$((i+1))
done

# Post-filter regexes to drop false positives per category (BSD grep has no lookaround).
# Format: "category_name|exclusion_regex"
FP_FILTERS=(
  # placeholder tokens: x/X/y repeated, or contains "..." / "your-" etc.
  'gitlab_pat|glpat-(x{10,}|X{10,}|y{10,})|glpat-[A-Za-z0-9_-]*(xxxx|\.\.\.|your[-_]|YOUR[-_])'
  'gitlab_runner_token|glrt-(x{4,}|X{4,})|glrt-[A-Za-z0-9_.-]*(xxxx|XXXX|\.\.\.|your[-_]|YOUR[-_])'
  'github_token|gh[pousr]_(x{30,}|X{30,}|[A-Za-z0-9]*xxxx)'
  'slack_token|xox[abprs]-(your|YOUR|xxx|example|placeholder)'
  # value side is ALL-CAPS const (PRIVATE_TOKEN, DEFAULT_PASSWORD),
  # OR value is a lowercase/snake_case identifier (self.x = x, self-reference),
  # OR the key NAMES a k8s object rather than holding a credential — `secret:`/`secretName:`
  #    take an RFC 1123 name (lowercase-alphanumeric-with-hyphens), e.g.
  #    `secret: <k8s-object-name>` names the Secret to read a credential FROM;
  #    it is not the password. Scoped to the secret* keys ON PURPOSE: allowing hyphens in
  #    the general identifier class above would silently un-flag real credentials such as
  #    a `password:` key holding a real hyphenated passphrase, which no other category catches.
  # OR value is an obvious placeholder (your-xxx, <...>, REPLACE_ME, xxx...)
  'generic_secret_assignment|[:=]\s*["'"'"']?[A-Z][A-Z0-9_]{2,}[,)\s"'"'"']*$|[:=]\s*[a-z_][a-z0-9_]*[,)\s]*$|(secret|secretName|secretRef|secretKeyRef)\s*[:=]\s*["'"'"']?[a-z0-9]([a-z0-9.-]*[a-z0-9])?["'"'"']?[,)\s]*$|[:=]\s*["'"'"']?(your[-_]|YOUR[-_]|<[A-Z_]+>|REPLACE|example|PLACEHOLDER|xxx)'
)

get_fp_filter() {
  local cat="$1"
  for f in "${FP_FILTERS[@]}"; do
    if [[ "${f%%|*}" == "$cat" ]]; then
      printf '%s' "${f#*|}"
      return
    fi
  done
}

# Keep only PEM headers immediately followed by a real base64 key body line.
# A real private key has a long base64 body on the next line; commented or
# documentation/example headers (the common false positive) do not.
filter_ssh_with_body() {
  local input="$1" line f rest ln nextline kept=""
  while IFS= read -r line; do
    [[ -z "$line" ]] && continue
    f="${line%%:*}"
    rest="${line#*:}"
    ln="${rest%%:*}"
    [[ "$ln" =~ ^[0-9]+$ ]] || continue
    nextline=$(sed -n "$((ln + 1))p" "$f" 2>/dev/null || true)
    if printf '%s\n' "$nextline" | grep -qE '^[[:space:]]*[A-Za-z0-9+/]{40,}={0,2}[[:space:]]*$'; then
      kept+="$line"$'\n'
    fi
  done <<< "$input"
  printf '%s' "${kept%$'\n'}"
}

hits=0
printf '=== Sensitive value scan: %s ===\n' "$TARGET"

for entry in "${CATEGORIES[@]}"; do
  name="${entry%%|*}"
  regex="${entry#*|}"

  out=$(grep -rEnI "${INCLUDES[@]}" "${EXCLUDES[@]}" -- "$regex" "$TARGET" 2>/dev/null || true)

  fp=$(get_fp_filter "$name")
  if [[ -n "$fp" && -n "$out" ]]; then
    out=$(printf '%s\n' "$out" | grep -Ev "$fp" || true)
  fi

  if [[ "$name" == "ssh_private_key" && -n "$out" ]]; then
    out=$(filter_ssh_with_body "$out")
  fi

  if [[ -z "$out" ]]; then
    continue
  fi

  count=$(printf '%s\n' "$out" | wc -l | tr -d ' ')
  hits=$((hits + count))

  printf '\n[%s] %d match(es)\n' "$name" "$count"
  if [[ "$QUIET" -eq 0 ]]; then
    printf '%s\n' "$out"
  fi
done

echo ""
if [[ "$hits" -eq 0 ]]; then
  echo "Clean: no sensitive values found."
  exit 0
else
  printf 'Total matches: %d\n' "$hits"
  exit 1
fi
