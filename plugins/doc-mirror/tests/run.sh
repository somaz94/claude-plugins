#!/usr/bin/env bash
# doc-mirror bundled-script tests
#
# Run directly (`bash plugins/doc-mirror/tests/run.sh`) or through the whole
# suite (`bash tests/run.sh`). CI calls this same file.
set -euo pipefail
. "$(dirname "${BASH_SOURCE[0]:-$0}")/../../../tests/lib.sh"

DM="python3 plugins/doc-mirror/scripts/docmirror.py"
HOOK="plugins/doc-mirror/hooks/pair-lockstep-nudge.sh"

step 'doc-mirror finds the mirror that was never written'
(
set -euo pipefail
fixture="$(mktemp -d)/repo"
mkdir -p "$fixture/docs"
# Four documents, three of them mirrored. The fourth is the finding.
for name in alpha beta gamma; do
  printf '# %s\n\n## One\n\ntext\n' "$name" > "$fixture/docs/$name.md"
  printf '# %s\n\n## One\n\n텍스트\n' "$name" > "$fixture/docs/$name-ko.md"
done
printf '# delta\n\n## One\n\ntext\n' > "$fixture/docs/delta.md"

$DM "$fixture" --json > "$fixture/out.json"
python3 - "$fixture/out.json" <<'PY'
import json, sys
r = json.load(open(sys.argv[1]))
failures = []
if r["languages"] != ["ko"]: failures.append(f"languages: {r['languages']}")
if r["pairs"] != 4: failures.append(f"expected 4 source documents, got {r['pairs']}")
if r["mirrors"] != 3: failures.append(f"expected 3 mirrors, got {r['mirrors']}")
missing = [f for f in r["findings"] if f["code"] == "missing-mirror"]
if len(missing) != 1:
    failures.append(f"expected 1 missing-mirror, got {[f['file'] for f in missing]}")
elif missing[0]["file"] != "docs/delta.md":
    failures.append(f"wrong file flagged: {missing[0]['file']}")
elif missing[0]["severity"] != "critical":
    failures.append(f"3 of 4 mirrored should be strong evidence, got {missing[0]['severity']}")
elif "3 of 4" not in missing[0]["message"]:
    failures.append(f"the evidence ratio is not in the message: {missing[0]['message']}")
for line in failures: print(f"FAIL: {line}")
sys.exit(1 if failures else 0)
PY
echo "PASS: the unmirrored document is found and the evidence is quoted"
)

step 'doc-mirror stays quiet where mirroring is the exception'
(
set -euo pipefail
fixture="$(mktemp -d)/repo"
mkdir -p "$fixture/docs"
# One pair among many documents: translating the rest is clearly not the plan.
printf '# a\n' > "$fixture/docs/a.md"
printf '# a\n' > "$fixture/docs/a-ko.md"
for name in b c d e f; do printf '# %s\n' "$name" > "$fixture/docs/$name.md"; done

$DM "$fixture" --json > "$fixture/out.json"
python3 - "$fixture/out.json" <<'PY'
import json, sys
r = json.load(open(sys.argv[1]))
missing = [f for f in r["findings"] if f["code"] == "missing-mirror"]
failures = []
if not missing:
    failures.append("a lone pair produced no finding at all — the gap should still be mentioned")
if any(f["severity"] == "critical" for f in missing):
    failures.append("1 of 6 mirrored was treated as strong evidence")
if not all("may be deliberate" in f["message"] for f in missing):
    failures.append("the weak-evidence wording is missing")
for line in failures: print(f"FAIL: {line}")
sys.exit(1 if failures else 0)
PY
echo "PASS: a lone pair is a warning, not a demand"
)

step 'doc-mirror does not invent a language out of a file name'
(
set -euo pipefail
fixture="$(mktemp -d)/repo"
mkdir -p "$fixture/docs"
# `bit` and `ha` are three and two lowercase letters after a hyphen, exactly
# the shape of a language suffix, and neither is one. Real repositories are
# full of these; treating them as orphans buries every real finding.
printf '# x\n' > "$fixture/docs/06-followup-fluent-bit.md"
printf '# y\n' > "$fixture/docs/scaling-and-ha.md"
printf '# z\n' > "$fixture/docs/setup-v2.md"

$DM "$fixture" --json > "$fixture/out.json"
python3 - "$fixture/out.json" <<'PY'
import json, sys
r = json.load(open(sys.argv[1]))
failures = []
if r["languages"]:
    failures.append(f"invented languages with no pair to prove them: {r['languages']}")
if r["findings"]:
    failures.append(f"reported {[f['code'] + ':' + f['file'] for f in r['findings']]}")
for line in failures: print(f"FAIL: {line}")
sys.exit(1 if failures else 0)
PY

# The same names, once a real pair proves `ko` — still not orphans, because
# neither `bit` nor `ha` is the language this directory actually keeps.
printf '# a\n' > "$fixture/docs/a.md"
printf '# a\n' > "$fixture/docs/a-ko.md"
$DM "$fixture" --json > "$fixture/out2.json"
python3 - "$fixture/out2.json" <<'PY'
import json, sys
r = json.load(open(sys.argv[1]))
orphans = [f for f in r["findings"] if f["code"] == "orphan-mirror"]
if orphans:
    print(f"FAIL: a non-language suffix became an orphan: {[f['file'] for f in orphans]}")
    sys.exit(1)
PY
echo "PASS: a suffix is a language only when a real pair proves it"
)

