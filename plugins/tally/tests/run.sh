#!/usr/bin/env bash
# tally bundled-script tests
#
# Two promises carry this plugin, and both fail silently when broken. The wrapper
# must never change what a hook decided: its stdout, stderr and exit code pass
# through untouched, and a logging failure is swallowed. The aggregator must
# never let text out: its output holds counts, dates and schema-fixed labels
# only. The suite therefore feeds both of them values that must not leak, and
# fails if any of those values shows up.
#
# Run directly (`bash plugins/tally/tests/run.sh`) or through the whole suite
# (`bash tests/run.sh`). CI calls this same file.
set -euo pipefail
. "$(dirname "${BASH_SOURCE[0]:-$0}")/../../../tests/lib.sh"

PLUGIN="$ROOT/plugins/tally"
WRAP="$PLUGIN/scripts/log-wrap.sh"
TALLY="$PLUGIN/scripts/tally.py"
SCHEMA="$PLUGIN/schema/tally-v0.schema.json"

work="$(mktemp -d)"
trap 'chmod -R u+w "$work" 2>/dev/null; rm -rf "$work"' EXIT
export TALLY_LOG_DIR="$work/log"

failures=0
check() {
  if [ "$2" != "$3" ]; then
    printf 'FAIL: %s\n  want: %s\n  got:  %s\n' "$1" "$3" "$2"
    failures=$((failures + 1))
  fi
}

mkhook() {
  printf '#!/usr/bin/env bash\n# tally: tier=%s\n%s\n' "$2" "$3" >"$work/$1"
  chmod +x "$work/$1"
}

last_field() {
  python3 -c '
import glob, json, sys
rows = [json.loads(l) for f in sorted(glob.glob(sys.argv[1] + "/decisions-*.jsonl")) for l in open(f)]
v = rows[-1].get(sys.argv[2])
print(json.dumps(v) if isinstance(v, (dict, list)) or v is None else v)
' "$TALLY_LOG_DIR" "$1"
}

log_lines() {
  cat "$TALLY_LOG_DIR"/decisions-*.jsonl 2>/dev/null | wc -l | tr -d ' '
}

payload='{"hook_event_name":"PreToolUse","tool_name":"Bash","session_id":"s1","tool_use_id":"t1","cwd":"/x","tool_input":{"command":"git commit -m leaked-command-text"}}'

step 'log-wrap passes a hook through unchanged'
mkhook silent.sh warn 'cat >/dev/null; exit 0'
out="$(printf '%s' "$payload" | "$WRAP" "$work/silent.sh")" && rc=0 || rc=$?
check silent.rc "$rc" 0
check silent.out "$out" ""
check silent.not-logged "$(log_lines)" 0

mkhook echo.sh warn 'cat'
out="$(printf '%s' "$payload" | "$WRAP" "$work/echo.sh")"
check stdin.reaches-hook "$out" "$payload"
rm -rf "$TALLY_LOG_DIR"

mkhook block.sh block 'cat >/dev/null; echo "BLOCKED: reason here" >&2; exit 2'
err="$(printf '%s' "$payload" | "$WRAP" "$work/block.sh" 2>&1 >/dev/null)" && rc=0 || rc=$?
check block.rc "$rc" 2
check block.stderr "$err" "BLOCKED: reason here"
check block.decision "$(last_field decision)" block
check block.reason "$(last_field reason)" "BLOCKED: reason here"
echo "checked pass-through"

step 'log-wrap records a decision without the command text'
ask_json='{"hookSpecificOutput":{"hookEventName":"PreToolUse","permissionDecision":"ask","permissionDecisionReason":"release guard"}}'
mkhook ask.sh ask "cat >/dev/null; printf '%s' '$ask_json'"
out="$(printf '%s' "$payload" | "$WRAP" "$work/ask.sh")"
check ask.out "$out" "$ask_json"
check ask.decision "$(last_field decision)" ask
check ask.tier "$(last_field tier)" ask
check ask.cmd_head "$(last_field cmd_head)" git
check ask.cmd_sha-length "$(last_field cmd_sha | tr -d '\n' | wc -c | tr -d ' ')" 12
check ask.no-command-text "$(grep -c 'leaked-command-text' "$TALLY_LOG_DIR"/decisions-*.jsonl || true)" 0
check ask.no-cwd "$(last_field cwd)" null

