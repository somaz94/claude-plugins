#!/usr/bin/env bash
# Every check CI runs that does not need a tag build or a GitHub runner.
#
# The point of this file is that the answer to "will CI pass?" is available
# before pushing. When it and CI disagree, the workflow is the one to fix.
set -euo pipefail
. "$(dirname "${BASH_SOURCE[0]:-$0}")/lib.sh"

failed=0

step "Byte-compile every bundled script"
python3 -m compileall -q plugins
echo "OK"

step "Validate the marketplace and plugin manifests"
if command -v claude >/dev/null 2>&1; then
  claude plugin validate .
  for manifest in plugins/*/.claude-plugin/plugin.json; do
    claude plugin validate "$(dirname "$(dirname "$manifest")")"
  done
else
  # CI installs the CLI; a contributor's machine may not have it, and that is
  # not a reason to fail the rest of the suite.
  echo "SKIP: the claude CLI is not on PATH — CI still runs this"
fi

suites=("tests/consistency.sh")
for suite in plugins/*/tests/run.sh; do
  [ -f "$suite" ] && suites+=("$suite")
done

for suite in "${suites[@]}"; do
  printf '\n══ %s\n' "$suite"
  if bash "$suite"; then
    printf '\n[PASS] %s\n' "$suite"
  else
    printf '\n[FAIL] %s\n' "$suite"
    failed=1
  fi
done

printf '\n'
if [ "$failed" -eq 0 ]; then
  echo "[PASS] every local suite passed"
else
  echo "[FAIL] at least one suite failed"
fi
exit "$failed"
