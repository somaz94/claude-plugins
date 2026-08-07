#!/usr/bin/env bash
# atlas bundled-script tests
#
# The bodies below are the same text .github/workflows/ci.yml runs; the
# workflow calls this file rather than carrying them inline, so the answer
# to 'will CI pass?' is available before pushing.
set -euo pipefail
. "$(dirname "${BASH_SOURCE[0]:-$0}")/../../../tests/lib.sh"

step 'atlas maps all four layers'
(
set -euo pipefail
fixture="$(mktemp -d)"
mkdir -p "$fixture/home/.claude/agents" \
         "$fixture/home/.claude/commands/git" \
         "$fixture/home/.claude/skills/tidy" \
         "$fixture/home/.claude/plugins" \
         "$fixture/repo/.claude/commands" \
         "$fixture/plug/commands" \
         "$fixture/plug/skills/demo" \
         "$fixture/plug/.claude-plugin"
printf -- '---\nname: reviewer\ndescription: A user agent\n---\nbody\n' \
  > "$fixture/home/.claude/agents/reviewer.md"
printf -- '---\ndescription: Ships it\n---\nbody\n' \
  > "$fixture/home/.claude/commands/git/ship.md"
printf -- '---\nname: tidy\ndescription: Tidies up\n---\nbody\n' \
  > "$fixture/home/.claude/skills/tidy/SKILL.md"
printf '# Global memory\n' > "$fixture/home/.claude/CLAUDE.md"
printf -- '---\n---\nno description\n' > "$fixture/repo/.claude/commands/nodesc.md"
printf -- '---\ndescription: A plugin command\n---\nbody\n' \
  > "$fixture/plug/commands/ship.md"
printf -- '---\nname: demo\ndescription: A plugin skill\n---\nbody\n' \
  > "$fixture/plug/skills/demo/SKILL.md"
printf '{"name":"demoplug","version":"1.2.3","description":"a demo plugin"}' \
  > "$fixture/plug/.claude-plugin/plugin.json"
cat > "$fixture/home/.claude/plugins/installed_plugins.json" <<EOF
{"version":2,"plugins":{"demoplug@acme":[
  {"scope":"user","installPath":"$fixture/plug","version":"1.2.3"}]}}
EOF

python3 plugins/atlas/scripts/atlas.py --project "$fixture/repo" \
  --user-root "$fixture/home/.claude" scan > "$fixture/graph.json"
python3 - "$fixture/graph.json" <<'PY'
import json, sys

graph = json.load(open(sys.argv[1]))
items = graph["items"]
failures = []

layers = {i["layer"] for i in items}
for layer in ("user", "project", "plugin"):
    if layer not in layers:
        failures.append(f"nothing collected from the {layer} layer")

def one(kind, name):
    hits = [i for i in items if i["kind"] == kind and i["name"] == name]
    if len(hits) != 1:
        failures.append(f"expected exactly one {kind} named {name!r}, got {len(hits)}")
        return None
    return hits[0]

# A subdirectory namespaces a command, and the map must report the
# name actually typed rather than the file stem.
nested = one("command", "git:ship")
if nested and nested["invocation"] != "/git:ship":
    failures.append(f"nested command invocation is {nested['invocation']!r}")

# A plugin's command carries its plugin namespace, which is exactly
# why it can never collide with the user's own.
plugged = one("command", "ship")
if plugged and plugged["invocation"] != "/demoplug:ship":
    failures.append(f"plugin command invocation is {plugged['invocation']!r}")

skill = one("skill", "demo")
if skill and skill["invocation"] != "/demoplug:demo":
    failures.append(f"plugin skill invocation is {skill['invocation']!r}")

if not any(i["kind"] == "memory" for i in items):
    failures.append("CLAUDE.md was not collected")
if graph["stats"]["missingDescriptions"] != 1:
    failures.append(
        f"expected 1 item with no description, got "
        f"{graph['stats']['missingDescriptions']}"
    )

# The per-scope split is what makes the always-on figure actionable.
# If it stops summing to the whole, one scope is being charged to
# another and the page points at the wrong directory.
split = graph["stats"]["residentByLayer"]
if sum(split.values()) != graph["stats"]["residentChars"]:
    failures.append(
        f"per-scope resident split {split} does not sum to "
        f"{graph['stats']['residentChars']}"
    )
if set(split) - layers:
    failures.append(f"resident charged to a layer with no items: {set(split) - layers}")

for line in failures:
    print(f"FAIL: {line}")
sys.exit(1 if failures else 0)
PY
echo "PASS: user, project and plugin layers all collected with the right invocations"
)

