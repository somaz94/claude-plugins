#!/usr/bin/env bash
#
# pair-lockstep-nudge.sh
#
# Claude Code PostToolUse(Edit|Write|MultiEdit) hook shipped with the doc-mirror
# plugin.
#
# Purpose:
#   A repository that keeps `README.md` beside `README-ko.md` has no mechanism
#   that notices when only one of them changes. The build is green, the linter
#   is quiet, the diff looks deliberate, and the review sees one file. This hook
#   is that mechanism, placed at the only moment it can still be cheap to act
#   on: immediately after the edit, while the other half is still in mind.
#
#   WARN-ONLY. PostToolUse fires after the tool has run, so nothing is undone
#   and nothing is re-prompted. The hook emits a visible systemMessage plus an
#   additionalContext line reminding Claude to edit the counterpart next.
#
# Which files pair:
#   Discovered, never configured — the same rule the plugin's script uses. A
#   document `<stem>.md` pairs with `<stem>-<lang>.md` or `<stem>.<lang>.md`,
#   and `<lang>` counts as a language only if some OTHER pair in the same
#   directory already proves it. That is what separates a real `README-ko.md`
#   from `06-followup-fluent-bit.md`, whose tail is also three lowercase
#   letters and means nothing.
#
# Decision:
#   - counterpart exists but was not edited this session -> nudge (may be stale)
#   - counterpart missing, and this directory mirrors most of its documents
#                                                 -> nudge (mirror never created)
#   - counterpart edited this session              -> silent (in sync)
#   - no counterpart convention in this directory  -> silent
#
# Session-scope detection:
#   PostToolUse is stateless, so "was the counterpart edited this session?" is
#   answered by parsing transcript_path — a JSONL of API messages where each
#   assistant turn carries content blocks, and an Edit/Write/MultiEdit appears
#   as {"type":"tool_use","name":...,"input":{"file_path":...}}.
#
# Input: PostToolUse JSON on stdin
#        ({ tool_input.file_path, transcript_path, cwd, tool_name, ... }).
#
# Notes:
#   - Fails OPEN. Any parse error, unreadable transcript, or unreadable
#     directory produces silence. A nudge is a courtesy, never a boundary, and
#     one that misfires on every edit gets the whole plugin uninstalled.
#   - Pure-bash fast path: a payload with no ".md" skips the python3 spawn, so
#     the common source-code edit stays free.
#   - No single quotes inside the python3 -c block — one would close the wrapper
#     and leak to bash. Every literal there is double-quoted.
#
set -euo pipefail

payload="$(cat)"

# Fast path: only Markdown can be half of a documentation pair.
case "$payload" in
  *.md*) : ;;
  *) exit 0 ;;
esac

printf '%s' "$payload" | python3 -c '
import json, os, re, sys

def silent():
    sys.exit(0)

try:
    d = json.load(sys.stdin)
except Exception:
    silent()
if not isinstance(d, dict):
    silent()

fp = (d.get("tool_input") or {}).get("file_path") or ""
if not fp or not fp.endswith(".md"):
    silent()

path = os.path.normpath(os.path.expanduser(fp))
directory = os.path.dirname(path)
if not os.path.isdir(directory):
    silent()

LANG = r"[a-z]{2,3}(?:-[a-z]{2,4})?"
PATTERNS = [
    re.compile(r"^(?P<stem>.+)-(?P<lang>" + LANG + r")$"),
    re.compile(r"^(?P<stem>.+)\.(?P<lang>" + LANG + r")$"),
]

def split(stem):
    for p in PATTERNS:
        m = p.match(stem)
        if m:
            return m.group("stem"), m.group("lang")
    return None

try:
    stems = {}
    for name in os.listdir(directory):
        if name.endswith(".md") and os.path.isfile(os.path.join(directory, name)):
            stems[name[:-3]] = os.path.join(directory, name)
except Exception:
    silent()

