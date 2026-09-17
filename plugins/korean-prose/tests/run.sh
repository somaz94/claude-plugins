#!/usr/bin/env bash
# korean-prose bundled-script tests
#
# Run directly (`bash plugins/korean-prose/tests/run.sh`) or through the whole
# suite (`bash tests/run.sh`). CI calls this same file.
set -euo pipefail
. "$(dirname "${BASH_SOURCE[0]:-$0}")/../../../tests/lib.sh"

SCAN="python3 -B plugins/korean-prose/scripts/scan.py"

step 'korean-prose scanner unit tests'
(
set -euo pipefail
python3 -B -m unittest discover -s plugins/korean-prose/tests
echo "PASS: extraction, both passes, lexicon validation and the CLI"
)

step 'korean-prose shipped lexicon is valid'
(
set -euo pipefail
$SCAN --check-lexicon
[ ! -e plugins/korean-prose/lexicon/patterns.local.tsv ] && [ ! -e plugins/korean-prose/lexicon/terms.local.tsv ] \
  || { echo "FAIL: a personal *.local.tsv overlay is committed"; exit 1; }
echo "PASS: every example matches its row, no kept label is flagged, no local overlay shipped"
)

step 'korean-prose reports one referent spelled three ways'
(
set -euo pipefail
fixture="$(mktemp -d)"
# The regression that started the lexicon: a Hangul transliteration and a
# lower-case spelling next to the English one, which a reviewer missed.
printf '# 배포\n\n카나리 배포로 전환했다.\n\nArgo Rollouts Canary 전략을 썼다.\n\ncanary 단계에서 검증했다.\n' > "$fixture/doc.md"
$SCAN --json "$fixture/doc.md" > "$fixture/out.json"
python3 - "$fixture/out.json" <<'PY'
import json, sys
f = json.load(open(sys.argv[1]))["files"][0]
failures = []
canary = [t for t in f["terms"] if t["canonical"] == "Canary"]
if not canary:
    failures.append(f"Canary not reported; terms were {[t['canonical'] for t in f['terms']]}")
else:
    forms = {x["form"]: x["lines"] for x in canary[0]["forms"]}
    if forms != {"카나리": [3], "Canary": [5], "canary": [7]}:
        failures.append(f"wrong spellings or lines: {forms}")
for line in failures: print(f"FAIL: {line}")
sys.exit(1 if failures else 0)
PY
echo "PASS: all three spellings are counted with their lines"
)