step 'atlas names the definition that wins'
(
set -euo pipefail
fixture="$(mktemp -d)"
mkdir -p "$fixture/home/.claude/agents" "$fixture/repo/.claude/agents"
printf -- '---\nname: reviewer\ndescription: The user copy\n---\nbody\n' \
  > "$fixture/home/.claude/agents/reviewer.md"
printf -- '---\nname: reviewer\ndescription: The project copy\n---\nother body\n' \
  > "$fixture/repo/.claude/agents/reviewer.md"
printf -- '---\nname: solo\ndescription: Only here\n---\nbody\n' \
  > "$fixture/repo/.claude/agents/solo.md"

python3 plugins/atlas/scripts/atlas.py --project "$fixture/repo" \
  --user-root "$fixture/home/.claude" scan > "$fixture/graph.json"
python3 - "$fixture/graph.json" <<'PY'
import json, sys

graph = json.load(open(sys.argv[1]))
failures = []

conflicts = graph["conflicts"]
if len(conflicts) != 1:
    failures.append(f"expected 1 conflict, got {len(conflicts)}")
else:
    conflict = conflicts[0]
    if conflict["name"] != "reviewer":
        failures.append(f"wrong name flagged: {conflict['name']!r}")
    if "project" not in conflict["verdict"]:
        failures.append(f"winner not named: {conflict['verdict']!r}")
    if len(conflict["where"]) != 2:
        failures.append(f"expected 2 origins, got {len(conflict['where'])}")

solo = next(i for i in graph["items"] if i["name"] == "solo")
if solo.get("conflicted"):
    failures.append("an agent defined once was reported as shadowed")

for line in failures:
    print(f"FAIL: {line}")
sys.exit(1 if failures else 0)
PY
echo "PASS: the shadowed name is reported and the winner is named"
)

step 'atlas tells an inline hook from a missing script'
(
set -euo pipefail
fixture="$(mktemp -d)"
mkdir -p "$fixture/home/.claude/hooks" "$fixture/repo"
printf '#!/usr/bin/env bash\ntrue\n' > "$fixture/home/.claude/hooks/present.sh"
chmod +x "$fixture/home/.claude/hooks/present.sh"
cat > "$fixture/home/.claude/settings.json" <<EOF
{"hooks":{"PreToolUse":[{"matcher":"Bash","hooks":[
  {"type":"command","command":"$fixture/home/.claude/hooks/present.sh"},
  {"type":"command","command":"$fixture/home/.claude/hooks/gone.sh"},
  {"type":"command","command":"jq -r .tool_name"}]}]}}
EOF

python3 plugins/atlas/scripts/atlas.py --project "$fixture/repo" \
  --user-root "$fixture/home/.claude" scan > "$fixture/graph.json"
python3 - "$fixture/graph.json" <<'PY'
import json, sys

graph = json.load(open(sys.argv[1]))
hooks = {i["name"]: i for i in graph["items"] if i["kind"] == "hook"}
failures = []

if len(hooks) != 3:
    failures.append(f"expected 3 hooks, got {sorted(hooks)}")
if hooks.get("present.sh", {}).get("resolves") is not True:
    failures.append("a hook whose script exists was reported as missing")
if hooks.get("gone.sh", {}).get("resolves") is not False:
    failures.append("a hook pointing at a deleted script was not flagged")
if hooks.get("jq", {}).get("resolves") is not True:
    failures.append("an inline hook was reported as a missing script")
if graph["stats"]["unresolvedHooks"] != 1:
    failures.append(f"unresolvedHooks is {graph['stats']['unresolvedHooks']}")

for line in failures:
    print(f"FAIL: {line}")
sys.exit(1 if failures else 0)
PY
echo "PASS: missing script flagged, inline hook left alone"
)

step 'atlas ships a self-contained viewer that leaks no MCP values'
(
set -euo pipefail
fixture="$(mktemp -d)"
mkdir -p "$fixture/home/.claude" "$fixture/repo"
cat > "$fixture/repo/.mcp.json" <<'EOF'
{"mcpServers":{"gh":{"command":"npx","args":["-y","gh-mcp"],
  "env":{"GH_TOKEN":"do-not-ship-this"}}}}
EOF

python3 plugins/atlas/scripts/atlas.py --project "$fixture/repo" \
  --user-root "$fixture/home/.claude" view --out "$fixture/out.html"
test -s "$fixture/out.html"

if grep -qF 'do-not-ship-this' "$fixture/out.html"; then
  echo "FAIL: an MCP env VALUE reached the viewer"
  exit 1
fi
if ! grep -qF 'GH_TOKEN' "$fixture/out.html"; then
  echo "FAIL: the MCP env variable name was not reported at all"
  exit 1
fi
# A strict CSP is not available for a file:// page, so the guarantee
# has to be that no external reference is emitted in the first place.
if grep -qE '<script[^>]+src=|<link |@import|url\(' "$fixture/out.html"; then
  echo "FAIL: the viewer references something outside itself"
  grep -nE '<script[^>]+src=|<link |@import|url\(' "$fixture/out.html" | head
  exit 1
fi
echo "PASS: no external reference, env reported by name only"
)