nudge_json='{"systemMessage":"consider the pair"}'
printf '#!/usr/bin/env bash\ncat >/dev/null; printf %%s %q\n' "$nudge_json" >"$work/untagged.sh"
chmod +x "$work/untagged.sh"
printf '%s' "$payload" | "$WRAP" "$work/untagged.sh" >/dev/null
check untagged.tier "$(last_field tier)" null
check untagged.decision "$(last_field decision)" nudge
printf '%s' "$payload" | "$WRAP" --tier WARN "$work/untagged.sh" >/dev/null
check flag.tier "$(last_field tier)" warn
echo "checked the record"

step 'log-wrap fails open'
check missing-hook.rc "$("$WRAP" "$work/does-not-exist.sh" </dev/null; echo $?)" 0
check no-args.rc "$("$WRAP" </dev/null; echo $?)" 0
mkdir -p "$work/ro" && chmod 0555 "$work/ro"
out="$(printf '%s' "$payload" | TALLY_LOG_DIR="$work/ro/log" "$WRAP" "$work/ask.sh" 2>"$work/ro-err")" && rc=0 || rc=$?
check unwritable.rc "$rc" 0
check unwritable.out "$out" "$ask_json"
check unwritable.quiet "$(wc -c <"$work/ro-err" | tr -d ' ')" 0
echo "checked failure paths"

step 'the mutating-command classifier'
python3 - "$PLUGIN/scripts" <<'PY' || failures=$((failures + 1))
import sys
sys.path.insert(0, sys.argv[1])
import tally

cases = {
    # reads
    "git status && git diff --stat": None,
    "git tag -l": None,
    "git tag --sort=-creatordate": None,
    "git apply --cached --check -v /tmp/p.patch": None,
    "gh release create --help | grep x": None,
    "gh api repos/o/r/pulls": None,
    "gh api graphql -f query='{ viewer { login } }'": None,
    "grep -nE 'crictl|mv|rm' notes.sh": None,
    "kubectl rollout status deploy/x": None,
    "echo hi > /dev/null 2>&1": None,
    "sed -n 1,5p f.txt": None,
    "cat > /tmp/a.py <<'EOF'\nrm -rf /\ngit push\nEOF": None,
    # workspace changes
    "git commit -m 'x'": "workspace",
    "git tag -a v1.0.0 -m x": "workspace",
    "gh pr create --fill": "workspace",
    "gh api repos/o/r/issues -f title=x": "workspace",
    "gh api -X DELETE repos/o/r": "workspace",
    "gh api graphql -f query='mutation { addStar }'": "workspace",
    "rm -f README.md": "workspace",
    "sed -i '' s/a/b/ f.txt": "workspace",
    "cp /tmp/a.sh README.md": "workspace",
    "terraform apply plan.out": "workspace",
    "if true; then git push; fi": "workspace",
    "rm -f /tmp/x; rm -f y": "workspace",
    # throwaway work
    "git -C /tmp/r commit -m x": "scratch",
    "cd /tmp/x && rm -rf build && mkdir build": "scratch",
    "d=$(mktemp -d); cp a.sh \"$d/s.sh\"": "scratch",
    "(cd /tmp/x && rm -rf b)": "scratch",
}
bad = [(c, w, tally.classify(c)) for c, w in cases.items() if tally.classify(c) != w]
for c, w, g in bad:
    print(f"FAIL: {c!r}: expected {w}, got {g}")
