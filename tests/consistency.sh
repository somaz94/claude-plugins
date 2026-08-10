#!/usr/bin/env bash
# Repo-wide manifest and frontmatter consistency
#
# The bodies below are the same text .github/workflows/ci.yml runs; the
# workflow calls this file rather than carrying them inline, so the answer
# to 'will CI pass?' is available before pushing.
set -euo pipefail
. "$(dirname "${BASH_SOURCE[0]:-$0}")/../tests/lib.sh"

step 'Vendored _shared.py is byte-identical everywhere'
(
python3 - <<'PY'
import hashlib, pathlib, sys

# A plugin is installed on its own, so it cannot import from a sibling: the
# shared helpers are vendored into each one instead. Vendoring only stays honest
# if something fails when the copies drift — and drift is exactly what put this
# check here. Two hand-written frontmatter parsers had already disagreed about
# `''` unescaping, and two hand-written document-shape counters about whether a
# `#` inside a fenced block is a heading.
#
# `bash tests/sync-shared.sh` propagates the canonical copy to the others.
CANONICAL = pathlib.Path("plugins/census/scripts/_shared.py")

copies = sorted(pathlib.Path("plugins").glob("*/scripts/_shared.py"))
if CANONICAL not in copies:
    print(f"FAIL: the canonical copy {CANONICAL} is missing")
    sys.exit(1)

def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()

want = digest(CANONICAL)
failures = [
    f"{copy} differs from {CANONICAL} — run `bash tests/sync-shared.sh`"
    for copy in copies
    if digest(copy) != want
]

print(f"checked {len(copies)} vendored cop{'y' if len(copies) == 1 else 'ies'}")
for line in failures:
    print(f"FAIL: {line}")
sys.exit(1 if failures else 0)
PY
)

step 'Every plugin that vendors _shared.py actually imports it'
(
python3 - <<'PY'
import pathlib, sys

# The copies being identical says nothing about whether they are used. A script
# that keeps its own inlined version alongside an unused vendored one is exactly
# the state this refactor removed, and it would pass the hash check silently.
failures = []
checked = 0
for shared in sorted(pathlib.Path("plugins").glob("*/scripts/_shared.py")):
    scripts = [p for p in sorted(shared.parent.glob("*.py")) if p.name != "_shared.py"]
    if not scripts:
        failures.append(f"{shared}: vendored beside no script that could use it")
        continue
    if not any("from _shared import" in p.read_text(encoding="utf-8") for p in scripts):
        failures.append(f"{shared}: no script in {shared.parent} imports it")
    checked += 1

print(f"checked {checked} plugin(s)")
for line in failures:
    print(f"FAIL: {line}")
sys.exit(1 if failures else 0)
PY
)

step 'Marketplace entry version matches plugin.json'
(
python3 - <<'PY'
import json, pathlib, sys

market = json.loads(pathlib.Path(".claude-plugin/marketplace.json").read_text())
root = market.get("metadata", {}).get("pluginRoot", ".")
failures = []

for entry in market["plugins"]:
    source = entry["source"]
    if not isinstance(source, str):
        continue  # remote source; nothing local to compare against
    path = (pathlib.Path(root) / source).resolve()
    manifest = path / ".claude-plugin" / "plugin.json"
    if not manifest.is_file():
        failures.append(f"{entry['name']}: no plugin.json at {manifest}")
        continue
    plugin = json.loads(manifest.read_text())
    if entry.get("version") != plugin.get("version"):
        failures.append(
            f"{entry['name']}: marketplace={entry.get('version')!r} "
            f"!= plugin.json={plugin.get('version')!r}"
        )
    if entry["name"] != plugin.get("name"):
        failures.append(
            f"{entry['name']}: marketplace name != plugin.json name "
            f"{plugin.get('name')!r}"
        )

for line in failures:
    print(f"FAIL: {line}")
sys.exit(1 if failures else 0)
PY
)

