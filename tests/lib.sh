# Shared helpers for the repo's test scripts. Sourced, never executed.
#
# The bodies of these tests are the same text CI runs. What lives here is only
# what a workflow gets for free and a shell does not: a known working directory
# and a legible heading between steps.

# Resolved from this file rather than from the caller's cwd, so a suite behaves
# the same whether it is run from the repo root, from a plugin directory, or by
# an editor. `${BASH_SOURCE[0]:-$0}` covers being sourced by zsh, where
# BASH_SOURCE does not exist.
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]:-$0}")/.." && pwd)"
cd "$ROOT"

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