step 'atlas pairs a translation mirror without counting it twice'
(
set -euo pipefail
fixture="$(mktemp -d)"
mkdir -p "$fixture/home/.claude/agents" "$fixture/home/.claude/agents-ko" \
         "$fixture/home/.claude/skills/tidy" "$fixture/home/.claude/skills-ko/tidy" \
         "$fixture/repo"
printf -- '---\nname: reviewer\ndescription: Reviews things.\n---\nsource body\n' \
  > "$fixture/home/.claude/agents/reviewer.md"
# The mirror translates the name too — pairing must still work, which
# is why it is done by path and never by the name field.
printf -- '---\nname: 리뷰어\ndescription: 이것저것 검토합니다.\n---\n번역된 본문\n' \
  > "$fixture/home/.claude/agents-ko/reviewer.md"
printf -- '---\nname: lonely\ndescription: Has no mirror.\n---\nbody\n' \
  > "$fixture/home/.claude/agents/lonely.md"
printf -- '---\nname: tidy\ndescription: Tidies up.\n---\nbody\n' \
  > "$fixture/home/.claude/skills/tidy/SKILL.md"
printf -- '---\nname: tidy\ndescription: 정리합니다.\n---\n본문\n' \
  > "$fixture/home/.claude/skills-ko/tidy/SKILL.md"

python3 plugins/atlas/scripts/atlas.py --project "$fixture/repo" \
  --user-root "$fixture/home/.claude" scan > "$fixture/graph.json"
python3 - "$fixture/graph.json" <<'PY'
import json, sys

graph = json.load(open(sys.argv[1]))
items = {(i["kind"], i["name"]): i for i in graph["items"]}
failures = []

if graph["languages"] != ["ko"]:
    failures.append(f"languages is {graph['languages']!r}")

agents = [i for i in graph["items"] if i["kind"] == "agent"]
if len(agents) != 2:
    failures.append(f"expected 2 agents, got {[a['name'] for a in agents]}")
if any(a["name"] == "리뷰어" for a in agents):
    failures.append("the mirror was collected as its own agent")

reviewer = items.get(("agent", "reviewer"))
if reviewer is None:
    failures.append("reviewer missing")
elif reviewer["translations"].get("ko", {}).get("description") != "이것저것 검토합니다.":
    failures.append(f"reviewer ko translation not paired: {reviewer['translations']!r}")

skill = items.get(("skill", "tidy"))
if skill is None or "ko" not in skill["translations"]:
    failures.append("the skill mirror was not paired")

lonely = items.get(("agent", "lonely"))
if lonely is None:
    failures.append("lonely missing")
else:
    if lonely["translations"]:
        failures.append("an item with no mirror was given one")
    # A gap is only a gap where mirrors are kept at all.
    if "ko" not in lonely.get("mirrorLangs", []):
        failures.append("a missing mirror in a mirrored directory was not flaggable")

# The mirror must not be charged for: only the source description is
# resident, so the total is the two source descriptions plus the skill.
expected = sum(
    len(i["name"]) + len(i["description"])
    for i in graph["items"] if i["kind"] in ("agent", "skill")
)
if graph["stats"]["residentChars"] != expected:
    failures.append(
        f"residentChars {graph['stats']['residentChars']} != {expected} "
        "— a translation was counted as always-on context"
    )

for line in failures:
    print(f"FAIL: {line}")
sys.exit(1 if failures else 0)
PY
echo "PASS: mirror paired by path, not counted, not charged for"
)