print(f"checked {len(cases)} commands")
sys.exit(1 if bad else 0)
PY

step 'the aggregator counts a synthetic harness and leaks none of it'
python3 - "$PLUGIN/scripts" "$TALLY" "$SCHEMA" "$work/fx" <<'PY' || failures=$((failures + 1))
import json, os, subprocess, sys
from pathlib import Path

scripts, tally_py, schema_path, fx = sys.argv[1], sys.argv[2], sys.argv[3], Path(sys.argv[4])
sys.path.insert(0, scripts)
import tally

# Every string below that a leak would expose carries this marker.
M = "zzleak"
cfg = fx / "cfg"
(cfg / "agents").mkdir(parents=True)
(cfg / "agents" / f"{M}-runner.md").write_text(f"---\nname: {M}-runner\ndescription: 'x'\ntools: Bash, Read\n---\nbody\n")
(cfg / "agents" / f"{M}-writer.md").write_text(f"---\nname: {M}-writer\ndescription: 'x'\ntools: Read, Edit\n---\n")
(cfg / "agents" / f"{M}-gitonly.md").write_text(f"---\nname: {M}-gitonly\ndescription: 'x'\ntools: Bash(git:*), Read\n---\n")
(cfg / "agents" / f"{M}-open.md").write_text(f"---\nname: {M}-open\ndescription: 'x'\n---\n")
(cfg / "commands" / "sub").mkdir(parents=True)
(cfg / "commands" / f"{M}.md").write_text("x")
(cfg / "commands" / "sub" / "b.md").write_text("x")
(cfg / "skills" / f"{M}-skill").mkdir(parents=True)
(cfg / "skills" / f"{M}-skill" / "SKILL.md").write_text("x")
(cfg / "settings.json").write_text(json.dumps({"hooks": {
    "PreToolUse": [{"matcher": "Bash", "hooks": [{"type": "command", "command": f"/{M}/a.sh"},
                                                   {"type": "command", "command": f"/{M}/b.sh"}]}],
    f"{M}Event": [{"hooks": [{"type": "command", "command": "c"}]}],
}}))

proj = fx / "projects" / f"-home-{M}-project"
sess = proj / "s1"
(sess / "subagents").mkdir(parents=True)

def line(ts, **rec):
    rec.setdefault("timestamp", ts)
    return json.dumps(rec) + "\n"

def tool(ts, name, **inp):
    return line(ts, type="assistant", message={"content": [{"type": "tool_use", "name": name, "input": inp}]})

W1, W2 = "2026-11-02T10:00:00Z", "2026-11-09T10:00:00Z"   # ISO weeks 44 and 46
(proj / "s1.jsonl").write_text("".join([
    line(W1, type="user", message={"content": f"please look at /home/{M}/secret"}),
    tool(W1, "Bash", command=f"git commit -m '{M} message'"),
    tool(W1, "Bash", command=f"ls /home/{M}"),
    tool(W1, "Edit", file_path=f"/home/{M}/f"),
    tool(W1, "AskUserQuestion", questions=[]),
    tool(W1, "Agent", subagent_type=f"{M}-runner", prompt=M),
    line(W1, type="user", permissionDecision={"decision": "accept", "source": "config"}, message={"content": []}),
    line(W1, type="user", permissionDecision={"decision": "accept", "source": "user_temporary"}, message={"content": []}),
    line(W1, type="user", permissionDecision={"decision": "reject", "source": "hook"}, message={"content": []}),
    line(W1, type="user", message={"content": [{"type": "tool_result", "is_error": True,
         "content": "The user doesn't want to proceed with this tool use. The tool use was rejected"}]}),
    line(W1, type="user", message={"content": [{"type": "text", "text": "[Request interrupted by user]"}]}),
    line(W1, type="attachment", attachment={"type": "hook_non_blocking_error", "hookName": M}),
    line(W1, type="attachment", attachment={"type": f"hook_{M}", "hookName": M}),
    line(W1, type="system", subtype="compact_boundary"),
    '{"type":"api-request-blob","data":"' + M + '"}\n',
    "not json " + M + "\n",
    tool(W2, "Bash", command="cd /tmp/x && rm -rf build"),
    tool("2026-01-01T00:00:00Z", "Bash", command="rm -rf old"),
]))
(sess / "subagents" / "agent-a1.jsonl").write_text("".join([
    tool(W1, "Bash", command=f"gh pr create --title {M}"),
    tool(W1, "Bash", command="git status"),
]))
(sess / "subagents" / "agent-a1.meta.json").write_text(json.dumps({"agentType": f"{M}-runner", "isBuiltIn": False}))
(sess / "subagents" / "agent-a2.jsonl").write_text(tool(W2, "Bash", command="git diff"))
(sess / "subagents" / "agent-a2.meta.json").write_text(json.dumps({"agentType": f"{M}-runner", "isBuiltIn": False}))
(sess / "subagents" / "agent-a3.jsonl").write_text(tool(W2, "Write", file_path=f"/{M}"))
(sess / "subagents" / "agent-a3.meta.json").write_text(json.dumps({"agentType": "general-purpose", "isBuiltIn": True}))

