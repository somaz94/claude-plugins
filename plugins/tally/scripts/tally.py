#!/usr/bin/env python3
"""Count what a Claude Code harness did, without keeping what it said.

Reads local session transcripts, the optional hook decision log written by
`log-wrap.sh`, and the shape of one or more `.claude/` config directories, and
prints a JSON document of COUNTS ONLY: weekly buckets plus totals. No path,
command, prompt, project, agent or hook name ever reaches the output; every
key is fixed by `schema/tally-v0.schema.json`, and every value is a number, a
date or a schema-fixed label.

The transcript format is Claude Code's internal storage, not a public API. A
record this script does not recognise is counted, never guessed at, so format
drift shows up as a rising `unrecognized_lines` rather than as wrong numbers.

stdlib only, like everything else here: a tool you run over your own private
transcripts must not need an install step before it can be trusted.
"""

from __future__ import annotations

import argparse
import datetime as dt
import functools
import json
import os
import re
import shlex
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _shared import parse_frontmatter  # noqa: E402

SCHEMA_ID = "tally/v0"
CLASSIFIER_ID = "mutating-v0"
TOOL_VERSION = "0.1.0"

FILE_EDIT_TOOLS = {"Edit", "Write", "MultiEdit", "NotebookEdit"}
AGENT_TOOLS = {"Agent", "Task"}
USER_REJECT_MARK = "The user doesn't want to proceed with this tool use"
INTERRUPT_MARK = "[Request interrupted by user"

HOOK_ATTACHMENTS = {
    "hook_success": "success",
    "hook_additional_context": "additional_context",
    "hook_system_message": "system_message",
    "hook_non_blocking_error": "non_blocking_error",
    "hook_blocking_error": "blocking_error",
}
LOG_DECISIONS = ("block", "deny", "ask", "allow", "nudge", "info", "nonblocking-error", "output")
LOG_TIERS = ("block", "ask", "warn", "info")
HOOK_EVENTS = (
    "PreToolUse", "PostToolUse", "UserPromptSubmit", "SessionStart", "SessionEnd",
    "Stop", "SubagentStop", "PreCompact", "Notification",
)

# --------------------------------------------------------------------------
# mutating-command classifier (versioned as CLASSIFIER_ID: change the id when
# the lists change, or two outputs stop being comparable)
# --------------------------------------------------------------------------

_ENV_ASSIGN = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=")
_HEREDOC = re.compile(r"<<-?\s*(['\"]?)([A-Za-z_][A-Za-z0-9_]*)\1")
_PREFIXES = {"sudo", "command", "exec", "time", "nohup"}
# Throwaway locations. A change confined to them is counted as scratch work, not
# as a change to the workspace: reviewers routinely build a copy there to test a
# claim, and lumping that in would hide the mutations that matter.
_TEMP_PATH = re.compile(r"^(/tmp/|/private/tmp/|/private/var/folders/|/var/folders/|\$TMPDIR|\$\{TMPDIR)")
_TEMP_MARK = re.compile(r"mktemp|/tmp/|/var/folders/|\$TMPDIR|\$\{TMPDIR")
_HELP = {"-h", "--help", "help"}

FS_MUTATORS = {"rm", "rmdir", "mv", "cp", "ln", "mkdir", "touch", "truncate", "chmod", "chown"}
GIT_MUTATORS = {
    "add", "commit", "push", "reset", "rebase", "merge", "cherry-pick", "revert",
    "rm", "mv", "clean", "restore", "am", "apply", "pull",
}
GIT_APPLY_READ = {"--check", "--stat", "--numstat", "--summary"}
GIT_TAG_READ = {"-l", "--list", "-n", "--contains", "--no-contains", "--points-at",
                "--merged", "--no-merged", "-v", "--verify"}