step 'atlas renders Markdown without letting a scanned file inject markup'
(
set -euo pipefail
fixture="$(mktemp -d)"
mkdir -p "$fixture/home/.claude/agents" "$fixture/repo"
printf -- '---\nname: alpha\ndescription: an agent\n---\nbody\n' \
  > "$fixture/home/.claude/agents/alpha.md"
python3 plugins/atlas/scripts/atlas.py --project "$fixture/repo" \
  --user-root "$fixture/home/.claude" view --out "$fixture/out.html"

# Lift the page's own renderer out and run it on hostile input.
python3 - "$fixture/out.html" > "$fixture/render.js" <<'PY'
import pathlib, re, sys
js = re.findall(r'<script>(.*?)</script>', pathlib.Path(sys.argv[1]).read_text(), re.S)[-1]
js = js[: js.index("function renderSummary")]
js = js.replace(
    "const DATA = JSON.parse(document.getElementById('atlas-data').textContent);",
    "const DATA={};")
print(js)
print("module.exports = { esc, inline, mdToHtml };")
PY

node -e '
const { inline, mdToHtml } = require(process.argv[1]);
const failures = [];

const hostile = [
  "# Heading",
  "<script>alert(1)</script>",
  "<img src=x onerror=alert(1)>",
  "- item with `code` and **bold**",
  "> quoted line",
  "| a | b |",
  "|---|---|",
  "| 1 | 2 |",
  "```bash",
  "echo \"a<b\" && exit 1",
  "```",
  "trailing paragraph",
].join("\n");
const html = mdToHtml(hostile);

// The precise invariant: every tag in the output was emitted by the
// renderer. Checking for substrings like "onerror=" instead would fire
// on the escaped text `&lt;img src=x onerror=...&gt;`, which is inert.
const allowed = new Set(["p","br","code","strong","em","h3","h4","h5","h6",
  "ul","ol","li","blockquote","table","thead","tbody","tr","th","td","pre","hr","span"]);
const emitted = [...html.matchAll(/<\/?([a-zA-Z][a-zA-Z0-9]*)/g)].map(m => m[1].toLowerCase());
const unexpected = [...new Set(emitted)].filter(t => !allowed.has(t));
if (unexpected.length) failures.push("renderer emitted unexpected tags: " + unexpected.join(", "));
if (!html.includes("&lt;script&gt;")) failures.push("the script tag was not escaped as text");
if (!html.includes("&lt;img src=x onerror=")) failures.push("the img tag was not escaped as text");

for (const tag of ["p","ul","li","blockquote","table","thead","tbody","tr","th","td","pre","code","strong"]) {
  const open = (html.match(new RegExp("<" + tag + "[ >]", "g")) || []).length;
  const close = (html.match(new RegExp("</" + tag + ">", "g")) || []).length;
  if (open !== close) failures.push(`unbalanced <${tag}>: ${open} open, ${close} close`);
}
for (const want of ["<h3>", "<table>", "<pre><code>", "<blockquote>", "<li>"]) {
  if (!html.includes(want)) failures.push(`expected ${want} in the rendered output`);
}
if (html.includes("&amp;lt;")) failures.push("double-escaped output");

// A description is rendered with inline Markdown only.
const desc = inline("uses `~/code/repo/` and **bold** <script>x</script>");
if (!desc.includes("<code>~/code/repo/</code>")) failures.push("backticks not rendered in a description");
if (!desc.includes("<strong>bold</strong>")) failures.push("bold not rendered in a description");
if (desc.includes("<script")) failures.push("a description let markup through");

for (const line of failures) console.log("FAIL: " + line);
process.exit(failures.length ? 1 : 0);
' "$fixture/render.js"
echo "PASS: Markdown rendered, document markup kept inert"
)

step 'atlas refuses to write into a scanned tree'
(
set -euo pipefail
fixture="$(mktemp -d)"
mkdir -p "$fixture/home/.claude/agents" "$fixture/repo/.claude"
printf -- '---\nname: alpha\ndescription: an agent\n---\nbody\n' \
  > "$fixture/home/.claude/agents/alpha.md"

if python3 plugins/atlas/scripts/atlas.py --project "$fixture/repo" \
     --user-root "$fixture/home/.claude" \
     view --out "$fixture/home/.claude/atlas.html"; then
  echo "FAIL: wrote the viewer inside the scanned config root"
  exit 1
fi
if [ -e "$fixture/home/.claude/atlas.html" ]; then
  echo "FAIL: refused but the file exists anyway"
  exit 1
fi
python3 plugins/atlas/scripts/atlas.py --project "$fixture/repo" \
  --user-root "$fixture/home/.claude" view --out "$fixture/outside.html"
test -s "$fixture/outside.html"
echo "PASS: refused inside the root, allowed outside it"
)