log = fx / "log"
log.mkdir()
(log / "decisions-2026-11.jsonl").write_text("".join(json.dumps(r) + "\n" for r in [
    {"ts": W1, "hook": f"{M}.sh", "tier": "ask", "decision": "ask", "reason": M},
    {"ts": W2, "hook": f"{M}.sh", "tier": None, "decision": "block"},
    {"ts": W2, "hook": f"{M}.sh", "tier": M, "decision": M},
]))

out = fx / "out.json"
subprocess.run([sys.executable, tally_py, "--projects", str(fx / "projects"), "--log-dir", str(log),
                "--config", str(cfg), "--since", "2026-10-01", "--out", str(out)], check=True)
text = out.read_text()
doc = json.loads(text)
fails = []

def expect(path, want):
    node = doc
    for key in path.split("."):
        node = node[int(key)] if isinstance(node, list) else node[key]
    if node != want:
        fails.append(f"{path}: expected {want!r}, got {node!r}")

expect("window.first", "2026-11-02")
expect("window.weeks", 2)
expect("input.unrecognized_lines", 1)
expect("harness.agents.total", 4)
expect("harness.agents.write_capable", 1)
expect("harness.agents.inherit_all_tools", 1)
expect("harness.commands", 2)
expect("harness.skills", 1)
expect("harness.hooks.total", 3)
expect("harness.hooks.by_event.PreToolUse", 2)
expect("harness.hooks.by_event.other", 1)
expect("totals.sessions", 1)
expect("totals.main.tool_calls", 6)
expect("totals.main.file_edit_calls", 1)
expect("totals.main.mutating_bash_calls", 1)
expect("totals.main.scratch_bash_calls", 1)
expect("totals.main.questions_asked", 1)
expect("totals.main.subagent_launches", 1)
expect("totals.main.interrupts", 1)
expect("totals.main.compactions", 1)
expect("totals.permissions.auto_allowed", 1)
expect("totals.permissions.user_allowed_once", 1)
expect("totals.permissions.hook_denied", 1)
expect("totals.permissions.user_rejected", 1)
expect("totals.subagents.runs", 3)
expect("totals.subagents.runs_custom", 2)
expect("totals.subagents.runs_with_mutation", 2)
expect("totals.subagents.runs_readonly_declared", 2)
expect("totals.subagents.runs_readonly_declared_with_mutation", 1)
expect("totals.subagents.readonly_declared_mutating_calls", 1)
expect("totals.subagents.file_edit_calls", 1)
expect("totals.hooks_transcript.non_blocking_error", 1)
expect("totals.hooks_transcript.other", 1)
expect("totals.hooks_log.decisions.ask", 1)
expect("totals.hooks_log.decisions.block", 1)
expect("totals.hooks_log.decisions.other", 1)
expect("totals.hooks_log.by_tier.ask", 1)
expect("totals.hooks_log.by_tier.untiered", 1)
expect("totals.hooks_log.by_tier.other", 1)
expect("weeks.0.week", "2026-W45")
expect("weeks.1.week", "2026-W46")