step 'Every skill and agent declares a description'
(
python3 - <<'PY'
import pathlib, re, sys

plugins = pathlib.Path("plugins")
# A command is invoked by its FILENAME, so it needs no `name:` field.
# A skill and an agent are both addressed by a declared name.
targets = (
    [(p, True) for p in sorted(plugins.glob("*/skills/*/SKILL.md"))]
    + [(p, True) for p in sorted(plugins.glob("*/agents/*.md"))]
    + [(p, False) for p in sorted(plugins.glob("*/commands/*.md"))]
)
if not targets:
    print("FAIL: nothing found — the globs are wrong")
    sys.exit(1)

failures = []
for target, needs_name in targets:
    text = target.read_text(encoding="utf-8")
    if not text.startswith("---"):
        failures.append(f"{target}: no frontmatter")
        continue
    block = text.split("---", 2)[1]
    if not re.search(r"^description:\s*\S", block, re.M):
        failures.append(f"{target}: no description in frontmatter")
    if needs_name and not re.search(r"^name:\s*\S", block, re.M):
        failures.append(f"{target}: no name in frontmatter")

print(f"checked {len(targets)} items")
for line in failures:
    print(f"FAIL: {line}")
sys.exit(1 if failures else 0)
PY
)

step 'Every plugin hook resolves to an executable script'
(
python3 - <<'PY'
import json, os, pathlib, sys

# Built at runtime, never written literally: a doubled brace in this
# file is read by GitHub Actions as an expression before the step ever
# runs, and an unknown one makes the whole workflow invalid.
PLUGIN_ROOT = "${" + "CLAUDE_PLUGIN_ROOT}"

failures = []
manifests = sorted(pathlib.Path("plugins").glob("*/hooks/hooks.json"))
for manifest in manifests:
    root = manifest.parent.parent
    try:
        data = json.loads(manifest.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        failures.append(f"{manifest}: invalid JSON ({exc})")
        continue
    for event, entries in (data.get("hooks") or {}).items():
        for entry in entries:
            for hook in entry.get("hooks", []):
                command = hook.get("command", "")
                if "${CLAUDE_PLUGIN_ROOT}" not in command:
                    failures.append(
                        f"{manifest}: {event} command is not rooted at "
                        f"{PLUGIN_ROOT}: {command!r}"
                    )
                    continue
                rel = command.split("${CLAUDE_PLUGIN_ROOT}/", 1)[1].split()[0]
                target = root / rel
                if not target.is_file():
                    failures.append(f"{manifest}: {event} points at a missing {target}")
                elif not os.access(target, os.X_OK):
                    failures.append(f"{manifest}: {target} is not executable")

print(f"checked {len(manifests)} hook manifest(s)")
for line in failures:
    print(f"FAIL: {line}")
sys.exit(1 if failures else 0)
PY
)

step 'The shared helpers behave the same in every plugin that vendors them'
(
python3 - <<'PY'
import importlib.util, pathlib, sys

# Byte-identity says the copies match. This says they do the RIGHT thing — the
# two behaviours that had already diverged between hand-written duplicates, now
# pinned so a future edit to the canonical copy cannot quietly undo either.
def load(path):
    spec = importlib.util.spec_from_file_location(f"shared_{path.parts[1]}", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module

failures = []
copies = sorted(pathlib.Path("plugins").glob("*/scripts/_shared.py"))

# A single-quoted YAML scalar doubles its apostrophes, which is exactly what
# this repo's frontmatter convention requires. Stripping the quotes without
# undoing the escape shows `don''t` to a human as if it were the text.
QUOTED = "---\ndescription: 'Use when you don''t want a `: ` to break YAML'\n---\nbody\n"
WANT_DESC = "Use when you don't want a `: ` to break YAML"

# A `#` inside a fenced block is a shell comment, not a heading. Counting it
# made every README that documents its own CLI look structurally different from
# a faithful translation of itself.
FENCED = "# Title\n\n## Install\n\n```bash\n# not a heading\n```\n\n### Notes\n"

for path in copies:
    shared = load(path)
    fields, offset = shared.parse_frontmatter(QUOTED)
    if fields.get("description") != WANT_DESC:
        failures.append(f"{path}: quoted scalar not unescaped: {fields.get('description')!r}")
    if QUOTED[offset:].lstrip("\n") != "body\n":
        failures.append(f"{path}: body offset lands at {QUOTED[offset:]!r}")

    shape = shared.measure(FENCED)
    if shape.headings != 3:
        failures.append(f"{path}: counted {shape.headings} headings in a doc with 3")
    if shape.code_blocks != 1:
        failures.append(f"{path}: counted {shape.code_blocks} code blocks in a doc with 1")

print(f"checked {len(copies)} cop{'y' if len(copies) == 1 else 'ies'}")
for line in failures:
    print(f"FAIL: {line}")
sys.exit(1 if failures else 0)
PY
)