step 'atlas maps a named project beside this one and the global layer'
(
set -euo pipefail
fixture="$(mktemp -d)"
mkdir -p "$fixture/home/.claude/agents" \
         "$fixture/here/.claude/agents" \
         "$fixture/there/.claude/agents"
printf -- '---\nname: shared\ndescription: A user agent\n---\nbody\n' \
  > "$fixture/home/.claude/agents/shared.md"
printf -- '---\nname: here-only\ndescription: Only in here\n---\nbody\n' \
  > "$fixture/here/.claude/agents/here-only.md"
printf -- '---\nname: there-only\ndescription: Only in there\n---\nbody\n' \
  > "$fixture/there/.claude/agents/there-only.md"

python3 plugins/atlas/scripts/atlas.py --project "$fixture/here" \
  --user-root "$fixture/home/.claude" \
  view "$fixture/there" --out "$fixture/maps"

count="$(find "$fixture/maps" -name '*.html' | wc -l | tr -d ' ')"
if [ "$count" != "3" ]; then
  echo "FAIL: expected 3 maps, got $count"
  ls -1 "$fixture/maps"
  exit 1
fi
for name in here global there; do
  test -s "$fixture/maps/claude-atlas-$name.html" \
    || { echo "FAIL: no map named $name"; exit 1; }
done

global="$fixture/maps/claude-atlas-global.html"
grep -qF '"scope": "global"' "$global" \
  || { echo "FAIL: the global map does not declare itself as one"; exit 1; }
if grep -qF 'here-only' "$global"; then
  echo "FAIL: the global map scanned a project layer"
  exit 1
fi
grep -qF 'shared' "$global" \
  || { echo "FAIL: the global map dropped the user layer too"; exit 1; }

grep -qF 'here-only' "$fixture/maps/claude-atlas-here.html" \
  || { echo "FAIL: the session project map is missing its own agent"; exit 1; }
grep -qF 'there-only' "$fixture/maps/claude-atlas-there.html" \
  || { echo "FAIL: the named project map is missing its own agent"; exit 1; }
if grep -qF 'there-only' "$fixture/maps/claude-atlas-here.html"; then
  echo "FAIL: one project's items leaked into another's map"
  exit 1
fi
echo "PASS: three maps, and the global one really is global"
)

