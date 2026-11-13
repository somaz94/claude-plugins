#!/usr/bin/env bash
# commit-gates bundled-content tests
#
# This plugin ships no script: its whole contract is an authority split written
# into an agent and a command. A drift there is silent. An Edit tool slipped back
# into the agent's `tools:` line, or a KO mirror that lost the plan-only section,
# still loads and still answers; the agent just quietly regains the power to act.
# So the suite pins the frontmatter and the section text, not behavior.
#
# Run directly (`bash plugins/commit-gates/tests/run.sh`) or through the whole
# suite (`bash tests/run.sh`). CI calls this same file.
set -euo pipefail
. "$(dirname "${BASH_SOURCE[0]:-$0}")/../../../tests/lib.sh"

PLUGIN="$ROOT/plugins/commit-gates"

step 'the planner holds no file-write tool, in either language'
python3 - "$PLUGIN" <<'PY'
import pathlib, re, sys

root = pathlib.Path(sys.argv[1])
WRITE_TOOLS = {"Edit", "Write", "MultiEdit", "NotebookEdit", "*"}
failures = []
for rel in ("agents/git-commit-runner.md", "agents-ko/git-commit-runner.md"):
    text = (root / rel).read_text(encoding="utf-8")
    block = text.split("---", 2)[1]
    m = re.search(r"^tools:\s*(.+)$", block, re.M)
    if not m:
        # No tools line means the agent inherits every tool, Edit included.
        failures.append(f"{rel}: no tools line, so the agent inherits every tool")
        continue
    granted = set(re.findall(r"[A-Za-z*]+", m.group(1)))
    leaked = granted & WRITE_TOOLS
    if leaked:
        failures.append(f"{rel}: tools grants {sorted(leaked)}")
for line in failures:
    print(f"FAIL: {line}")
print("checked 2 agent files")
sys.exit(1 if failures else 0)
PY

step 'the plan-only section is present in both languages'
python3 - "$PLUGIN" <<'PY'
import pathlib, sys

root = pathlib.Path(sys.argv[1])
checks = {
    "agents/git-commit-runner.md": ["# Mutating steps: plan, never run", "relayed", "Gate 1", "Gate 2"],
    "agents-ko/git-commit-runner.md": ["# 변경 단계: 계획만 하고 실행하지 않는다", "전달", "Gate 1", "Gate 2"],
}
failures = []
for rel, needles in checks.items():
    text = (root / rel).read_text(encoding="utf-8")
    for needle in needles:
        if needle not in text:
            failures.append(f"{rel}: missing {needle!r}")
for line in failures:
    print(f"FAIL: {line}")
print(f"checked {len(checks)} files")
sys.exit(1 if failures else 0)
PY

step 'the command asks each gate and runs the approved command itself'
python3 - "$PLUGIN" <<'PY'
import pathlib, re, sys

root = pathlib.Path(sys.argv[1])
failures = []
for rel in ("commands/commit.md", "commands-ko/commit.md"):
    text = (root / rel).read_text(encoding="utf-8")
    block = text.split("---", 2)[1]
    m = re.search(r"^allowed-tools:\s*(.+)$", block, re.M)
    tools = set(re.findall(r"[A-Za-z]+", m.group(1))) if m else set()
    for needed in ("Bash", "AskUserQuestion"):
        if needed not in tools:
            failures.append(f"{rel}: allowed-tools lacks {needed}")
    # Both gates, asked one at a time, in the body as well.
    for needle in ("Gate 1", "Gate 2", "AskUserQuestion", "git-commit-runner"):
        if needle not in text:
            failures.append(f"{rel}: body never mentions {needle!r}")
    if "--no-verify" not in text:
        failures.append(f"{rel}: the --no-verify rule is gone")
for line in failures:
    print(f"FAIL: {line}")
print("checked 2 command files")
sys.exit(1 if failures else 0)
PY

step 'frontmatter parses as strict YAML-shaped key: value with quoted descriptions'
python3 - "$PLUGIN" <<'PY'
import pathlib, re, sys

root = pathlib.Path(sys.argv[1])
failures = []
files = sorted(p for d in ("agents", "agents-ko", "commands", "commands-ko") for p in (root / d).glob("*.md"))
for path in files:
    block = path.read_text(encoding="utf-8").split("---", 2)[1]
    m = re.search(r"^description:\s*(.*)$", block, re.M)
    if not m:
        failures.append(f"{path.relative_to(root)}: no description")
        continue
    value = m.group(1).strip()
    # A description almost always carries a `: ` or a backtick; unquoted, that
    # breaks every strict YAML reader even though Claude Code repairs it.
    if not (value.startswith("'") and value.endswith("'")):
        failures.append(f"{path.relative_to(root)}: description is not single-quoted")
    elif re.search(r"(?<!')'(?!')", value[1:-1]):
        failures.append(f"{path.relative_to(root)}: an apostrophe inside the description is not doubled")
for line in failures:
    print(f"FAIL: {line}")
print(f"checked {len(files)} files")
sys.exit(1 if failures else 0)
PY

step 'EN and KO mirrors keep the same heading shape'
python3 - "$PLUGIN" <<'PY'
import pathlib, re, sys

root = pathlib.Path(sys.argv[1])

def shape(path):
    levels, fenced = [], False
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.startswith("```"):
            fenced = not fenced
            continue
        m = re.match(r"^(#{1,6})\s", line)
        if m and not fenced:
            levels.append(len(m.group(1)))
    return levels

pairs = [
    ("agents/git-commit-runner.md", "agents-ko/git-commit-runner.md"),
    ("commands/commit.md", "commands-ko/commit.md"),
    ("README.md", "README-ko.md"),
]
failures = []
for en, ko in pairs:
    a, b = shape(root / en), shape(root / ko)
    if a != b:
        failures.append(f"{en} {a} vs {ko} {b}")
for line in failures:
    print(f"FAIL: heading shape differs: {line}")
print(f"checked {len(pairs)} pairs")
sys.exit(1 if failures else 0)
PY

step 'no personal markers leak into a public plugin'
# Split so this file does not match its own pattern when the repo is scanned.
pattern='/Use''rs/|Private''Work|gitlab''-project|con''crit|\bso''maz\b'
if grep -rnE "$pattern" "$PLUGIN" --include='*.md' --include='*.json'; then
  echo "FAIL: a personal path or account name is in the plugin"
  exit 1
fi
echo "OK"