GIT_STASH_READ = {"list", "show"}
FORGE_MUTATORS = {
    "pr": {"create", "merge", "close", "reopen", "ready", "comment", "review", "edit"},
    "mr": {"create", "merge", "close", "reopen", "approve", "note", "update"},
    "issue": {"create", "close", "reopen", "comment", "edit", "delete"},
    "release": {"create", "delete", "edit", "upload"},
    "repo": {"create", "delete", "fork", "edit", "archive", "rename"},
    "workflow": {"run", "enable", "disable"},
    "run": {"rerun", "cancel", "delete"},
    "ci": {"run", "retry", "cancel", "delete"},
    "label": {"create", "delete", "edit"},
    "secret": {"set", "delete"},
    "variable": {"set", "delete"},
}
API_WRITE_FLAGS = {"-f", "-F", "--field", "--raw-field", "--input"}
API_WRITE_METHODS = {"POST", "PUT", "PATCH", "DELETE"}
INFRA_MUTATORS = {
    "kubectl": {"apply", "create", "delete", "patch", "replace", "scale", "edit",
                "label", "annotate", "cordon", "uncordon", "drain", "taint", "rollout"},
    "helm": {"install", "upgrade", "uninstall", "rollback", "push"},
    "terraform": {"apply", "destroy", "import", "taint", "untaint"},
    "docker": {"push", "rm", "rmi"},
    "npm": {"publish"},
    "cargo": {"publish"},
}


def _strip_heredocs(command: str) -> str:
    """Drop heredoc bodies: they are data fed to a program, not commands."""
    lines = command.split("\n")
    out, i = [], 0
    while i < len(lines):
        out.append(lines[i])
        tags = [m.group(2) for m in _HEREDOC.finditer(lines[i])]
        i += 1
        for tag in tags:
            while i < len(lines) and lines[i].strip() != tag:
                i += 1
            i += 1
    return "\n".join(out)


_PUNCT = set("();<>|&\n")


def _is_separator(tok: str) -> bool:
    """shlex groups adjacent punctuation (`);`, `)|`), so test the run as a whole:
    anything carrying ; | ( ) or a newline ends a command, as does a bare & or &&.
    What is left (`>`, `>>`, `>&`, `<<`) is a redirection and stays in the words."""
    if not set(tok) <= _PUNCT:
        return False
    return bool(set(tok) & set(";|()\n")) or tok in ("&", "&&")


def _tokens(command: str) -> list[str]:
    """Quote-aware tokens with control operators (newline included) kept apart."""
    try:
        return _shlex(command)
    except ValueError:
        pass
    # An unbalanced quote somewhere: retry line by line so one bad line does not
    # flatten the whole command, and split a hopeless line on operators by hand.
    out: list[str] = []
    for line in command.split("\n"):
        try:
            out += _shlex(line)
        except ValueError:
            out += re.sub(r"(&&|\|\||;|\|)", r" \1 ", line).split()
        out.append("\n")
    return out


def _shlex(text: str) -> list[str]:
    lex = shlex.shlex(text, posix=True, punctuation_chars="();<>|&\n")
    lex.whitespace = " \t\r"
    lex.whitespace_split = True
    return list(lex)


_KEYWORDS = {"do", "then", "else", "elif", "!", "{", "}", "(", ")", "time"}
_REDIRECT = re.compile(r"^[0-9]*(>|>>|<|<<|<<<|>&|<&|&>)$")


def _drop_redirects(words: list[str]) -> list[str]:
    """Remove redirections and stray grouping punctuation. Output redirection is
    left uncounted on purpose: `> /dev/null` and `2>&1` are everywhere."""
    out, skip = [], False
    for w in words:
        if skip:
            skip = False
            continue
        if _REDIRECT.match(w):
            skip = True
            continue
        if w in (")", "}", "(", "{"):
            continue
        out.append(w)
    return out


def _segments(command: str) -> list[list[str]]:
    out, words = [], []
    for tok in _tokens(_strip_heredocs(command)) + ["\n"]:
        if not _is_separator(tok):
            words.append(tok)
            continue
        words = _drop_redirects(words)
        while words and (_ENV_ASSIGN.match(words[0]) or words[0] in _PREFIXES
                         or words[0] in _KEYWORDS):
            words = words[1:]
        if words and words[0] == "taskpolicy":
            words = words[2:] if len(words) > 1 and words[1].startswith("-") else words[1:]
        if words and words[0] == "nice":
            words = words[3:] if len(words) > 2 and words[1] == "-n" else words[1:]
        if words:
            out.append(words)
        words = []
    return out


def _git_subcommand(words: list[str]) -> tuple[str | None, list[str], str | None]:
    """(subcommand, its arguments, the -C directory if one was given)."""
    i, where = 1, None
    while i < len(words):
        w = words[i]
        if w in ("-C", "-c", "--git-dir", "--work-tree"):
            if w == "-C" and i + 1 < len(words):
                where = words[i + 1]
            i += 2
            continue
        if w.startswith("-"):
            i += 1
            continue
        return w, words[i + 1:], where
    return None, [], where