step 'atlas clears its own stale viewers and nothing else'
(
set -euo pipefail
fixture="$(mktemp -d)"
export TMPDIR="$fixture/tmp"
mkdir -p "$TMPDIR" "$fixture/home/.claude/agents" "$fixture/repo"
printf -- '---\nname: alpha\ndescription: an agent\n---\nbody\n' \
  > "$fixture/home/.claude/agents/alpha.md"

printf '<!doctype html>\n<title>Claude Code atlas — gone</title>\n' \
  > "$TMPDIR/claude-atlas-gone.html"
# Wears the name, carries no atlas title. Must survive.
printf '<!doctype html>\n<title>somebody else entirely</title>\n' \
  > "$TMPDIR/claude-atlas-imposter.html"
printf 'not html at all\n' > "$TMPDIR/notes.txt"

python3 plugins/atlas/scripts/atlas.py --project "$fixture/repo" \
  --user-root "$fixture/home/.claude" view

test ! -e "$TMPDIR/claude-atlas-gone.html" \
  || { echo "FAIL: a viewer from an earlier run survived"; exit 1; }
test -e "$TMPDIR/claude-atlas-imposter.html" \
  || { echo "FAIL: deleted a file that only shared the name"; exit 1; }
test -e "$TMPDIR/notes.txt" \
  || { echo "FAIL: deleted an unrelated file"; exit 1; }
test -s "$TMPDIR/claude-atlas-repo.html" \
  || { echo "FAIL: the viewer it just wrote is not there"; exit 1; }

printf '<!doctype html>\n<title>Claude Code atlas — kept</title>\n' \
  > "$TMPDIR/claude-atlas-kept.html"
python3 plugins/atlas/scripts/atlas.py --project "$fixture/repo" \
  --user-root "$fixture/home/.claude" view --keep-old
test -e "$TMPDIR/claude-atlas-kept.html" \
  || { echo "FAIL: --keep-old deleted an earlier viewer anyway"; exit 1; }
echo "PASS: own stale viewers cleared, lookalikes and --keep-old respected"
)
step 'atlas defers body rendering until a card is opened'
(
set -euo pipefail
fixture="$(mktemp -d)"
mkdir -p "$fixture/home/.claude/agents" "$fixture/repo"
printf -- '---\nname: alpha\ndescription: an agent\n---\n# Heading\n\nsome `code` and **bold**\n' \
  > "$fixture/home/.claude/agents/alpha.md"
python3 plugins/atlas/scripts/atlas.py --project "$fixture/repo" \
  --user-root "$fixture/home/.claude" view --out "$fixture/out.html"

# Run the page's own script against a stub DOM. Rendering every collapsed body
# up front cost ~17ms and 1.25MB of string per keystroke on a 95-item config;
# this asserts the list ships slots instead, and that the filler still works.
cat > "$fixture/probe.js" <<'PROBE'
const fs = require('fs');
const html = fs.readFileSync(process.argv[2], 'utf8');
const data = html.match(/<script id="atlas-data" type="application\/json">([\s\S]*?)<\/script>/);
const script = html.match(/<script>\n([\s\S]*?)\n<\/script>/);
const captured = {};
const make = id => ({
  set innerHTML(v) { captured[id] = v; }, get innerHTML() { return captured[id] || ''; },
  set textContent(v) { captured[id] = v; },
  get textContent() { return id === 'atlas-data' ? data[1] : (captured[id] || ''); },
  addEventListener() {}, querySelectorAll() { return []; },
});
const els = {};
global.document = { getElementById: id => els[id] || (els[id] = make(id)),
                    addEventListener() {}, activeElement: { id: '' } };
const checks = `
const failures = [];
const list = document.getElementById('list').innerHTML;
if (!list.includes('class="body-slot" data-i=')) failures.push('no body slot was emitted');
if (list.includes('class="md"')) failures.push('a body was rendered before its card was opened');
if (list.includes('class="body"')) failures.push('a raw body was rendered before its card was opened');
const rendered = bodyHtml(DATA.items.find(i => i.kind === 'agent'));
if (!rendered.includes('<h3>')) failures.push('bodyHtml did not render the heading: ' + rendered.slice(0, 80));
if (!rendered.includes('<code>code</code>')) failures.push('bodyHtml did not render inline code');
state.raw = true;
if (!bodyHtml(DATA.items.find(i => i.kind === 'agent')).includes('<pre class="body">'))
  failures.push('raw mode did not produce raw source');
for (const f of failures) console.log('FAIL: ' + f);
process.exit(failures.length ? 1 : 0);
`;
eval(script[1] + checks);
PROBE
node "$fixture/probe.js" "$fixture/out.html"
echo "PASS: bodies ship as slots and render correctly on demand"
)
step 'atlas budget ranks what the always-on context is spent on'
(
set -euo pipefail
fixture="$(mktemp -d)"
mkdir -p "$fixture/home/.claude/agents" "$fixture/repo/.claude/commands"
# Three descriptions of deliberately different length, so the ranking has a
# right answer rather than a plausible one.
printf -- '---\nname: heavy\ndescription: %s\n---\nbody\n' "$(printf 'x%.0s' $(seq 1 800))" \
  > "$fixture/home/.claude/agents/heavy.md"
printf -- '---\nname: light\ndescription: short one\n---\nbody\n' \
  > "$fixture/home/.claude/agents/light.md"
printf -- '---\ndescription: %s\n---\nbody\n' "$(printf 'y%.0s' $(seq 1 400))" \
  > "$fixture/repo/.claude/commands/mid.md"

python3 plugins/atlas/scripts/atlas.py --project "$fixture/repo" \
  --user-root "$fixture/home/.claude" budget --by scope --json > "$fixture/b.json"
python3 - "$fixture/b.json" <<'PY'
import json, sys

r = json.load(open(sys.argv[1]))
failures = []

names = [i["name"] for i in r["items"]]
if names[:3] != ["heavy", "mid", "light"]:
    failures.append(f"ranking is {names[:3]}, expected heavy, mid, light")

# The totals must be the same number the graph reports, not a second estimate
# computed a slightly different way.
# Characters are the exact quantity; tokens are a division and only ever an
# estimate, so the invariant is asserted on the thing that adds up.
if sum(g["chars"] for g in r["groups"]) != r["totalChars"]:
    failures.append(f"group chars {sum(g['chars'] for g in r['groups'])} != {r['totalChars']}")
if sum(g["count"] for g in r["groups"]) != r["residentItems"]:
    failures.append("group counts do not sum to the resident item count")

by_scope = {g["name"]: g for g in r["groups"]}
if set(by_scope) != {"Global", "Project"}:
    failures.append(f"scope axis produced {sorted(by_scope)}")
elif by_scope["Global"]["chars"] <= by_scope["Project"]["chars"]:
    failures.append("the heavier scope did not rank first")

for line in failures:
    print(f"FAIL: {line}")
sys.exit(1 if failures else 0)
PY

# A threshold lists everything at or over it, rather than a fixed-size top N.
over="$(python3 plugins/atlas/scripts/atlas.py --project "$fixture/repo" \
  --user-root "$fixture/home/.claude" budget --over 50 --json)"
python3 - <<PY
import json, sys
r = json.loads('''$over''')
names = sorted(i["name"] for i in r["items"])
if names != ["heavy", "mid"]:
    print(f"FAIL: --over 50 listed {names}, expected heavy and mid")
    sys.exit(1)
PY
echo "PASS: budget ranks by cost, groups consistently, and honours a threshold"
)