step 'doc-mirror sees a source that grew a section its mirror did not'
(
set -euo pipefail
fixture="$(mktemp -d)/repo"
mkdir -p "$fixture"
printf '# Guide\n\n## A\n\n## B\n\n## C\n\n## D\n\n## E\n\n## F\n' > "$fixture/guide.md"
printf '# Guide\n\n## A\n\n## B\n' > "$fixture/guide-ko.md"
# A second pair, untouched, so the directory has a convention to compare against.
printf '# Other\n\n## A\n' > "$fixture/other.md"
printf '# Other\n\n## A\n' > "$fixture/other-ko.md"

$DM "$fixture" --json > "$fixture/out.json"
python3 - "$fixture/out.json" <<'PY'
import json, sys
r = json.load(open(sys.argv[1]))
drift = [f for f in r["findings"] if f["code"] == "structural-drift"]
failures = []
if len(drift) != 1:
    failures.append(f"expected 1 drift finding, got {[f['file'] for f in drift]}")
elif drift[0]["file"] != "guide-ko.md":
    failures.append(f"wrong file: {drift[0]['file']}")
elif "headings" not in drift[0]["message"]:
    failures.append(f"the diverging metric is not named: {drift[0]['message']}")
for line in failures: print(f"FAIL: {line}")
sys.exit(1 if failures else 0)
PY
echo "PASS: heading drift reported, the pair that matches stays quiet"
)

step 'doc-mirror tells a broken link from a home path'
(
set -euo pipefail
fixture="$(mktemp -d)/repo"
mkdir -p "$fixture"
printf '# A\n\n[gone](docs/missing.md)\n[home](~/.claude/agents/x.md)\n[web](https://example.com/x.md)\n[anchor](#section)\n' \
  > "$fixture/a.md"
printf '# A\n' > "$fixture/a-ko.md"
printf '# B\n' > "$fixture/b.md"
printf '# B\n' > "$fixture/b-ko.md"

$DM "$fixture" --json > "$fixture/out.json"
python3 - "$fixture/out.json" <<'PY'
import json, sys
r = json.load(open(sys.argv[1]))
codes = [f["code"] for f in r["findings"]]
failures = []
if codes.count("broken-link") != 1:
    failures.append(f"expected exactly 1 broken-link, got {codes}")
if codes.count("home-path-link") != 1:
    failures.append(f"a ~ link should be its own quieter finding, got {codes}")
home = next((f for f in r["findings"] if f["code"] == "home-path-link"), None)
if home and home["severity"] != "suggestion":
    failures.append(f"a home path is not critical: {home['severity']}")
for line in failures: print(f"FAIL: {line}")
sys.exit(1 if failures else 0)
PY
echo "PASS: an unresolvable relative link is critical, a ~ path is a suggestion"
)

step 'doc-mirror says nothing about a repository that keeps one language'
(
set -euo pipefail
fixture="$(mktemp -d)/repo"
mkdir -p "$fixture/docs"
for name in README CONTRIBUTING docs/setup docs/deploy; do
  mkdir -p "$(dirname "$fixture/$name")"
  printf '# %s\n\n## One\n\ntext\n' "$name" > "$fixture/$name.md"
done

out="$($DM "$fixture")"
echo "$out"
grep -q 'No translated documentation pairs found' <<<"$out" \
  || { echo "FAIL: a single-language repo did not get the short answer"; exit 1; }
$DM "$fixture" --json > "$fixture/out.json"
python3 - "$fixture/out.json" <<'PY'
import json, sys
r = json.load(open(sys.argv[1]))
if r["findings"]:
    print("FAIL: invented findings for a single-language repo:", r["findings"])
    sys.exit(1)
PY
echo "PASS: no pairs, no findings, no lecture"
)