# Which languages this directory actually keeps, proven by a mirror that has a
# source sitting beside it.
langs = set()
mirrored_sources = set()
for stem in stems:
    s = split(stem)
    if s and s[0] in stems:
        langs.add(s[1])
        mirrored_sources.add(s[0])
if not langs:
    silent()

edited_stem = os.path.basename(path)[:-3]
edited_split = split(edited_stem)

if edited_split and edited_split[1] in langs and edited_split[0] in stems:
    # A mirror was edited; its counterpart is the source.
    counterparts = [stems[edited_split[0]]]
    role, other = "mirror", "source document"
elif edited_stem in stems and not (edited_split and edited_split[1] in langs):
    # A source was edited; its counterparts are every mirror of it, plus any
    # language this directory keeps that this document has no mirror for.
    counterparts = []
    missing = []
    for lang in sorted(langs):
        for sep in ("-", "."):
            candidate = edited_stem + sep + lang
            if candidate in stems:
                counterparts.append(stems[candidate])
                break
        else:
            missing.append(lang)
    role, other = "source document", "mirror"
    if not counterparts and missing:
        # No mirror at all. Only worth saying where most documents here are
        # mirrored — otherwise this file is simply not one of the translated
        # ones, which is a decision, not an oversight.
        total = len([s for s in stems if not (split(s) and split(s)[1] in langs)])
        if total < 3 or len(mirrored_sources) / max(total, 1) < 0.5:
            silent()
        langs_text = ", ".join(missing)
        rel = os.path.basename(path)
        json.dump({
            "systemMessage": (
                "doc-mirror: edited %s, which has no %s mirror — most documents "
                "in this directory have one." % (rel, langs_text)
            ),
            "hookSpecificOutput": {
                "hookEventName": "PostToolUse",
                "additionalContext": (
                    "You edited %s. Most documents in that directory keep a %s "
                    "mirror and this one does not. Ask the user whether to "
                    "create it; do not translate the content unprompted."
                    % (rel, langs_text)
                ),
            },
        }, sys.stdout)
        sys.exit(0)
else:
    silent()

if not counterparts:
    silent()

# Which files were edited earlier in this session?
tpath = d.get("transcript_path") or ""
tpath = os.path.expanduser(tpath) if tpath else ""
if not tpath or not os.path.isfile(tpath):
    silent()  # fail-open: no history means no claim about staleness

edited = set()
try:
    with open(tpath, "r", encoding="utf-8", errors="replace") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except Exception:
                continue
            if rec.get("type") != "assistant":
                continue
            msg = rec.get("message")
            if not isinstance(msg, dict):
                continue
            content = msg.get("content")
            if not isinstance(content, list):
                continue
            for block in content:
                if (
                    isinstance(block, dict)
                    and block.get("type") == "tool_use"
                    and block.get("name") in ("Edit", "Write", "MultiEdit")
                ):
                    other_fp = (block.get("input") or {}).get("file_path")
                    if other_fp:
                        edited.add(os.path.normpath(os.path.expanduser(other_fp)))
except Exception:
    silent()

stale = [c for c in counterparts if os.path.normpath(c) not in edited]
if not stale:
    silent()

names = ", ".join(os.path.basename(c) for c in stale)
rel = os.path.basename(path)
json.dump({
    "systemMessage": (
        "doc-mirror: edited the %s %s but its %s (%s) was not edited this "
        "session — the pair may now be out of step."
        % (role, rel, other, names)
    ),
    "hookSpecificOutput": {
        "hookEventName": "PostToolUse",
        "additionalContext": (
            "You edited the %s %s. Its %s (%s) was not edited in this session "
            "and may now be out of step. Carry the same change across so the "
            "pair stays in lockstep, matching structure rather than translating "
            "word for word."
            % (role, rel, other, names)
        ),
    },
}, sys.stdout)
sys.exit(0)
'

exit 0