step 'atlas viewer can sort a section by what it costs'
(
set -euo pipefail
fixture="$(mktemp -d)"
mkdir -p "$fixture/home/.claude/agents" "$fixture/repo"
printf -- '---\nname: aaa-heavy\ndescription: %s\n---\nbody\n' "$(printf 'x%.0s' $(seq 1 600))" \
  > "$fixture/home/.claude/agents/aaa-heavy.md"
printf -- '---\nname: zzz-light\ndescription: tiny\n---\nbody\n' \
  > "$fixture/home/.claude/agents/zzz-light.md"
printf -- '---\nname: mmm-mid\ndescription: %s\n---\nbody\n' "$(printf 'y%.0s' $(seq 1 300))" \
  > "$fixture/home/.claude/agents/mmm-mid.md"
python3 plugins/atlas/scripts/atlas.py --project "$fixture/repo" \
  --user-root "$fixture/home/.claude" view --out "$fixture/out.html"

cat > "$fixture/sort.js" <<'PROBE'
const fs = require('fs');
const html = fs.readFileSync(process.argv[2], 'utf8');
const data = html.match(/<script id="atlas-data" type="application\/json">([\s\S]*?)<\/script>/);
const script = html.match(/<script>\n([\s\S]*?)\n<\/script>/);
const captured = {};
const make = id => ({
  set innerHTML(v) { captured[id] = v; }, get innerHTML() { return captured[id] || ''; },
  set textContent(v) { captured[id] = v; },
  get textContent() { return id === 'atlas-data' ? data[1] : (captured[id] || ''); },
  addEventListener() {}, querySelectorAll() { return []; },
});
const els = {};
global.document = { getElementById: id => els[id] || (els[id] = make(id)),
                    addEventListener() {}, activeElement: { id: '' } };
const checks = `
const failures = [];
const order = () => [...document.getElementById('list').innerHTML
  .matchAll(/<span class="name">([^<]*)<\\/span>/g)].map(m => m[1]);
state.sort = 'name'; render();
const byName = order();
state.sort = 'size'; render();
const bySize = order();
if (byName.join() !== 'aaa-heavy,mmm-mid,zzz-light')
  failures.push('sort:name did not keep the on-disk order: ' + byName.join());
if (bySize.join() !== 'aaa-heavy,mmm-mid,zzz-light')
  failures.push('sort:size did not rank heaviest first: ' + bySize.join());
if (byName.length !== bySize.length)
  failures.push('sorting changed how many items are shown');
for (const f of failures) console.log('FAIL: ' + f);
process.exit(failures.length ? 1 : 0);
`;
eval(script[1] + checks);
PROBE
node "$fixture/sort.js" "$fixture/out.html"
echo "PASS: the size sort ranks by resident cost without dropping items"
)
step 'atlas viewer remembers its filters but never a stale search'
(
set -euo pipefail
fixture="$(mktemp -d)"
mkdir -p "$fixture/home/.claude/agents" "$fixture/home/.claude/commands" "$fixture/repo"
printf -- '---\nname: alpha\ndescription: an agent\n---\nbody\n' \
  > "$fixture/home/.claude/agents/alpha.md"
printf -- '---\ndescription: a command\n---\nbody\n' \
  > "$fixture/home/.claude/commands/beta.md"
python3 plugins/atlas/scripts/atlas.py --project "$fixture/repo" \
  --user-root "$fixture/home/.claude" view --out "$fixture/out.html"

cat > "$fixture/store.js" <<'PROBE'
const fs = require('fs');
const html = fs.readFileSync(process.argv[2], 'utf8');
const data = html.match(/<script id="atlas-data" type="application\/json">([\s\S]*?)<\/script>/);
const script = html.match(/<script>\n([\s\S]*?)\n<\/script>/);

// A localStorage stand-in, plus one that throws the way a locked-down file://
// page does — the map must still render in that case.
function run(storage, extra) {
  const captured = {};
  const make = id => ({
    set innerHTML(v) { captured[id] = v; }, get innerHTML() { return captured[id] || ''; },
    set textContent(v) { captured[id] = v; },
    get textContent() { return id === 'atlas-data' ? data[1] : (captured[id] || ''); },
    addEventListener() {}, querySelectorAll() { return []; },
  });
  const els = {};
  global.document = { getElementById: id => els[id] || (els[id] = make(id)),
                      addEventListener() {}, activeElement: { id: '' } };
  global.localStorage = storage;
  let out = null;
  global.__capture = v => { out = v; };
  eval(script[1] + extra);
  return { out, captured };
}

const box = {};
const ok = {
  getItem: k => (k in box ? box[k] : null),
  setItem: (k, v) => { box[k] = v; },
};
const hostile = { getItem() { throw new Error('denied'); }, setItem() { throw new Error('denied'); } };

const failures = [];

// First run: set filters, which must be written through.
run(ok, `
  state.kinds.add('agent'); state.group = 'kind'; state.sort = 'size';
  state.q = 'should-not-persist';
  render();
`);
const keys = Object.keys(box);
if (keys.length !== 1) failures.push('expected exactly one stored key, got ' + keys);
const saved = JSON.parse(box[keys[0]] || '{}');
if (!keys[0].startsWith('atlas:')) failures.push('key is not namespaced: ' + keys[0]);
if (JSON.stringify(saved.kinds) !== '["agent"]') failures.push('kinds not saved: ' + JSON.stringify(saved.kinds));
if (saved.group !== 'kind' || saved.sort !== 'size') failures.push('group/sort not saved');
if ('q' in saved) failures.push('the search query was persisted');

// Second run: a fresh page reads them back, and the search box starts empty.
const second = run(ok, `__capture({kinds: [...state.kinds], group: state.group, sort: state.sort, q: state.q});`);
if (JSON.stringify(second.out.kinds) !== '["agent"]') failures.push('kinds not restored: ' + JSON.stringify(second.out));
if (second.out.group !== 'kind' || second.out.sort !== 'size') failures.push('group/sort not restored');
if (second.out.q !== '') failures.push('a stale query was restored: ' + second.out.q);

// A filter for something this map no longer contains must not be restored, or
// the page comes up empty with no visible cause.
box[keys[0]] = JSON.stringify({ kinds: ['nosuchkind'], layers: ['nosuchlayer'] });
const third = run(ok, `__capture({kinds: [...state.kinds], layers: [...state.layers]});`);
if (third.out.kinds.length || third.out.layers.length)
  failures.push('restored a filter this map cannot satisfy: ' + JSON.stringify(third.out));

// Storage that throws must not take the page down with it.
try {
  const denied = run(hostile, `__capture(document.getElementById('list').innerHTML.length);`);
  if (!denied.out) failures.push('nothing rendered when storage was unavailable');
} catch (e) {
  failures.push('an unavailable localStorage threw out of the page: ' + e.message);
}

for (const f of failures) console.log('FAIL: ' + f);
process.exit(failures.length ? 1 : 0);
PROBE
node "$fixture/store.js" "$fixture/out.html"
echo "PASS: filters persist per project, stale or impossible state is dropped"
)
step 'atlas diff reports what a rewrite campaign actually changed'
(
set -euo pipefail
fixture="$(mktemp -d)"
mkdir -p "$fixture/home/.claude/agents" "$fixture/repo"
long="$(printf 'x%.0s' $(seq 1 400))"
printf -- '---\nname: keeper\ndescription: %s\n---\nbody\n' "$long" \
  > "$fixture/home/.claude/agents/keeper.md"
printf -- '---\nname: doomed\ndescription: about to be deleted\n---\nbody\n' \
  > "$fixture/home/.claude/agents/doomed.md"

atlas() {
  python3 plugins/atlas/scripts/atlas.py --project "$fixture/repo" \
    --user-root "$fixture/home/.claude" "$@"
}
atlas scan --no-bodies --out "$fixture/before.json" >/dev/null

# Shorten one, delete one, add one — the three things a campaign does.
printf -- '---\nname: keeper\ndescription: much shorter now\n---\nbody\n' \
  > "$fixture/home/.claude/agents/keeper.md"
rm "$fixture/home/.claude/agents/doomed.md"
printf -- '---\nname: newcomer\ndescription: just arrived\n---\nbody\n' \
  > "$fixture/home/.claude/agents/newcomer.md"

atlas diff "$fixture/before.json" --json > "$fixture/diff.json"
python3 - "$fixture/diff.json" <<'PY'
import json, sys

d = json.load(open(sys.argv[1]))
failures = []

def names(section):
    return sorted(e["name"] for e in d[section])

if names("removed") != ["doomed"]: failures.append(f"removed: {names('removed')}")
if names("added") != ["newcomer"]: failures.append(f"added: {names('added')}")
if names("changed") != ["keeper"]: failures.append(f"changed: {names('changed')}")

if d["changed"] and d["changed"][0]["delta"] >= 0:
    failures.append("a shortened description was not reported as a decrease")
if d["residentChars"]["delta"] >= 0:
    failures.append(f"total did not shrink: {d['residentChars']}")

# Every per-item delta plus the total must agree, or the report is telling two
# different stories about the same campaign.
moved = sum(e["delta"] for section in ("added", "removed", "changed") for e in d[section])
if moved != d["residentChars"]["delta"]:
    failures.append(f"item deltas sum to {moved}, total says {d['residentChars']['delta']}")

for line in failures:
    print(f"FAIL: {line}")
sys.exit(1 if failures else 0)
PY

# Diffing against something that is not a scan must say so, not traceback.
if atlas diff /dev/null 2>"$fixture/err.txt"; then
  echo "FAIL: accepted a file that is not a scan"
  exit 1
fi
grep -q 'not a scan written by atlas' "$fixture/err.txt" \
  || { echo "FAIL: unhelpful error for a non-scan file"; cat "$fixture/err.txt"; exit 1; }
echo "PASS: diff reports added, removed and resized items with totals that agree"
)