def _api_writes(rest: list[str]) -> bool:
    for i, w in enumerate(rest):
        nxt = rest[i + 1].upper() if i + 1 < len(rest) else ""
        if w in ("-X", "--method") and nxt in API_WRITE_METHODS:
            return True
        if w.startswith("-X") and w[2:].upper() in API_WRITE_METHODS:
            return True
        if w.startswith("--method=") and w.split("=", 1)[1].upper() in API_WRITE_METHODS:
            return True
    if len(rest) > 1 and rest[1] == "graphql":
        # GraphQL reads are POSTs with -f too; only a `mutation` changes anything.
        return any(re.search(r"\bmutation\b", w) for w in rest)
    return any(w in API_WRITE_FLAGS for w in rest)


def _segment_mutates(words: list[str]) -> tuple[bool, list[str]]:
    """(does this segment change state, the operands it changes)."""
    head = Path(words[0]).name
    rest = words[1:]
    if any(w in _HELP for w in rest[:3]):
        return False, []
    operands = [w for w in rest if not w.startswith("-")]
    if head in FS_MUTATORS:
        # cp and ln only write their last operand; the sources are read.
        return True, operands[-1:] if head in ("cp", "ln") else operands
    if head in ("sed", "perl") and any(w == "-i" or w.startswith("-i") for w in rest):
        return True, operands[1:]
    if head == "git":
        sub, args, where = _git_subcommand(words)
        target = [where] if where else []
        if sub == "apply" and any(a in GIT_APPLY_READ for a in args):
            return False, []
        if sub in GIT_MUTATORS:
            return True, target
        if sub == "tag":
            named = any(not a.startswith("-") for a in args)
            return named and not any(a in GIT_TAG_READ for a in args), target
        if sub == "stash":
            return not args or args[0] not in GIT_STASH_READ, target
        return False, []
    if head in ("gh", "glab"):
        if not rest:
            return False, []
        if rest[0] == "api":
            return _api_writes(rest), []
        return len(rest) > 1 and rest[1] in FORGE_MUTATORS.get(rest[0], set()), []
    if head in INFRA_MUTATORS:
        subs = [w for w in rest if not w.startswith("-")]
        if head == "terraform" and subs[:1] == ["state"]:
            return len(subs) > 1 and subs[1] in {"rm", "mv", "push"}, []
        if head == "kubectl" and subs[:1] == ["rollout"]:
            return len(subs) > 1 and subs[1] in {"restart", "undo", "pause", "resume"}, []
        return bool(subs) and subs[0] in INFRA_MUTATORS[head], []
    return False, []


def _is_temp(word: str, line_has_temp: bool) -> bool:
    return bool(_TEMP_PATH.match(word)) or (word.startswith("$") and line_has_temp)


# Any writer the classifier knows, as a bare word. A command without one cannot
# mutate under this classifier, so it skips tokenizing, which dominates run time.
_MAY_MUTATE = re.compile(
    r"(?<![\w-])(rm|rmdir|mv|cp|ln|mkdir|touch|truncate|chmod|chown|sed|perl|git|gh|glab|"
    r"kubectl|helm|terraform|docker|npm|cargo)(?![\w-])"
)


@functools.lru_cache(maxsize=8192)
def classify(command: str) -> str | None:
    """None (no state change), "scratch" (only temp locations) or "workspace".

    Scratch is a heuristic: every operand of every mutating segment must be a
    temp path, a variable on a line that mentions a temp location, or a relative
    path after a `cd` into one. Anything it cannot place counts as workspace, so
    the workspace count errs high rather than low.
    """
    if not _MAY_MUTATE.search(command):
        return None
    line_has_temp = bool(_TEMP_MARK.search(command))
    in_temp = False
    found = None
    for words in _segments(command):
        if words[0] == "cd":
            in_temp = len(words) > 1 and _is_temp(words[1], line_has_temp)
            continue
        mutates, operands = _segment_mutates(words)
        if not mutates:
            continue
        scratch = all(
            _is_temp(w, line_has_temp) or (in_temp and not w.startswith(("/", "~")))
            for w in operands
        ) if operands else in_temp
        if not scratch:
            return "workspace"
        found = "scratch"
    return found


def is_mutating(command: str) -> bool:
    """True when any segment of a shell command line changes state."""
    return classify(command) is not None


