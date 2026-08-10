#!/usr/bin/env bash
# session-continuity bundled-script tests
#
# The hook is advisory: PreCompact cannot cancel a compaction, so the only ways
# it can fail are emitting nothing when it should nudge, or emitting something
# malformed into a compaction the user needs. Both are silent at the moment they
# happen — the context is already being summarized away.
#
# Run directly (`bash plugins/session-continuity/tests/run.sh`) or through the
# whole suite (`bash tests/run.sh`). CI calls this same file.
set -euo pipefail
. "$(dirname "${BASH_SOURCE[0]:-$0}")/../../../tests/lib.sh"

HOOK="$ROOT/plugins/session-continuity/hooks/pre-compact-handoff-nudge.sh"

# Run the hook on a payload; print its stdout. Exits non-zero only if the hook
# itself does, which is already a defect for an advisory hook.
run_hook() {
  printf '%s' "$1" | bash "$HOOK"
}

step 'the nudge fires when context runs out on its own'
(
set -euo pipefail
out="$(run_hook '{"trigger":"auto","session_id":"s1","cwd":"/tmp"}')"
python3 - "$out" <<'PY'
import json, sys

failures = []
try:
    d = json.loads(sys.argv[1])
except Exception as exc:
    print(f"FAIL: output is not valid JSON ({exc}): {sys.argv[1][:200]!r}")
    sys.exit(1)

msg = d.get("systemMessage") or ""
ctx = (d.get("hookSpecificOutput") or {}).get("additionalContext") or ""

if not msg:
    failures.append("no systemMessage — the user sees nothing")
if not ctx:
    # The context line is the half that survives compaction; without it the
    # model has no idea a handoff was just suggested.
    failures.append("no additionalContext — nothing survives into the new context")
if (d.get("hookSpecificOutput") or {}).get("hookEventName") != "PreCompact":
    failures.append(f"wrong hookEventName: {(d.get('hookSpecificOutput') or {}).get('hookEventName')!r}")
if "handoff" not in msg:
    failures.append("the message never names the command it is recommending")
# An auto compaction is the strongest case for handing off, and the message has
# to say WHY now rather than reading as a generic tip.
if "near the limit" not in msg:
    failures.append(f"the auto trigger is not explained as a near-limit event: {msg[:120]!r}")
# The nudge must not read as though this session is being closed.
if "stays" not in msg:
    failures.append("the message does not say the current session stays open")

for line in failures:
    print(f"FAIL: {line}")
sys.exit(1 if failures else 0)
PY
echo "PASS: the auto nudge names the command, the reason and the consequence"
)

step 'a hand-run /compact gets its own wording'
(
set -euo pipefail
auto="$(run_hook '{"trigger":"auto"}')"
manual="$(run_hook '{"trigger":"manual"}')"
python3 - "$auto" "$manual" <<'PY'
import json, sys

failures = []
a = json.loads(sys.argv[1])
m = json.loads(sys.argv[2])
am, mm = a["systemMessage"], m["systemMessage"]

if am == mm:
    failures.append("both triggers produce the same message, so the field is unread")
if "/compact" not in mm:
    failures.append(f"the manual message does not acknowledge /compact: {mm[:120]!r}")
# Telling someone who deliberately ran /compact that they are "near the limit"
# is simply false.
if "near the limit" in mm:
    failures.append("the manual message claims the context was near the limit")
if "handoff" not in mm:
    failures.append("the manual message never names the command")

for line in failures:
    print(f"FAIL: {line}")
sys.exit(1 if failures else 0)
PY
echo "PASS: manual and auto are worded for what actually happened"
)

step 'an unknown trigger still gets nudged'
(
set -euo pipefail
# `trigger` is documented but not guaranteed. Degrading to silence would drop
# the nudge at exactly the moment it matters most.
for payload in '{}' '{"trigger":""}' '{"trigger":"something-new"}' '{"session_id":"s1"}'; do
  out="$(run_hook "$payload")"
  if [ -z "$out" ]; then
    echo "FAIL: payload $payload produced no nudge at all"
    exit 1
  fi
  printf '%s' "$out" | python3 -c '
import json, sys
d = json.load(sys.stdin)
assert d.get("systemMessage"), "no systemMessage"
' || { echo "FAIL: payload $payload produced no usable message"; exit 1; }
done
echo "PASS: a missing or unrecognised trigger degrades to the generic nudge"
)

step 'the nudge never disrupts the compaction it is attached to'
(
set -euo pipefail
# PreCompact cannot cancel a compaction, so the hook has nothing to gain by
# failing loudly and everything to lose: a non-zero exit surfaces an error at
# the exact moment the user needs the compaction to proceed.
for payload in 'not json' '[]' 'null' '' '{"trigger":' '"a string"'; do
  set +e
  out="$(printf '%s' "$payload" | bash "$HOOK" 2>&1)"
  rc=$?
  set -e
  if [ "$rc" != 0 ]; then
    echo "FAIL: payload ${payload:-<empty>} exited $rc — a nudge must never fail"
    printf '%s\n' "$out"
    exit 1
  fi
  # Whatever it prints must be parseable, or Claude Code sees a broken hook.
  if [ -n "$out" ]; then
    printf '%s' "$out" | python3 -c 'import json,sys; json.load(sys.stdin)' \
      || { echo "FAIL: payload ${payload:-<empty>} emitted non-JSON: $out"; exit 1; }
  fi
done
echo "PASS: malformed input exits 0 and prints either nothing or valid JSON"
)

step 'no hook smuggles a quote out of its python block'
(
set -euo pipefail
# These hooks wrap a python program in a SINGLE-quoted `python3 -c '...'`, so a
# single quote anywhere inside it closes the wrapper early and hands the rest of
# the program to bash. The hooks say so in their own comments; this checks it,
# because the failure is a shell injection in a file nobody re-reads.
python3 - <<'PY'
import pathlib, re, sys

failures = []
checked = 0
for hook in sorted(pathlib.Path("plugins").glob("*/hooks/*.sh")):
    text = hook.read_text(encoding="utf-8")
    for match in re.finditer(r"python3 -c '\n(.*?)\n'\n", text, re.S):
        checked += 1
        body = match.group(1)
        if "'" in body:
            line = body[: body.index("'")].count("\n") + 1
            failures.append(f"{hook}: a single quote inside the python block (line ~{line} of it)")

if not checked:
    print("FAIL: no python blocks matched — the pattern is wrong, not the hooks")
    sys.exit(1)

print(f"checked {checked} embedded python block(s)")
for line in failures:
    print(f"FAIL: {line}")
sys.exit(1 if failures else 0)
PY
echo "PASS: every embedded python block keeps its wrapper intact"
)
