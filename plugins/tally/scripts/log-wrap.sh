#!/usr/bin/env bash
#
# log-wrap.sh [--tier <tier>] <hook-script> [args...]
#
# Runs a hook unchanged and, when the hook speaks (non-zero exit or any stdout),
# appends one JSONL decision record to $TALLY_LOG_DIR/decisions-YYYY-MM.jsonl
# (default ~/.claude/tally/hook-decisions).
#
# The hook's stdout, stderr and exit code pass through byte-identical and every
# logging failure is swallowed: a wrapper that can change a hook's verdict is a
# second hook nobody reviewed. Bash command text is never logged, only a short
# hash and its first word. Silent allows are not logged; their denominator is
# the tool-call count in the session transcripts.
#
# The tier comes from --tier, else from a `# tally: tier=<tier>` line in the
# first 4 KB of the hook script, else it is left empty.

set -u

tier_arg=""
if [ "${1:-}" = "--tier" ]; then
  tier_arg="${2:-}"
  shift 2 2>/dev/null || shift $#
fi

hook="${1:-}"
if [ -z "$hook" ] || [ ! -x "$hook" ]; then
  exit 0
fi
shift

tmp="$(mktemp -d 2>/dev/null)" || exec "$hook" "$@"
trap 'rm -rf "$tmp"' EXIT

cat >"$tmp/in"
"$hook" "$@" <"$tmp/in" >"$tmp/out" 2>"$tmp/err"
rc=$?

cat "$tmp/out"
cat "$tmp/err" >&2

if [ "$rc" -ne 0 ] || [ -s "$tmp/out" ]; then
  (
    log_dir="${TALLY_LOG_DIR:-$HOME/.claude/tally/hook-decisions}"
    mkdir -p "$log_dir" &&
      python3 - "$hook" "$rc" "$tmp" "$log_dir" "$tier_arg" <<'PY'
import datetime, hashlib, json, os, re, sys

hook, rc, tmp, log_dir, tier = sys.argv[1], int(sys.argv[2]), sys.argv[3], sys.argv[4], sys.argv[5]

def read(name):
    with open(os.path.join(tmp, name), "r", encoding="utf-8", errors="replace") as fh:
        return fh.read()

try:
    payload = json.loads(read("in") or "{}")
except Exception:
    payload = {}
if not isinstance(payload, dict):
    payload = {}
out, err = read("out"), read("err")
try:
    out_json = json.loads(out) if out.strip() else {}
except Exception:
    out_json = {}
if not isinstance(out_json, dict):
    out_json = {}

if not tier:
    with open(hook, "r", encoding="utf-8", errors="replace") as fh:
        m = re.search(r"^#\s*tally:\s*tier=(\w+)", fh.read(4096), re.M)
    tier = m.group(1) if m else ""
tier = tier.lower() or None

hso = out_json.get("hookSpecificOutput") or {}
perm = hso.get("permissionDecision")
if rc == 2:
    decision = "block"
elif perm:
    decision = perm
elif rc != 0:
    decision = "nonblocking-error"
elif out_json.get("systemMessage") or hso.get("additionalContext"):
    decision = "info" if tier == "info" else "nudge"
else:
    decision = "output"

tin = payload.get("tool_input") or {}
rec = {
    "ts": datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    "hook": os.path.basename(hook),
    "tier": tier,
    "event": payload.get("hook_event_name"),
    "tool": payload.get("tool_name"),
    "decision": decision,
    "rc": rc,
    "session_id": payload.get("session_id"),
    "tool_use_id": payload.get("tool_use_id"),
}
if isinstance(tin, dict):
    if tin.get("file_path"):
        rec["file_path"] = tin["file_path"]
    cmd = tin.get("command")
    if isinstance(cmd, str) and cmd.strip():
        rec["cmd_head"] = cmd.split()[0][:40]
        rec["cmd_sha"] = hashlib.sha256(cmd.encode()).hexdigest()[:12]

reason = hso.get("permissionDecisionReason") or out_json.get("systemMessage")
if not reason and rc != 0:
    reason = err.strip().splitlines()[0] if err.strip() else None
if reason:
    rec["reason"] = reason[:200]

path = os.path.join(log_dir, "decisions-%s.jsonl" % rec["ts"][:7])
with open(path, "a", encoding="utf-8") as fh:
    fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
PY
  ) >/dev/null 2>&1 || true
fi

exit "$rc"
