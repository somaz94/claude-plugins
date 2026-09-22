# shellcheck shell=bash
# Sourced by every test script (hence the directive, not a shebang). Holds only
# what a workflow gets for free: a fixed working directory and step headings.

# Resolved from this file, not the caller's cwd; `:-$0` covers zsh (no BASH_SOURCE).
# Every path below is repo-relative, so a failed cd must abort the suite.
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]:-$0}")/.." && pwd)"
cd "$ROOT" || exit 1

# Bold when a terminal is watching, plain when output is piped or NO_COLOR is
# set — a log full of escape codes is worse than no emphasis at all.
if [ -t 1 ] && [ -z "${NO_COLOR:-}" ]; then
  _step_open=$'\033[1m'; _step_close=$'\033[0m'
else
  _step_open=""; _step_close=""
fi

step() {
  printf '\n%s▸ %s%s\n' "$_step_open" "$1" "$_step_close"
}
