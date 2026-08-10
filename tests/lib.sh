# shellcheck shell=bash
# Shared helpers for the repo's test scripts. Sourced, never executed — hence
# the directive above instead of a shebang, which would be a lie about how this
# file is used and would still leave shellcheck guessing.
#
# The bodies of these tests are the same text CI runs. What lives here is only
# what a workflow gets for free and a shell does not: a known working directory
# and a legible heading between steps.

# Resolved from this file rather than from the caller's cwd, so a suite behaves
# the same whether it is run from the repo root, from a plugin directory, or by
# an editor. `${BASH_SOURCE[0]:-$0}` covers being sourced by zsh, where
# BASH_SOURCE does not exist.
#
# A failed `cd` must abort rather than run the whole suite against whatever
# directory the caller happened to be in — every path below this line is
# relative to the repo root.
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
