#!/usr/bin/env bash
# Propagate the canonical _shared.py to every plugin that vendors it.
#
# The helpers in _shared.py are vendored rather than imported, because a plugin
# is installed on its own and nothing outside its directory exists at runtime.
# Vendoring is only safe while the copies are identical, which is what
# tests/consistency.sh enforces and what this script restores.
#
# Edit plugins/census/scripts/_shared.py — the canonical copy — then run this.
set -euo pipefail
. "$(dirname "${BASH_SOURCE[0]:-$0}")/lib.sh"

CANONICAL="plugins/census/scripts/_shared.py"

if [ ! -f "$CANONICAL" ]; then
  echo "FAIL: no canonical copy at $CANONICAL" >&2
  exit 1
fi

step "Propagating $CANONICAL"

changed=0
for copy in plugins/*/scripts/_shared.py; do
  [ "$copy" = "$CANONICAL" ] && continue
  if cmp -s "$CANONICAL" "$copy"; then
    echo "  unchanged  $copy"
  else
    cp "$CANONICAL" "$copy"
    echo "  UPDATED    $copy"
    changed=$((changed + 1))
  fi
done

printf '\n'
if [ "$changed" -eq 0 ]; then
  echo "every copy was already identical"
else
  echo "$changed cop$([ "$changed" -eq 1 ] && echo y || echo ies) updated — commit them together"
fi