step 'doc-mirror never expects a mirror of a generated document'
(
set -euo pipefail
fixture="$(mktemp -d)/repo"
mkdir -p "$fixture"
printf '# R\n' > "$fixture/README.md"
printf '# R\n' > "$fixture/README-ko.md"
printf '# G\n' > "$fixture/GUIDE.md"
printf '# G\n' > "$fixture/GUIDE-ko.md"
printf '# I\n' > "$fixture/INSTALL.md"
printf '# I\n' > "$fixture/INSTALL-ko.md"
# A machine writes these, and a model reads the last one. None get mirrors.
for name in RELEASE CHANGELOG CONTRIBUTORS LICENSE CLAUDE; do
  printf '# %s\n' "$name" > "$fixture/$name.md"
done

$DM "$fixture" --json > "$fixture/out.json"
python3 - "$fixture/out.json" <<'PY'
import json, sys
r = json.load(open(sys.argv[1]))
flagged = sorted(f["file"] for f in r["findings"] if f["code"] == "missing-mirror")
if flagged:
    print(f"FAIL: demanded a mirror for a generated or agent document: {flagged}")
    sys.exit(1)
PY
echo "PASS: RELEASE, CHANGELOG, CONTRIBUTORS, LICENSE and CLAUDE are left alone"
)

step 'doc-mirror checks its own README pair'
(
set -euo pipefail
# The plugin ships an EN/KO pair of its own, so the marketplace repo is a real
# fixture. If this plugin cannot keep its own documentation in sync, it has no
# business reporting on anyone else's.
$DM . --strict > /tmp/dm-self.txt || {
  echo "FAIL: this repository has a critical doc-mirror finding"
  cat /tmp/dm-self.txt
  exit 1
}
cat /tmp/dm-self.txt
grep -q 'Every pair is structurally in sync' /tmp/dm-self.txt \
  || { echo "FAIL: the marketplace repo's own pairs have drifted"; exit 1; }
echo "PASS: the plugin's own pair, and every other pair here, is in sync"
)

step 'the lockstep hook nudges only when a counterpart is really stale'
(
set -euo pipefail
fixture="$(mktemp -d)"
mkdir -p "$fixture/docs"
for name in a b c; do
  printf '# %s\n' "$name" > "$fixture/docs/$name.md"
  printf '# %s\n' "$name" > "$fixture/docs/$name-ko.md"
done
printf '# lonely\n' > "$fixture/docs/lonely.md"

edited() {
  printf '{"type":"assistant","message":{"content":[{"type":"tool_use","name":"Edit","input":{"file_path":"%s"}}]}}\n' "$1"
}
fire() {
  printf '{"tool_input":{"file_path":"%s"},"transcript_path":"%s"}' "$1" "$2" | bash "$HOOK"
}

edited "$fixture/docs/a.md" > "$fixture/t.jsonl"

# 1. source edited, mirror untouched this session -> nudge
out="$(fire "$fixture/docs/a.md" "$fixture/t.jsonl")"
grep -q 'a-ko.md' <<<"$out" || { echo "FAIL: no nudge for the stale mirror: $out"; exit 1; }

# 2. mirror edited too -> silent
edited "$fixture/docs/a-ko.md" >> "$fixture/t.jsonl"
out="$(fire "$fixture/docs/a.md" "$fixture/t.jsonl")"
[ -z "$out" ] || { echo "FAIL: nudged although the pair was edited together: $out"; exit 1; }

# 3. editing the mirror looks for its source, not the other way round only
out="$(fire "$fixture/docs/b-ko.md" "$fixture/t.jsonl")"
grep -q 'b.md' <<<"$out" || { echo "FAIL: the check is not bidirectional: $out"; exit 1; }

# 4. a document with no mirror, where most have one -> nudge
out="$(fire "$fixture/docs/lonely.md" "$fixture/t.jsonl")"
grep -q 'no ko mirror' <<<"$out" || { echo "FAIL: no nudge for the unmirrored doc: $out"; exit 1; }

# 5. a directory that keeps no mirrors -> silent
plain="$(mktemp -d)"
printf '# x\n' > "$plain/README.md"
printf '# y\n' > "$plain/GUIDE.md"
out="$(fire "$plain/README.md" "$fixture/t.jsonl")"
[ -z "$out" ] || { echo "FAIL: nudged in a single-language directory: $out"; exit 1; }

# 6. not Markdown -> silent, and fast
out="$(fire "$plain/main.go" "$fixture/t.jsonl")"
[ -z "$out" ] || { echo "FAIL: nudged on a non-Markdown edit: $out"; exit 1; }

# 7. every kind of broken input fails OPEN — a nudge is never a boundary
for bad in 'not json at all .md' '{"tool_input":{}}' '{".md":1}' \
           '{"tool_input":{"file_path":"/nope/gone.md"},"transcript_path":"/nope"}'; do
  if ! out="$(printf '%s' "$bad" | bash "$HOOK" 2>&1)"; then
    echo "FAIL: hook exited non-zero on input: $bad"
    exit 1
  fi
  [ -z "$out" ] || { echo "FAIL: hook spoke up on garbage input $bad: $out"; exit 1; }
done
echo "PASS: nudges when stale, silent when in sync, silent when unsure"
)