# --------------------------------------------------------------------------
# buckets
# --------------------------------------------------------------------------


def _zero_bucket() -> dict:
    return {
        "sessions": 0,
        "main": {"tool_calls": 0, "file_edit_calls": 0, "mutating_bash_calls": 0,
                 "scratch_bash_calls": 0,
                 "questions_asked": 0, "interrupts": 0, "compactions": 0,
                 "subagent_launches": 0},
        "permissions": {"auto_allowed": 0, "user_allowed_once": 0, "user_allowed_rule": 0,
                        "auto_denied": 0, "hook_denied": 0, "user_rejected": 0, "other": 0},
        "subagents": {"runs": 0, "runs_custom": 0, "runs_with_mutation": 0, "tool_calls": 0,
                      "file_edit_calls": 0, "mutating_bash_calls": 0, "scratch_bash_calls": 0,
                      "runs_readonly_declared": 0, "runs_readonly_declared_with_mutation": 0,
                      "readonly_declared_mutating_calls": 0},
        "hooks_transcript": {k: 0 for k in sorted(set(HOOK_ATTACHMENTS.values()) | {"other"})},
        "hooks_log": {
            "decisions": {k: 0 for k in LOG_DECISIONS + ("other",)},
            "by_tier": {k: 0 for k in LOG_TIERS + ("other", "untiered")},
        },
    }


def _parse_ts(value) -> dt.datetime | None:
    if not isinstance(value, str):
        return None
    try:
        return dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def _week(ts: dt.datetime) -> str:
    year, week, _ = ts.isocalendar()
    return f"{year}-W{week:02d}"