for needle in (M, "/home", "/tmp", "commit", "runner", "general-purpose"):
    if needle in text:
        fails.append(f"output contains {needle!r}")

# A small validator for the subset of JSON Schema the schema file uses.
schema = json.loads(Path(schema_path).read_text())

def resolve(node):
    while "$ref" in node:
        node = schema["$defs"][node["$ref"].rsplit("/", 1)[1]]
    return node

def validate(value, node, where):
    node = resolve(node)
    if "anyOf" in node:
        errs = [validate(value, n, where) for n in node["anyOf"]]
        return [] if any(not e for e in errs) else [f"{where}: no anyOf branch matched"]
    if "const" in node:
        return [] if value == node["const"] else [f"{where}: expected {node['const']!r}"]
    kind = node.get("type")
    if kind == "object":
        if not isinstance(value, dict):
            return [f"{where}: not an object"]
        errs = [f"{where}.{k}: missing" for k in node.get("required", []) if k not in value]
        props = node.get("properties", {})
        for k, v in value.items():
            if k not in props:
                errs.append(f"{where}.{k}: not in schema")
            else:
                errs += validate(v, props[k], f"{where}.{k}")
        return errs
    if kind == "array":
        if not isinstance(value, list):
            return [f"{where}: not an array"]
        return [e for i, v in enumerate(value) for e in validate(v, node["items"], f"{where}[{i}]")]
    if kind == "integer":
        ok = isinstance(value, int) and not isinstance(value, bool) and value >= node.get("minimum", value)
        return [] if ok else [f"{where}: not a non-negative integer"]
    if kind == "string":
        import re
        ok = isinstance(value, str) and re.fullmatch(node.get("pattern", ".*"), value)
        return [] if ok else [f"{where}: string does not match"]
    if kind == "null":
        return [] if value is None else [f"{where}: not null"]
    return [f"{where}: unsupported schema node"]

fails += validate(doc, schema, "$")

# The schema is written by hand from the code's shape: keep the two in step.
def shape(value):
    if isinstance(value, dict):
        return {"type": "object", "additionalProperties": False,
                "required": list(value), "properties": {k: shape(v) for k, v in value.items()}}
    return {"type": "integer", "minimum": 0}

if schema["$defs"]["bucket"] != shape(tally._zero_bucket()):
    fails.append("schema $defs.bucket no longer matches _zero_bucket() in tally.py")
if schema["properties"]["harness"] != shape(tally.harness_shape([])[0]):
    fails.append("schema properties.harness no longer matches harness_shape() in tally.py")
if schema["properties"]["schema"]["const"] != tally.SCHEMA_ID:
    fails.append("schema id differs from tally.SCHEMA_ID")
if schema["properties"]["classifier"]["const"] != tally.CLASSIFIER_ID:
    fails.append("classifier id differs from tally.CLASSIFIER_ID")

for f in fails:
    print("FAIL:", f)
print(f"checked {len(text)} bytes of output")
sys.exit(1 if fails else 0)
PY

step 'the manifest version is the one the aggregator reports'
manifest="$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["version"])' "$PLUGIN/.claude-plugin/plugin.json")"
reported="$(python3 -c 'import sys; sys.path.insert(0, sys.argv[1]); import tally; print(tally.TOOL_VERSION)' "$PLUGIN/scripts")"
check tool-version "$reported" "$manifest"
echo "checked $manifest"

if [ "$failures" -ne 0 ]; then
  printf '\n%s check(s) failed\n' "$failures"
  exit 1
fi
