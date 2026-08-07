#!/usr/bin/env bash
# Repo-wide manifest and frontmatter consistency
#
# The bodies below are the same text .github/workflows/ci.yml runs; the
# workflow calls this file rather than carrying them inline, so the answer
# to 'will CI pass?' is available before pushing.
set -euo pipefail
. "$(dirname "${BASH_SOURCE[0]:-$0}")/../tests/lib.sh"

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