class Tally:
    def __init__(self, since: dt.date | None, until: dt.date | None,
                 readonly_agents: frozenset[str] = frozenset()):
        self.since, self.until = since, until
        self.readonly_agents = readonly_agents
        self.weeks: dict[str, dict] = {}
        self.first: dt.date | None = None
        self.last: dt.date | None = None
        self.files_read = 0
        self.lines_read = 0
        self.unrecognized_lines = 0

    def in_window(self, ts: dt.datetime | None) -> bool:
        if ts is None:
            return False
        day = ts.date()
        return not ((self.since and day < self.since) or (self.until and day > self.until))

    def bucket(self, ts: dt.datetime) -> dict:
        day = ts.date()
        self.first = day if self.first is None or day < self.first else self.first
        self.last = day if self.last is None or day > self.last else self.last
        key = _week(ts)
        if key not in self.weeks:
            self.weeks[key] = _zero_bucket()
        return self.weeks[key]

    # ---- transcripts -------------------------------------------------------

    def read_transcript(self, path: Path, *, subagent: bool, custom: bool = False,
                        readonly: bool = False) -> None:
        self.files_read += 1
        session_counted = run_counted = False
        run_mutations = 0
        run_bucket = None
        try:
            fh = path.open(encoding="utf-8", errors="replace")
        except OSError:
            return
        with fh:
            for line in fh:
                self.lines_read += 1
                # Request-log records carry no tool calls and are the bulkiest lines.
                if line.startswith('{"type":"api-request'):
                    continue
                try:
                    rec = json.loads(line)
                except ValueError:
                    self.unrecognized_lines += 1
                    continue
                if not isinstance(rec, dict):
                    self.unrecognized_lines += 1
                    continue
                ts = _parse_ts(rec.get("timestamp"))
                if not self.in_window(ts):
                    continue
                b = self.bucket(ts)
                if subagent and not run_counted:
                    b["subagents"]["runs"] += 1
                    b["subagents"]["runs_custom"] += int(custom)
                    b["subagents"]["runs_readonly_declared"] += int(readonly)
                    run_counted, run_bucket = True, b
                if not subagent and not session_counted:
                    b["sessions"] += 1
                    session_counted = True
                n = self._record(rec, b, subagent)
                run_mutations += n
                if readonly:
                    b["subagents"]["readonly_declared_mutating_calls"] += n
        if run_mutations and run_bucket is not None:
            run_bucket["subagents"]["runs_with_mutation"] += 1
            run_bucket["subagents"]["runs_readonly_declared_with_mutation"] += int(readonly)

    def _record(self, rec: dict, b: dict, subagent: bool) -> int:
        """Count one record into bucket `b`; return how many mutating calls it made."""
        kind = rec.get("type")
        side = b["subagents"] if subagent else b["main"]
        mutated = 0

        pd = rec.get("permissionDecision")
        if isinstance(pd, dict):
            b["permissions"][self._permission_bucket(pd)] += 1

        if kind == "assistant":
            for block in _content(rec):
                if block.get("type") != "tool_use":
                    continue
                name = block.get("name")
                side["tool_calls"] += 1
                if name in FILE_EDIT_TOOLS:
                    side["file_edit_calls"] += 1
                    mutated += 1
                elif name == "Bash":
                    cmd = (block.get("input") or {}).get("command")
                    effect = classify(cmd) if isinstance(cmd, str) else None
                    if effect == "workspace":
                        side["mutating_bash_calls"] += 1
                        mutated += 1
                    elif effect == "scratch":
                        side["scratch_bash_calls"] += 1
                elif not subagent and name == "AskUserQuestion":
                    side["questions_asked"] += 1
                elif not subagent and name in AGENT_TOOLS:
                    side["subagent_launches"] += 1
        elif kind == "user":
            for block in _content(rec):
                text = _text(block)
                if block.get("type") == "tool_result" and USER_REJECT_MARK in text:
                    b["permissions"]["user_rejected"] += 1
                elif not subagent and INTERRUPT_MARK in text:
                    side["interrupts"] += 1
        elif kind == "attachment":
            att = (rec.get("attachment") or {}).get("type") or ""
            if att.startswith("hook_"):
                b["hooks_transcript"][HOOK_ATTACHMENTS.get(att, "other")] += 1
        elif kind == "system" and rec.get("subtype") == "compact_boundary" and not subagent:
            side["compactions"] += 1
        return mutated

    @staticmethod
    def _permission_bucket(pd: dict) -> str:
        decision, source = pd.get("decision"), pd.get("source")
        if decision == "accept":
            return {"config": "auto_allowed", "user_temporary": "user_allowed_once",
                    "user_permanent": "user_allowed_rule"}.get(source, "other")
        if decision == "reject":
            return {"config": "auto_denied", "hook": "hook_denied"}.get(source, "other")
        return "other"

    def read_projects(self, root: Path) -> None:
        if not root.is_dir():
            return
        for project in sorted(p for p in root.iterdir() if p.is_dir()):
            for session in sorted(project.glob("*.jsonl")):
                self.read_transcript(session, subagent=False, custom=False)
            for run in sorted(project.glob("*/subagents/*.jsonl")):
                custom, agent_type = _run_meta(run)
                self.read_transcript(run, subagent=True, custom=custom,
                                     readonly=custom and agent_type in self.readonly_agents)

    # ---- hook decision log -------------------------------------------------

    def read_hook_log(self, log_dir: Path) -> None:
        if not log_dir.is_dir():
            return
        for path in sorted(log_dir.glob("decisions-*.jsonl")):
            self.files_read += 1
            for line in path.open(encoding="utf-8", errors="replace"):
                self.lines_read += 1
                try:
                    rec = json.loads(line)
                except ValueError:
                    self.unrecognized_lines += 1
                    continue
                ts = _parse_ts(rec.get("ts")) if isinstance(rec, dict) else None
                if not self.in_window(ts):
                    if ts is None:
                        self.unrecognized_lines += 1
                    continue
                hl = self.bucket(ts)["hooks_log"]
                decision = rec.get("decision")
                hl["decisions"][decision if decision in LOG_DECISIONS else "other"] += 1
                tier = rec.get("tier")
                hl["by_tier"][tier if tier in LOG_TIERS else ("untiered" if not tier else "other")] += 1

    # ---- output ------------------------------------------------------------

    def result(self, harness: dict) -> dict:
        weeks = [dict(week=k, **v) for k, v in sorted(self.weeks.items())]
        totals = _zero_bucket()
        for w in self.weeks.values():
            _add(totals, w)
        return {
            "schema": SCHEMA_ID,
            "tool_version": TOOL_VERSION,
            "classifier": CLASSIFIER_ID,
            "generated": dt.date.today().isoformat(),
            "window": {
                "first": self.first.isoformat() if self.first else None,
                "last": self.last.isoformat() if self.last else None,
                "weeks": len(weeks),
            },
            "input": {"files_read": self.files_read, "lines_read": self.lines_read,
                      "unrecognized_lines": self.unrecognized_lines},
            "harness": harness,
            "totals": totals,
            "weeks": weeks,
        }


