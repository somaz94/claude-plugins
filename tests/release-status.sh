#!/usr/bin/env bash
# Which plugins ship a version that was never tagged. marketplace.json is what
# users receive, so a missed tag is silent until the next release's notes swallow
# both ranges (git-cliff starts from that plugin's last tag).
# Never fails: the bump commit legitimately reaches main before its tag.
set -euo pipefail
. "$(dirname "${BASH_SOURCE[0]:-$0}")/lib.sh"

step 'Plugins shipping an untagged version'

python3 - <<'PY'
import json, pathlib, subprocess, sys

tags = set(
    subprocess.run(
        ["git", "tag", "-l"], capture_output=True, text=True, check=False
    ).stdout.split()
)
if not tags:
    # A shallow CI checkout fetches no tags, and reporting every plugin as
    # untagged there would be a wall of false alarms.
    print("SKIP: no tags in this checkout — nothing to compare against")
    sys.exit(0)

market = json.loads(pathlib.Path(".claude-plugin/marketplace.json").read_text())
rows = []
for entry in market["plugins"]:
    name, version = entry["name"], entry.get("version", "")
    mine = sorted(t for t in tags if t.startswith(f"{name}-v"))
    rows.append((name, version, f"{name}-v{version}" in tags, mine[-1] if mine else "—"))

width = max(len(r[0]) for r in rows)
for name, version, tagged, latest in rows:
    mark = "  " if tagged else "! "
    print(f"{mark}{name:<{width}}  shipped {version:<8}  latest tag {latest}")

missing = [(n, v) for n, v, tagged, _ in rows if not tagged]
if not missing:
    print("\nEvery shipped version has a tag.")
    sys.exit(0)

print(f"\n{len(missing)} shipped version(s) never tagged. Their changes are already")
print("published; only the release history is missing. To close the gap:")
for name, version in missing:
    print(f'  git tag -a {name}-v{version} -m "{name} {version} — <summary>"')
print("  git push origin " + " ".join(f"{n}-v{v}" for n, v in missing))
PY