def _content(rec: dict) -> list[dict]:
    content = (rec.get("message") or {}).get("content")
    if isinstance(content, str):
        return [{"type": "text", "text": content}]
    return [b for b in content or [] if isinstance(b, dict)]


def _text(block: dict) -> str:
    value = block.get("text") if block.get("type") == "text" else block.get("content")
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        return " ".join(v.get("text", "") for v in value if isinstance(v, dict))
    return ""


def _run_meta(run: Path) -> tuple[bool, str | None]:
    """(is a user-defined agent, its agent type) from the run's sidecar metadata."""
    try:
        meta = json.loads(run.with_suffix(".meta.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False, None
    if not isinstance(meta, dict):
        return False, None
    return meta.get("isBuiltIn") is False, meta.get("agentType")


def _add(into: dict, other: dict) -> None:
    for key, value in other.items():
        if isinstance(value, dict):
            _add(into[key], value)
        else:
            into[key] += value


# --------------------------------------------------------------------------
# harness shape
# --------------------------------------------------------------------------


def harness_shape(configs: list[Path]) -> tuple[dict, frozenset[str]]:
    """The harness's size, plus the names of agents declared without a write tool.

    The names are used only to classify sub-agent runs; they never reach the output.
    """
    agents = Counter()
    readonly: set[str] = set()
    hooks = Counter()
    commands = skills = 0
    for root in configs:
        for path in sorted((root / "agents").glob("*.md")):
            fields, _ = parse_frontmatter(path.read_text(encoding="utf-8", errors="replace"))
            agents["total"] += 1
            tools = fields.get("tools")
            if tools is None:
                agents["inherit_all_tools"] += 1
                continue
            names = set(re.findall(r"[A-Za-z*]+", tools))
            if names & (FILE_EDIT_TOOLS | {"*"}):
                agents["write_capable"] += 1
            else:
                readonly.add(fields.get("name") or path.stem)
        commands += sum(1 for _ in (root / "commands").rglob("*.md")) if (root / "commands").is_dir() else 0
        skills += sum(1 for _ in (root / "skills").glob("*/SKILL.md"))
        for name in ("settings.json", "settings.local.json"):
            try:
                data = json.loads((root / name).read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            for event, entries in ((data.get("hooks") or {}) if isinstance(data, dict) else {}).items():
                for entry in entries if isinstance(entries, list) else []:
                    n = len((entry or {}).get("hooks") or []) if isinstance(entry, dict) else 0
                    hooks[event if event in HOOK_EVENTS else "other"] += n
    return {
        "config_dirs": len(configs),
        "agents": {"total": agents["total"], "write_capable": agents["write_capable"],
                   "inherit_all_tools": agents["inherit_all_tools"]},
        "commands": commands,
        "skills": skills,
        "hooks": {"total": sum(hooks.values()),
                  "by_event": {e: hooks[e] for e in HOOK_EVENTS + ("other",)}},
    }, frozenset(readonly)


# --------------------------------------------------------------------------


def _date(value: str) -> dt.date:
    return dt.date.fromisoformat(value)


def main(argv: list[str] | None = None) -> int:
    home = Path.home() / ".claude"
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--projects", type=Path, default=home / "projects",
                    help="transcript root (default: ~/.claude/projects)")
    ap.add_argument("--log-dir", type=Path,
                    default=Path(os.environ.get("TALLY_LOG_DIR") or home / "tally" / "hook-decisions"),
                    help="log-wrap.sh output directory (default: $TALLY_LOG_DIR, else ~/.claude/tally/hook-decisions)")
    ap.add_argument("--config", type=Path, action="append",
                    help="a .claude directory to measure; repeatable (default: ~/.claude)")
    ap.add_argument("--since", type=_date, help="first day to count, YYYY-MM-DD")
    ap.add_argument("--until", type=_date, help="last day to count, YYYY-MM-DD")
    ap.add_argument("--out", type=Path, help="write here instead of stdout")
    args = ap.parse_args(argv)

    configs = [c.expanduser() for c in (args.config or [home])]
    harness, readonly = harness_shape(configs)
    t = Tally(args.since, args.until, readonly)
    t.read_projects(args.projects.expanduser())
    t.read_hook_log(args.log_dir.expanduser())
    text = json.dumps(t.result(harness), indent=2) + "\n"
    if args.out:
        args.out.write_text(text, encoding="utf-8")
    else:
        sys.stdout.write(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
