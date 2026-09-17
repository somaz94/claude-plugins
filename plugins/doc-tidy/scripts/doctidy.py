#!/usr/bin/env python3
"""doc-tidy - measures which documents should exist and where they belong.

A repository collects documents the way a desk collects paper. A handoff
written for one conversation gets committed. A finished migration keeps its
plan and phase reports beside the current guides. A plan file that Claude Code
has already deleted is still named, as a reference, by the config that outlived
it. None of it breaks a build, so none of it is ever cleaned up.

This script measures and never judges:

  scan    every document in one repository: its class, who links to it, which
          of its links are broken, whether it looks like a session byproduct or
          finished history, and where the repository keeps its archive
  sweep   the same, cheaply, for every repository under one or more roots
  plans   Claude Code plan files near automatic deletion, and references to
          plans that are already gone
  relink  every link edit a proposed move, archive, merge or delete needs
  apply   the only command that writes: it applies a relink result somebody
          approved, after checking that every edit still matches the files

Deliberately NOT here:
  - Verdicts. Whether a committed prompt is a reusable template or a leftover
    is decided by a reviewer who reads it with the repository's own rules in
    hand. Every class assigned here is a candidate.
  - What a document says. Content freshness, translation drift and prose
    quality belong to other tools.

Design contract:
  - Standard library only; runs on Python 3.10+.
  - Conventions are discovered, not configured. Rules that name things live in
    ../rules/rules.tsv and are validated by --check-rules.
"""

from __future__ import annotations

import argparse
import datetime as dt
import fnmatch
import hashlib
import json
import os
import posixpath
import re
import subprocess
import sys
import unicodedata
import urllib.parse
import zlib
from bisect import bisect_right
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable

TOOL = "doc-tidy"
SCHEMA_VERSION = 1
RULES_DIR = Path(__file__).resolve().parent.parent / "rules"

EXIT_OK = 0
EXIT_FINDINGS = 1
EXIT_USAGE = 2

SEVERITIES = ("critical", "warning", "suggestion")
SEVERITY_MARK = {"critical": "🔴", "warning": "🟡", "suggestion": "🟢"}
TEXT_LINES_PER_CODE = 20

DOC_EXTENSIONS = (".md", ".markdown", ".mdx")
GIT_TIMEOUT = 60


class UsageError(Exception):
    """Bad arguments or input: exit 2."""


class RulesError(Exception):
    """The rules directory is missing or unreadable: exit 2."""


# ---------------------------------------------------------------------------
# Rules
# ---------------------------------------------------------------------------

RULE_COLUMNS = ("id", "kind", "scope", "strength", "regex", "example", "counter", "note")
KIND_SCOPES: dict[str, frozenset[str]] = {
    "byproduct": frozenset({"name", "title", "head", "path"}),
    "historical": frozenset({"name", "title", "head", "path"}),
    "root-allow": frozenset({"name"}),
    "generated": frozenset({"name", "dir"}),
    "archive-dir": frozenset({"dir"}),
    "backup-dir": frozenset({"dir"}),
    "plan-placeholder": frozenset({"slug"}),
    "status-done": frozenset({"line"}),
    "status-open": frozenset({"line"}),
}
STRENGTHS = ("strong", "weak", "off")
COUNTER_REQUIRED = frozenset({("byproduct", "name"), ("historical", "name")})
COUNTER_SEPARATOR = ";;"
# Every rule must compile on Python 3.10, where atomic groups and possessive
# quantifiers do not exist, and every rule is compiled with IGNORECASE - a
# global inline flag inside the pattern would be redundant at best.
UNPORTABLE_SYNTAX = re.compile(r"\(\?>|(?<!\\)[*+?}]\+|\(\?[aiLmsux]+\)")


@dataclass(frozen=True)
class Rule:
    id: str
    kind: str
    scope: str
    strength: str
    pattern: str
    regex: re.Pattern[str]
    example: str
    counters: tuple[str, ...]
    note: str
    source: str


@dataclass
class RuleSet:
    directory: Path
    rules: dict[str, Rule]
    local: bool = False
    overridden: list[str] = field(default_factory=list)

    def active(self, kind: str, scope: str | None = None) -> list[Rule]:
        return [
            rule for rule in self.rules.values()
            if rule.kind == kind and rule.strength != "off" and (scope is None or rule.scope == scope)
        ]

    @property
    def disabled(self) -> list[str]:
        return sorted(rule.id for rule in self.rules.values() if rule.strength == "off")

    def describe(self) -> dict[str, Any]:
        return {
            "dir": str(self.directory),
            "rows": len(self.rules),
            "local": self.local,
            "overridden": self.overridden,
            "disabled": self.disabled,
        }


LANG_CODES = frozenset("en ko kr ja jp zh cn tw de fr es pt ru vi".split())
REGION_CODES = frozenset("zh-cn zh-tw zh-hans zh-hant pt-br en-us en-gb".split())

_CAMEL_BOUNDARY = re.compile(r"(?<=[a-z0-9])(?=[A-Z])")
_NAME_SEPARATORS = re.compile(r"[\s_.]+")
_REPEATED_DASH = re.compile(r"-{2,}")
_DATE_CORE = r"20\d\d-?(?:0[1-9]|1[0-2])(?:-?(?:0[1-9]|[12]\d|3[01]))?"
_TRAILING_DATE = re.compile(rf"-?{_DATE_CORE}$")
_LEADING_DATE = re.compile(rf"^{_DATE_CORE}-")
_LEADING_ORDINAL = re.compile(r"^\d{1,3}-")
_TEXT_EMPHASIS = re.compile(r"\*\*|__|~~|`")
_WHITESPACE = re.compile(r"\s+")


def nfc(text: str) -> str:
    return unicodedata.normalize("NFC", text)


def strip_doc_extension(name: str) -> str:
    lowered = name.lower()
    for extension in DOC_EXTENSIONS:
        if lowered.endswith(extension):
            return name[: -len(extension)]
    return name


def is_doc_path(path: str) -> bool:
    return path.lower().endswith(DOC_EXTENSIONS)


def _strip_lang_suffix(key: str) -> str:
    for code in REGION_CODES:
        if key.endswith("-" + code) and len(key) > len(code) + 1:
            return key[: -(len(code) + 1)]
    head, separator, tail = key.rpartition("-")
    if separator and head and tail in LANG_CODES:
        return head
    return key


def name_key(name: str) -> str:
    """Normalise a file name for rule matching.

    ``IMPLEMENTATION_SUMMARY-en.md`` -> ``implementation-summary``;
    ``ghost-alarm-incident-2026-04-23-en.md`` -> ``ghost-alarm-incident``;
    ``03_status_effects.md`` -> ``status-effects``.
    """
    stem = nfc(strip_doc_extension(posixpath.basename(name)))
    key = _CAMEL_BOUNDARY.sub("-", stem).casefold()
    key = _REPEATED_DASH.sub("-", _NAME_SEPARATORS.sub("-", key)).strip("-")
    key = _strip_lang_suffix(key)
    key = _TRAILING_DATE.sub("", key).strip("-")
    key = _strip_lang_suffix(key)
    key = _LEADING_DATE.sub("", key)
    key = _LEADING_ORDINAL.sub("", key)
    return key.strip("-")


def norm_text(text: str) -> str:
    return _WHITESPACE.sub(" ", _TEXT_EMPHASIS.sub("", nfc(text))).strip()


def normalize_subject(scope: str, value: str) -> str:
    if scope == "name":
        return name_key(value)
    if scope in ("title", "head", "line"):
        return norm_text(value)
    if scope in ("dir", "path"):
        return nfc(value).casefold()
    return value


def read_rules_file(path: Path, problems: list[str]) -> list[tuple[int, dict[str, str]]]:
    """Read a TSV whose first non-comment line is the header ``RULE_COLUMNS``."""
    rows: list[tuple[int, dict[str, str]]] = []
    header_seen = False
    for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        cells = line.split("\t")
        if not header_seen:
            if tuple(cell.strip() for cell in cells) != RULE_COLUMNS:
                problems.append(f"{path.name}:{lineno}: header must be {'<TAB>'.join(RULE_COLUMNS)}")
                return rows
            header_seen = True
            continue
        if len(cells) != len(RULE_COLUMNS):
            problems.append(f"{path.name}:{lineno}: expected {len(RULE_COLUMNS)} columns, got {len(cells)}")
            continue
        rows.append((lineno, dict(zip(RULE_COLUMNS, (cell.strip() for cell in cells)))))
    if not header_seen:
        problems.append(f"{path.name}: missing header row")
    return rows


def parse_rules_file(path: Path, problems: list[str]) -> dict[str, Rule]:
    rules: dict[str, Rule] = {}
    for lineno, row in read_rules_file(path, problems):
        where = f"{path.name}:{lineno}"
        rule_id = row["id"]
        if not rule_id:
            problems.append(f"{where}: empty id")
            continue
        if rule_id in rules:
            problems.append(f"{where}: duplicate id {rule_id!r}")
            continue
        kind, scope, strength = row["kind"], row["scope"], row["strength"]
        if kind not in KIND_SCOPES:
            problems.append(f"{where}: {rule_id}: kind {kind!r} not in {tuple(KIND_SCOPES)}")
            continue
        if scope not in KIND_SCOPES[kind]:
            allowed = tuple(sorted(KIND_SCOPES[kind]))
            problems.append(f"{where}: {rule_id}: scope {scope!r} is not allowed for kind {kind!r} {allowed}")
            continue
        if strength not in STRENGTHS:
            problems.append(f"{where}: {rule_id}: strength {strength!r} not in {STRENGTHS}")
            continue
        if UNPORTABLE_SYNTAX.search(row["regex"]):
            problems.append(f"{where}: {rule_id}: regex uses syntax Python 3.10 rejects, or a global inline flag")
            continue
        try:
            regex = re.compile(row["regex"], re.IGNORECASE)
        except re.error as exc:
            problems.append(f"{where}: {rule_id}: regex does not compile: {exc}")
            continue
        example = row["example"]
        if not example:
            problems.append(f"{where}: {rule_id}: no example")
            continue
        if not regex.search(normalize_subject(scope, example)):
            problems.append(f"{where}: {rule_id}: example {example!r} does not match its regex")
            continue
        counters = tuple(c.strip() for c in row["counter"].split(COUNTER_SEPARATOR) if c.strip())
        if (kind, scope) in COUNTER_REQUIRED and not counters:
            problems.append(f"{where}: {rule_id}: a {kind} name rule needs counter examples")
            continue
        matched = next((c for c in counters if regex.search(normalize_subject(scope, c))), None)
        if matched is not None:
            problems.append(f"{where}: {rule_id}: counter {matched!r} matches the regex")
            continue
        rules[rule_id] = Rule(rule_id, kind, scope, strength, row["regex"], regex, example, counters,
                              row["note"], path.name)
    return rules


def cross_check(rules: Iterable[Rule]) -> list[str]:
    """Rules that would contradict each other on their own examples."""
    rules = [rule for rule in rules if rule.strength != "off"]
    strong_byproduct_names = [r for r in rules if r.kind == "byproduct" and r.scope == "name" and r.strength == "strong"]
    backup_dirs = [r for r in rules if r.kind == "backup-dir"]
    problems: list[str] = []
    for rule in rules:
        if rule.kind == "root-allow":
            key = name_key(rule.example)
            for other in strong_byproduct_names:
                if other.regex.search(key):
                    problems.append(f"{rule.source}: {rule.id}: example {rule.example!r} is also a strong byproduct ({other.id})")
        elif rule.kind == "archive-dir":
            key = normalize_subject("dir", rule.example)
            for other in backup_dirs:
                if other.regex.search(key):
                    problems.append(f"{rule.source}: {rule.id}: example {rule.example!r} is also a backup directory ({other.id})")
    return problems


def load_rules(directory: Path) -> tuple[RuleSet, list[str]]:
    """Load ``rules.tsv`` plus the optional ``rules.local.tsv`` overlay.

    An overlay row whose id already exists replaces the base row in place; any
    other overlay row is appended. Strength ``off`` keeps a row but disables it.
    """
    base = directory / "rules.tsv"
    if not base.is_file():
        raise RulesError(f"rules file not found: {base}")
    problems: list[str] = []
    rules = parse_rules_file(base, problems)
    ruleset = RuleSet(directory=directory, rules=rules)
    local = directory / "rules.local.tsv"
    if local.is_file():
        overlay = parse_rules_file(local, problems)
        ruleset.local = True
        ruleset.overridden = sorted(rule_id for rule_id in overlay if rule_id in rules)
        rules.update(overlay)
    problems.extend(cross_check(rules.values()))
    return ruleset, problems


# ---------------------------------------------------------------------------
# Markdown
# ---------------------------------------------------------------------------

FENCE_OPEN = re.compile(r"^ {0,3}(`{3,}|~{3,})(.*)$")
ATX_HEADING = re.compile(r"^ {0,3}(#{1,6})(?:[ \t]+(.*?))?(?:[ \t]+#+)?[ \t]*$")
SETEXT_H1 = re.compile(r"^ {0,3}=+[ \t]*$")
CODE_SPAN = re.compile(r"(`+)(?!`)(.+?)(?<!`)\1(?!`)")
HTML_COMMENT = re.compile(r"<!--.*?-->")
KEEP_MARKER = re.compile(r"<!--\s*doc-tidy:\s*keep\s*-->", re.IGNORECASE)
FRONTMATTER_KEY = re.compile(r"^([A-Za-z0-9_-]+):")
REFDEF = re.compile(r"^ {0,3}\[[^\]]+\]:[ \t]*(<[^>]*>|\S+)")
HTML_LINK = re.compile(r"<(?:a|img|source)\b[^>]*?\s(?:href|src)\s*=\s*(?:\"([^\"]*)\"|'([^']*)')", re.IGNORECASE)
MENTION = re.compile(r"^(?:\.{1,2}/)*[^\s*?<>{}$|`]+\.(?:md|markdown|mdx)(?:#\S*)?$", re.IGNORECASE)
HANGUL_CHAR = re.compile(r"[가-힣]")
HEAD_LINES = 60
KEEP_WINDOW = 20
LANGUAGE_SAMPLE = 20000


@dataclass(frozen=True)
class Link:
    line: int
    col: int
    end: int
    raw: str
    form: str
    bracketed: bool = False


@dataclass
class Parsed:
    title: dict[str, Any] | None
    frontmatter: list[str] | None
    sections: list[dict[str, Any]]
    links: list[Link]
    head: list[tuple[int, str]]
    prose: list[tuple[int, str]]
    table_rows: list[tuple[int, str]]
    keep: bool
    lines: int
    hangul_ratio: float
    body: str


def split_lines(text: str) -> list[str]:
    """Lines as an editor numbers them: only ``\\n`` ends a line."""
    lines = text.split("\n")
    if lines and lines[-1] == "":
        lines.pop()
    return [line[:-1] if line.endswith("\r") else line for line in lines]


def unquote_scalar(value: str) -> str:
    value = value.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in "'\"":
        inner = value[1:-1]
        return inner.replace("''", "'") if value[0] == "'" else inner
    return value


def _open_bracket(line: str, close: int) -> int | None:
    """Index of the ``[`` matching the ``]`` at ``close``, or None."""
    depth = 0
    index = close
    while index >= 0:
        char = line[index]
        escaped = index > 0 and line[index - 1] == "\\"
        if char == "]" and not escaped:
            depth += 1
            if depth > 3:
                return None
        elif char == "[" and not escaped:
            depth -= 1
            if depth == 0:
                return index
        index -= 1
    return None


def _destination(line: str, start: int) -> tuple[int, int, str, bool, int] | None:
    """Parse ``dest [title])`` from ``start``: (col, end, raw, bracketed, close index)."""
    size = len(line)
    cursor = start
    while cursor < size and line[cursor] in " \t":
        cursor += 1
    if cursor >= size:
        return None
    if line[cursor] == "<":
        closing = line.find(">", cursor + 1)
        if closing < 0:
            return None
        col, end, bracketed = cursor + 1, closing, True
        after = closing + 1
    else:
        depth = 0
        after = cursor
        while after < size:
            char = line[after]
            if char == "\\" and after + 1 < size:
                after += 2
                continue
            if char in " \t":
                break
            if char == "(":
                depth += 1
            elif char == ")":
                if depth == 0:
                    break
                depth -= 1
            after += 1
        if depth:
            return None
        col, end, bracketed = cursor, after, False
    raw = line[col:end]
    while after < size and line[after] in " \t":
        after += 1
    if after < size and line[after] == ")":
        return col, end, raw, bracketed, after
    if after < size and line[after] in "\"'(":
        closer = ")" if line[after] == "(" else line[after]
        title_end = line.find(closer, after + 1)
        if title_end < 0:
            return None
        title_end += 1
        while title_end < size and line[title_end] in " \t":
            title_end += 1
        if title_end < size and line[title_end] == ")":
            return col, end, raw, bracketed, title_end
    return None


def inline_links(masked: str, lineno: int) -> list[Link]:
    links: list[Link] = []
    cursor = 0
    while True:
        close = masked.find("](", cursor)
        if close < 0:
            return links
        opener = _open_bracket(masked, close)
        parsed = _destination(masked, close + 2) if opener is not None else None
        if parsed is None:
            cursor = close + 2
            continue
        col, end, raw, bracketed, after = parsed
        form = "image" if opener and masked[opener - 1] == "!" else "inline"
        if raw:
            links.append(Link(lineno, col, end, raw, form, bracketed))
        cursor = after + 1


def line_links(line: str, lineno: int) -> list[Link]:
    spans = [(match.start(2), match.group(2)) for match in CODE_SPAN.finditer(line)]
    masked = CODE_SPAN.sub(lambda m: " " * len(m.group(0)), line) if spans else line
    links = inline_links(masked, lineno)
    refdef = REFDEF.match(masked)
    if refdef:
        dest = refdef.group(1)
        if dest.startswith("<") and dest.endswith(">"):
            links.append(Link(lineno, refdef.start(1) + 1, refdef.end(1) - 1, dest[1:-1], "refdef", True))
        else:
            links.append(Link(lineno, refdef.start(1), refdef.end(1), dest, "refdef"))
    for match in HTML_LINK.finditer(masked):
        group = 1 if match.group(1) is not None else 2
        links.append(Link(lineno, match.start(group), match.end(group), match.group(group), "html"))
    for col, content in spans:
        stripped = content.strip()
        if stripped and MENTION.match(stripped):
            offset = col + len(content) - len(content.lstrip())
            links.append(Link(lineno, offset, offset + len(stripped), stripped, "mention"))
    return links


def parse_markdown(text: str) -> Parsed:
    lines = split_lines(text)
    total = len(lines)
    keep = any(KEEP_MARKER.search(line) for line in lines[:KEEP_WINDOW])
    frontmatter: list[str] | None = None
    frontmatter_title: str | None = None
    start = 0
    if lines and lines[0].strip() == "---":
        for index in range(1, total):
            if lines[index].strip() in ("---", "..."):
                frontmatter = []
                for entry in lines[1:index]:
                    key = FRONTMATTER_KEY.match(entry)
                    if key:
                        frontmatter.append(key.group(1))
                        if key.group(1) == "title" and frontmatter_title is None:
                            frontmatter_title = unquote_scalar(entry.split(":", 1)[1])
                start = index + 1
                break

    title: dict[str, Any] | None = None
    sections: list[dict[str, Any]] = []
    links: list[Link] = []
    head: list[tuple[int, str]] = []
    prose: list[tuple[int, str]] = []
    table_rows: list[tuple[int, str]] = []
    fence: tuple[str, int] | None = None
    in_comment = False
    paragraph: tuple[int, str] | None = None

    for index in range(start, total):
        lineno = index + 1
        raw = lines[index]
        if fence is not None:
            closing = FENCE_OPEN.match(raw)
            if closing and closing.group(1)[0] == fence[0] and len(closing.group(1)) >= fence[1] \
                    and not closing.group(2).strip():
                fence = None
            continue
        if not in_comment:
            opening = FENCE_OPEN.match(raw)
            # A backtick fence's info string may not itself contain a backtick.
            if opening and (opening.group(1)[0] == "~" or "`" not in opening.group(2)):
                fence = (opening.group(1)[0], len(opening.group(1)))
                paragraph = None
                continue
        line = raw
        if in_comment:
            closing_at = line.find("-->")
            if closing_at < 0:
                continue
            line = " " * (closing_at + 3) + line[closing_at + 3:]
            in_comment = False
        line = HTML_COMMENT.sub(lambda m: " " * len(m.group(0)), line)
        opening_at = line.find("<!--")
        if opening_at >= 0:
            line = line[:opening_at] + " " * (len(line) - opening_at)
            in_comment = True
        stripped = line.strip()
        if not stripped:
            paragraph = None
            continue
        prose.append((lineno, line))
        if len(head) < HEAD_LINES:
            head.append((lineno, line))
        heading = ATX_HEADING.match(line)
        if heading:
            level = len(heading.group(1))
            heading_text = norm_text(heading.group(2) or "")
            if level == 1 and title is None and heading_text:
                title = {"text": heading_text, "line": lineno, "from": "atx"}
            elif level == 2 and heading_text:
                sections.append({"line": lineno, "text": heading_text})
            paragraph = None
        elif SETEXT_H1.match(line) and paragraph is not None:
            if title is None:
                title = {"text": norm_text(paragraph[1]), "line": paragraph[0], "from": "setext"}
            paragraph = None
        else:
            paragraph = (lineno, stripped)
        if stripped.startswith("|"):
            table_rows.append((lineno, stripped))
        links.extend(line_links(line, lineno))

    if title is None and frontmatter_title:
        title = {"text": norm_text(frontmatter_title), "line": 1, "from": "frontmatter"}
    sample = text[:LANGUAGE_SAMPLE]
    letters = sum(1 for char in sample if char.isalpha())
    hangul = len(HANGUL_CHAR.findall(sample))
    return Parsed(
        title=title,
        frontmatter=frontmatter,
        sections=sections,
        links=links,
        head=head,
        prose=prose,
        table_rows=table_rows,
        keep=keep,
        lines=total,
        hangul_ratio=round(hangul / letters, 3) if letters else 0.0,
        body="\n".join(lines[start:]),
    )


# ---------------------------------------------------------------------------
# Discovery
# ---------------------------------------------------------------------------

SKIP_DIRS = frozenset({
    ".git", "node_modules", "vendor", "venv", ".venv", "__pycache__", "dist", "build", "target", ".next",
    ".tox", "site-packages", ".terraform", "bower_components", "third_party", "Pods", ".cache",
    ".pytest_cache", ".mypy_cache",
})
SKIP_DIR_SUFFIXES = (".dist-info", ".egg-info")
# Trees recognisable only by more than one segment: a Claude Code config
# directory's plugin cache holds every installed plugin at every version.
SKIP_PATHS = ("plugins/cache", ".claude/worktrees", "skills/synced")
# Upstream module docs vendored by version: modules/<name>/v5.3.1/README.md.
VENDORED_SEGMENT = re.compile(r"^v\d+(?:\.\d+){1,2}(?:[-+][0-9A-Za-z.-]+)?$")

DOCS_DIR_NAMES = frozenset({"docs", "doc", "documentation", "wiki", "guides", "guide"})
FIXTURE_SEGMENTS = frozenset({"fixtures", "__fixtures__", "testdata", "__snapshots__", "snapshots", "golden", "goldens"})
AGENT_FILE_NAMES = frozenset({"claude.md", "claude.local.md", "agents.md", "gemini.md"})
AGENT_DIR_SEGMENTS = frozenset({".claude", ".agents", ".cursor", ".windsurf", ".clinerules"})
PLUGIN_SUBTREE = re.compile(r"^(?:agents|commands|skills|hooks|output-styles)(?:-[a-z]{2,3})?$")
CONFIG_MARKERS = frozenset({"agents", "commands", "skills", "plans", "memory", "CLAUDE.md", "settings.json"})
CONFIG_ANCHORS = frozenset({"agents", "plans", "memory"})
CONFIG_SUBTREE = re.compile(r"^(?:agents|commands|skills|plans|memory|output-styles)(?:-[a-z]{2,3})?$")
DEFINITION_DIRS = frozenset({"agents", "commands", "skills"})
PLATFORM_PREFIXES = (
    ".github/issue_template/", ".github/discussion_template/", ".github/pull_request_template",
    ".gitlab/issue_templates/", ".gitlab/merge_request_templates/",
)
ENTRY_KEYS = frozenset({"readme", "index", "home", "sidebar", "footer"})
INDEX_NAMES = ("README.md", "readme.md", "Readme.md", "index.md", "_index.md", "README.markdown")
SITE_MARKERS = (
    ("_config.yml", "jekyll"), ("hugo.toml", "hugo"), ("hugo.yaml", "hugo"), ("mkdocs.yml", "mkdocs"),
    ("mkdocs.yaml", "mkdocs"), ("docusaurus.config.js", "docusaurus"), ("docusaurus.config.ts", "docusaurus"),
    ("book.toml", "mdbook"), (".vitepress", "vitepress"),
)
SITE_EXEMPT_KEYS = frozenset({"permalink", "layout", "slug"})


@dataclass
class Doc:
    path: str
    text: str
    parsed: Parsed
    bytes: int
    mtime: float
    tracked: bool | None
    dirty: bool
    cls: str = "guide"
    hits: list[dict[str, Any]] = field(default_factory=list)
    pair: dict[str, Any] | None = None
    lang_prefix: str | None = None
    dated: dict[str, Any] | None = None
    archive: dict[str, Any] | None = None
    migration: str | None = None
    git: dict[str, Any] | None = None
    drift: dict[str, Any] | None = None
    resolved: list[tuple[Link, "Target"]] = field(default_factory=list)
    inbound: list[dict[str, Any]] = field(default_factory=list)
    broken: list[dict[str, Any]] = field(default_factory=list)
    out_links: int = 0
    reach: str = "orphan"

    @property
    def keep(self) -> bool:
        return self.parsed.keep

    @property
    def delete_risk(self) -> str:
        if self.tracked is None:
            return "no-git"
        if not self.tracked:
            return "untracked"
        return "uncommitted" if self.dirty else "none"

    @property
    def triageable(self) -> bool:
        return self.cls in ("guide", "byproduct", "historical")


@dataclass
class Repo:
    root: Path
    git: bool
    kind: str = "plain"
    site: str | None = None
    fork: bool = False
    shallow: bool = False
    head: str | None = None
    nested_repos: int = 0
    files: set[str] = field(default_factory=set)
    dirs: set[str] = field(default_factory=set)
    children: dict[str, set[str]] = field(default_factory=dict)
    files_casefold: dict[str, str] = field(default_factory=dict)
    by_basename: dict[str, list[str]] = field(default_factory=dict)
    docs: dict[str, Doc] = field(default_factory=dict)
    docs_by_basename: dict[str, list[str]] = field(default_factory=dict)
    tracked: set[str] = field(default_factory=set)
    untracked: set[str] = field(default_factory=set)
    dirty: set[str] = field(default_factory=set)
    plugin_roots: set[str] = field(default_factory=set)
    config_trees: set[str] = field(default_factory=set)
    excluded: dict[str, int] = field(default_factory=lambda: {"skipDirs": 0, "vendoredSemver": 0, "exclude": 0, "symlinks": 0})
    warnings: list[str] = field(default_factory=list)
    mirrors: dict[str, tuple[str, str, str]] = field(default_factory=dict)
    pairs: dict[str, dict[str, str]] = field(default_factory=dict)
    pair_info: dict[str, Any] = field(default_factory=dict)
    archive: dict[str, Any] = field(default_factory=dict)
    migrations: dict[str, dict[str, Any]] = field(default_factory=dict)
    history: dict[str, Any] = field(default_factory=lambda: {"available": False, "commits": 0, "bulkCommits": 0, "reason": "not requested"})

    def dir_index(self, directory: str) -> str | None:
        for name in INDEX_NAMES:
            candidate = f"{directory}/{name}" if directory else name
            if candidate in self.docs:
                return candidate
        return None

    def exists_on_disk(self, rel: str) -> bool:
        full = self.root / rel
        if not os.path.lexists(full):
            return False
        try:
            names = {nfc(name) for name in os.listdir(full.parent)}
        except OSError:
            return False
        return nfc(full.name) in names

    def unit_of(self, rel: str) -> str:
        mirror = self.mirrors.get(rel)
        return mirror[0] if mirror else rel

    def unit_paths(self, rel: str) -> list[str]:
        base = self.unit_of(rel)
        return [base, *sorted(self.pairs.get(base, {}).values())]


def run_git(root: Path, *args: str, timeout: int = GIT_TIMEOUT, stdin: bytes | None = None) -> bytes | None:
    try:
        completed = subprocess.run(
            ["git", "-C", str(root), *args],
            input=stdin, capture_output=True, timeout=timeout, check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    return completed.stdout if completed.returncode == 0 else None


def split_z(data: bytes | None) -> list[str]:
    if not data:
        return []
    return [nfc(item) for item in data.decode("utf-8", "surrogateescape").split("\0") if item]


def git_toplevel(path: Path) -> Path | None:
    output = run_git(path, "rev-parse", "--show-toplevel", timeout=10)
    if not output:
        return None
    return Path(output.decode("utf-8", "surrogateescape").strip()).resolve()


def parse_status(data: bytes | None) -> set[str]:
    """Paths with uncommitted changes, from ``git status --porcelain=v1 -z``."""
    dirty: set[str] = set()
    if not data:
        return dirty
    items = data.decode("utf-8", "surrogateescape").split("\0")
    index = 0
    while index < len(items):
        entry = items[index]
        index += 1
        if len(entry) < 4:
            continue
        code = entry[:2]
        dirty.add(nfc(entry[3:]))
        if "R" in code or "C" in code:
            index += 1  # a rename or copy carries its original path as the next item
    return dirty


def is_fork(root: Path) -> bool:
    """A clone with an ``upstream`` remote is somebody else's project."""
    git_path = root / ".git"
    if git_path.is_dir():
        try:
            config = (git_path / "config").read_text(encoding="utf-8", errors="replace")
        except OSError:
            return False
        return re.search(r'^\s*\[remote\s+"upstream"\]', config, re.MULTILINE) is not None
    output = run_git(root, "config", "--get", "remote.upstream.url", timeout=10)
    return bool(output and output.strip())


def exclusion_reason(rel: str, excludes: list[str]) -> str | None:
    parts = rel.split("/")
    directories = parts[:-1]
    for part in directories:
        if part in SKIP_DIRS or part.endswith(SKIP_DIR_SUFFIXES):
            return "skipDirs"
    padded = "/" + rel
    if any(f"/{skip}/" in padded for skip in SKIP_PATHS):
        return "skipDirs"
    if any(VENDORED_SEGMENT.match(part) for part in directories):
        return "vendoredSemver"
    if any(fnmatch.fnmatchcase(rel, pattern) for pattern in excludes):
        return "exclude"
    return None


def walk_files(repo: Repo) -> set[str]:
    """Every file under a directory that is not a git repository."""
    found: set[str] = set()
    root = str(repo.root)
    for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
        kept = []
        for name in dirnames:
            if name in SKIP_DIRS or name.endswith(SKIP_DIR_SUFFIXES):
                continue
            if os.path.exists(os.path.join(dirpath, name, ".git")):
                repo.nested_repos += 1
                continue
            kept.append(name)
        dirnames[:] = sorted(kept)
        relative = os.path.relpath(dirpath, root)
        for name in filenames:
            rel = name if relative == "." else f"{relative}/{name}"
            found.add(nfc(rel.replace(os.sep, "/")))
    return found


def index_paths(repo: Repo, paths: Iterable[str]) -> None:
    for rel in paths:
        repo.files.add(rel)
        repo.files_casefold.setdefault(rel.casefold(), rel)
        repo.by_basename.setdefault(posixpath.basename(rel), []).append(rel)
        parts = rel.split("/")
        for depth in range(len(parts)):
            parent = "/".join(parts[:depth])
            if depth:
                repo.dirs.add(parent)
            repo.children.setdefault(parent, set()).add(parts[depth])
            if parts[depth] == ".claude-plugin" and depth < len(parts) - 1:
                repo.plugin_roots.add(parent)


def detect_kind(repo: Repo) -> None:
    names = repo.children.get("", set())
    if repo.root.name.endswith(".wiki") or ("Home.md" in names and names & {"_Sidebar.md", "_Footer.md"}):
        repo.kind = "wiki"
        return
    for marker, site in SITE_MARKERS:
        if marker in names:
            repo.kind, repo.site = "site", site
            return


def discover(path: Path, excludes: list[str]) -> Repo:
    path = path.resolve()
    if not path.is_dir():
        raise UsageError(f"not a directory: {path}")
    top = git_toplevel(path)
    repo = Repo(root=top or path, git=top is not None)
    listed: set[str] | None = None
    if repo.git:
        cached = run_git(repo.root, "ls-files", "-z", "--cached")
        others = run_git(repo.root, "ls-files", "-z", "--others", "--exclude-standard")
        if cached is None or others is None:
            repo.warnings.append("git ls-files failed; walked the directory instead")
            repo.git = False
        else:
            repo.tracked = set(split_z(cached))
            repo.untracked = set(split_z(others)) - repo.tracked
            listed = repo.tracked | repo.untracked
            repo.dirty = parse_status(run_git(
                repo.root, "status", "--porcelain=v1", "-z", "--untracked-files=no", "--ignore-submodules=all"))
            repo.fork = is_fork(repo.root)
            shallow = run_git(repo.root, "rev-parse", "--is-shallow-repository", timeout=10)
            repo.shallow = bool(shallow and shallow.strip() == b"true")
            head = run_git(repo.root, "rev-parse", "--short", "HEAD", timeout=10)
            repo.head = head.decode().strip() if head else None
    if listed is None:
        listed = walk_files(repo)
    index_paths(repo, listed)
    for directory, kids in repo.children.items():
        markers = kids & CONFIG_MARKERS
        if len(markers) >= 3 and kids & CONFIG_ANCHORS:
            repo.config_trees.add(directory)
    detect_kind(repo)

    for rel in sorted(listed):
        if not is_doc_path(rel):
            continue
        reason = exclusion_reason(rel, excludes)
        if reason:
            repo.excluded[reason] += 1
            continue
        full = repo.root / rel
        if full.is_symlink():
            repo.excluded["symlinks"] += 1
            continue
        try:
            stat = full.stat()
            text = full.read_text(encoding="utf-8", errors="replace")
        except OSError:
            repo.files.discard(rel)  # tracked, but deleted from the working tree
            continue
        repo.docs[rel] = Doc(
            path=rel, text=text, parsed=parse_markdown(text), bytes=stat.st_size, mtime=stat.st_mtime,
            tracked=(rel in repo.tracked) if repo.git else None, dirty=rel in repo.dirty,
        )
        repo.docs_by_basename.setdefault(posixpath.basename(rel), []).append(rel)
    return repo


# ---------------------------------------------------------------------------
# Classes, pairs, archive, migration folders
# ---------------------------------------------------------------------------

NAME_DATE = re.compile(r"(?:^|[-_.\s])(20\d\d)[-_.]?(0[1-9]|1[0-2])(?:[-_.]?(0[1-9]|[12]\d|3[01]))?(?=$|[-_.\s])")
TITLE_DATE = re.compile(r"(?<!\d)(20\d\d)-(0[1-9]|1[0-2])(?:-(0[1-9]|[12]\d|3[01]))?(?!\d)")
LANG_PREFIX = re.compile(r"^(EN|KR|KO|JA|JP|ZH|CN)_")
LANG_DIR_NAMES = frozenset({"en", "ko", "kr", "ja", "jp", "zh", "cn"})
PAIR_SEPARATORS = ("-", "_", ".")
MIGRATION_DIR_NAME = re.compile(r"-(?:migration|cutover|rollout|upgrade)$", re.IGNORECASE)
PHASE_FILE = re.compile(r"^(?:\d{1,3}-|phase)")
CONVENTIONAL_DATED_RATIO = 0.6
CONVENTIONAL_DATED_MIN = 3


def dir_key(segment: str) -> str:
    return nfc(segment).casefold()


def is_agent_config(repo: Repo, doc: Doc) -> bool:
    rel = doc.path
    parts = rel.split("/")
    name = parts[-1]
    if name.casefold() in AGENT_FILE_NAMES or name == "SKILL.md":
        return True
    if any(part in AGENT_DIR_SEGMENTS for part in parts[:-1]):
        return True
    lowered = rel.casefold()
    if lowered.startswith(".github/instructions/") or lowered == ".github/copilot-instructions.md":
        return True
    for root, subtree in ((r, PLUGIN_SUBTREE) for r in repo.plugin_roots):
        rest = _relative_parts(rel, root)
        if rest and len(rest) > 1 and subtree.match(rest[0]):
            return True
    for root in repo.config_trees:
        rest = _relative_parts(rel, root)
        if rest and len(rest) > 1 and (CONFIG_SUBTREE.match(rest[0]) or (rest[0] == "projects" and "memory" in rest[1:-1])):
            return True
    frontmatter = doc.parsed.frontmatter or []
    if "name" in frontmatter and "description" in frontmatter:
        return any(part in DEFINITION_DIRS or PLUGIN_SUBTREE.match(part) for part in parts[:-1])
    return False


def _relative_parts(rel: str, root: str) -> list[str] | None:
    if not root:
        return rel.split("/")
    if rel.startswith(root + "/"):
        return rel[len(root) + 1:].split("/")
    return None


def archive_root_of(repo: Repo, rel: str) -> str | None:
    parts = rel.split("/")
    for depth in range(1, len(parts)):
        candidate = "/".join(parts[:depth])
        if candidate in repo.archive.get("_roots", {}):
            return candidate
    return None


def structural_class(repo: Repo, ruleset: RuleSet, doc: Doc) -> str | None:
    parts = doc.path.split("/")
    if any(part in FIXTURE_SEGMENTS for part in parts[:-1]):
        return "fixture"
    if is_agent_config(repo, doc):
        return "agent-config"
    if doc.path.casefold().startswith(PLATFORM_PREFIXES):
        return "platform"
    if archive_root_of(repo, doc.path):
        return "archived"
    key = name_key(parts[-1])
    if any(rule.regex.search(key) for rule in ruleset.active("generated", "name")):
        return "generated"
    dir_rules = ruleset.active("generated", "dir")
    if any(rule.regex.search(dir_key(part)) for part in parts[:-1] for rule in dir_rules):
        return "generated"
    if key in ENTRY_KEYS:
        return "entry"
    if len(parts) == 1 and any(rule.regex.search(key) for rule in ruleset.active("root-allow")):
        return "entry"
    return None


def rule_hits(ruleset: RuleSet, doc: Doc) -> list[dict[str, Any]]:
    hits: list[dict[str, Any]] = []
    key = name_key(doc.path)
    lowered_path = nfc(doc.path).casefold()
    title = doc.parsed.title
    head = [(lineno, norm_text(line)) for lineno, line in doc.parsed.head]
    for kind in ("byproduct", "historical"):
        for rule in ruleset.active(kind):
            line: int | None = None
            match: re.Match[str] | None = None
            if rule.scope == "name":
                match = rule.regex.search(key)
            elif rule.scope == "path":
                match = rule.regex.search(lowered_path)
            elif rule.scope == "title" and title:
                match = rule.regex.search(title["text"])
                line = title["line"]
            elif rule.scope == "head":
                for lineno, text in head:
                    match = rule.regex.search(text)
                    if match:
                        line = lineno
                        break
            if match:
                hit = {"id": rule.id, "kind": kind, "scope": rule.scope, "strength": rule.strength,
                       "match": match.group(0), "note": rule.note}
                if line is not None:
                    hit["line"] = line
                hits.append(hit)
    return hits


def split_lang_suffix(stem: str) -> tuple[str, str, str] | None:
    lowered = stem.casefold()
    for separator in PAIR_SEPARATORS:
        for code in sorted(REGION_CODES, key=len, reverse=True):
            suffix = separator + code
            if lowered.endswith(suffix) and len(stem) > len(suffix):
                return stem[: -len(suffix)], separator, code
        head, found, tail = stem.rpartition(separator)
        if found and head and 2 <= len(tail) <= 3 and tail.isascii() and tail.isalpha():
            return head, separator, tail.casefold()
    return None


def discover_pairs(repo: Repo) -> None:
    by_key: dict[tuple[str, str, str], str] = {}
    for rel in repo.docs:
        directory, name = posixpath.split(rel)
        stem, extension = posixpath.splitext(name)
        by_key[(directory, stem, extension.lower())] = rel

    candidates: list[tuple[str, str, str, str]] = []
    code_counts: Counter[str] = Counter()
    for (directory, stem, extension), rel in by_key.items():
        split = split_lang_suffix(stem)
        if not split:
            continue
        base_stem, separator, code = split
        base = by_key.get((directory, base_stem, extension))
        if base is None or base == rel:
            continue
        candidates.append((rel, base, code, separator))
        code_counts[code] += 1
    # A known language code is a language on sight. Any other short suffix has
    # to prove itself twice: `scaling-ha.md` beside `scaling.md` is not Hausa.
    accepted = LANG_CODES | REGION_CODES | {code for code, count in code_counts.items() if count >= 2}

    conventions: Counter[tuple[str, str]] = Counter()
    for rel, base, code, separator in candidates:
        if code not in accepted:
            continue
        repo.pairs.setdefault(base, {})[code] = rel
        repo.mirrors[rel] = (base, code, separator)
        conventions[(separator, code)] += 1
    for base in [b for b in repo.pairs if b in repo.mirrors]:
        for mirror in repo.pairs.pop(base).values():
            repo.mirrors.pop(mirror, None)

    for base, mirrors in repo.pairs.items():
        separators = {repo.mirrors[m][2] for m in mirrors.values() if m in repo.mirrors}
        repo.docs[base].pair = {"lang": "base", "sep": sorted(separators)[0] if separators else "-",
                                "role": "base", "partners": sorted(mirrors.values())}
    for mirror, (base, code, separator) in repo.mirrors.items():
        partners = [base, *sorted(m for m in repo.pairs.get(base, {}).values() if m != mirror)]
        repo.docs[mirror].pair = {"lang": code, "sep": separator, "role": "mirror", "partners": partners}

    orphan_mirrors = []
    for (directory, stem, extension), rel in by_key.items():
        split = split_lang_suffix(stem)
        if split and split[2] in accepted and rel not in repo.mirrors and rel not in repo.pairs \
                and by_key.get((directory, split[0], extension)) is None:
            orphan_mirrors.append(rel)

    summaries = []
    for (separator, code), count in sorted(conventions.items()):
        ratios = sorted(repo.docs[b].parsed.hangul_ratio for b, ms in repo.pairs.items() if ms.get(code) and repo.mirrors.get(ms[code], ("", "", ""))[2] == separator)
        median = ratios[len(ratios) // 2] if ratios else None
        base_lang = "unknown" if median is None else ("ko" if median > 0.3 else "other")
        summaries.append({"sep": separator, "lang": code, "pairs": count, "baseLang": base_lang})
    for doc in repo.docs.values():
        prefix = LANG_PREFIX.match(posixpath.basename(doc.path))
        doc.lang_prefix = prefix.group(1) if prefix else None
    lang_dirs = sorted({posixpath.dirname(rel) for rel in repo.docs
                        if posixpath.basename(posixpath.dirname(rel)).casefold() in LANG_DIR_NAMES})
    repo.pair_info = {
        "conventions": summaries,
        "halves": len(repo.mirrors),
        "langPrefixFiles": sum(1 for doc in repo.docs.values() if doc.lang_prefix),
        "langDirs": lang_dirs,
        "orphanMirrors": sorted(orphan_mirrors),
    }


def archive_convention(repo: Repo, ruleset: RuleSet) -> None:
    retired = ruleset.active("archive-dir")
    backup = ruleset.active("backup-dir")
    roots: dict[str, tuple[Rule, str]] = {}
    for directory in sorted(repo.dirs):
        parts = directory.split("/")
        if any("/".join(parts[:depth]) in roots for depth in range(1, len(parts))):
            continue
        segment = dir_key(parts[-1])
        rule = next((r for r in retired if r.regex.search(segment)), None)
        role = "retired"
        if rule is None:
            rule = next((r for r in backup if r.regex.search(segment)), None)
            role = "backup"
        if rule is not None:
            roots[directory] = (rule, role)

    dirs = []
    for directory, (rule, role) in roots.items():
        depth = directory.count("/") + 1
        first = directory.split("/")[0]
        movable = role == "retired" and rule.strength == "strong" and (
            depth == 1 or (depth == 2 and first in DOCS_DIR_NAMES))
        dirs.append({
            "path": directory, "role": role, "movable": movable,
            "docs": sum(1 for rel in repo.docs if rel.startswith(directory + "/")),
            "layout": _archive_layout(repo, directory, roots), "rule": rule.id,
        })
    movable = [entry for entry in dirs if entry["movable"]]
    preferred = movable[0] if len(movable) == 1 else None
    repo.archive = {
        "_roots": roots,
        "dirs": dirs,
        "preferred": preferred["path"] if preferred else None,
        "layout": preferred["layout"] if preferred else None,
        "needsAsk": preferred is None,
        "reason": None if preferred else ("none" if not movable else "ambiguous"),
        "docs": sum(entry["docs"] for entry in dirs),
    }


def _archive_layout(repo: Repo, archive: str, roots: dict[str, Any]) -> str:
    kids = repo.children.get(archive, set())
    if not kids:
        return "unknown"
    subdirs = {kid for kid in kids if f"{archive}/{kid}" in repo.dirs}
    if not subdirs:
        return "flat"
    parent = posixpath.dirname(archive)
    siblings = set()
    for kid in repo.children.get(parent, set()):
        candidate = f"{parent}/{kid}" if parent else kid
        if candidate in repo.dirs and candidate not in roots:
            siblings.add(kid)
    return "mirror" if len(subdirs & siblings) / len(subdirs) >= 0.5 else "flat"


def archive_target(archive: dict[str, Any], rel: str) -> str | None:
    preferred = archive.get("preferred")
    if not preferred:
        return None
    if archive.get("layout") == "flat":
        return f"{preferred}/{posixpath.basename(rel)}"
    parent = posixpath.dirname(preferred)
    tail = rel[len(parent) + 1:] if parent and rel.startswith(parent + "/") else rel
    return f"{preferred}/{tail}"


def light_key(rel: str) -> str:
    return _REPEATED_DASH.sub("-", _NAME_SEPARATORS.sub("-", strip_doc_extension(posixpath.basename(rel)).casefold()))


def detect_dates(repo: Repo, candidates: list[Doc]) -> None:
    by_dir: dict[str, list[Doc]] = {}
    for doc in candidates:
        by_dir.setdefault(posixpath.dirname(doc.path), []).append(doc)
        match = NAME_DATE.search(strip_doc_extension(posixpath.basename(doc.path)))
        if match:
            doc.dated = {"date": "-".join(g for g in match.groups() if g), "from": "name", "conventional": False}
        elif doc.parsed.title:
            title_match = TITLE_DATE.search(doc.parsed.title["text"])
            if title_match:
                doc.dated = {"date": title_match.group(0), "from": "title", "conventional": False}
    for directory, docs in by_dir.items():
        named = [doc for doc in docs if doc.dated and doc.dated["from"] == "name"]
        conventional = "_posts" in directory.split("/") or (
            len(docs) >= CONVENTIONAL_DATED_MIN and len(named) / len(docs) >= CONVENTIONAL_DATED_RATIO)
        if conventional:
            for doc in named:
                doc.dated["conventional"] = True


def detect_migrations(repo: Repo, ruleset: RuleSet, candidates: list[Doc]) -> None:
    by_dir: dict[str, list[Doc]] = {}
    for doc in candidates:
        by_dir.setdefault(posixpath.dirname(doc.path), []).append(doc)
    for directory, docs in by_dir.items():
        if not directory:
            continue
        keys = {doc.path: name_key(doc.path) for doc in docs}
        plan_docs = [doc for doc in docs if keys[doc.path] == "plan"]
        status_docs = [doc for doc in docs if keys[doc.path] == "status"]
        phases = sum(1 for doc in docs if PHASE_FILE.match(light_key(doc.path)))
        shaped = bool(plan_docs or status_docs) and (bool(plan_docs and status_docs) or phases >= 2)
        named = bool(MIGRATION_DIR_NAME.search(posixpath.basename(directory))) and len(docs) >= 2
        if not (shaped or named):
            continue
        # A status file speaks for the folder. Without one, progress is spread
        # over the plan and the phase reports, so every document is read.
        status_source = next((d for d in status_docs if d.path not in repo.mirrors), None)
        status = status_signals(ruleset, status_source.parsed if status_source else None,
                                [doc.parsed for doc in docs if doc.path not in repo.mirrors])
        repo.migrations[directory] = {
            "dir": directory,
            "files": sorted(doc.path for doc in docs),
            "statusFile": status_source.path if status_source else None,
            "status": status,
        }
        for doc in docs:
            doc.migration = directory


TABLE_MARKERS = (
    ("done", ("✅", "✔", "☑")),
    ("open", ("🔄", "⏳", "🚧", "🔶", "⬜", "☐")),
    ("dropped", ("❌", "🚫", "⛔")),
)
TABLE_SEPARATOR_ROW = re.compile(r"^\|?[\s:|-]+\|?$")
STATUS_EVIDENCE = 8


def status_signals(ruleset: RuleSet, parsed: Parsed | None, fallback: list[Parsed] | None = None) -> dict[str, Any]:
    """Raw completion signals. A heuristic label, never a verdict."""
    done = {"strong": 0, "weak": 0}
    opened = {"strong": 0, "weak": 0}
    table = {"done": 0, "open": 0, "dropped": 0}
    evidence: list[tuple[int, int, dict[str, Any]]] = []
    sources = [parsed] if parsed is not None else (fallback or [])
    done_rules = ruleset.active("status-done")
    open_rules = ruleset.active("status-open")
    for source in sources:
        for lineno, line in source.prose:
            text = norm_text(line)
            for rules, bucket, signal in ((done_rules, done, "done"), (open_rules, opened, "open")):
                rule = next((r for r in rules if r.regex.search(text)), None)
                if rule:
                    bucket[rule.strength] += 1
                    rank = 0 if rule.strength == "strong" else 1
                    evidence.append((rank, lineno, {"line": lineno, "signal": signal, "rule": rule.id, "text": text[:160]}))
        for lineno, row in source.table_rows:
            if TABLE_SEPARATOR_ROW.match(row):
                continue
            positions = [(row.find(mark), state) for state, marks in TABLE_MARKERS for mark in marks if mark in row]
            if not positions:
                continue
            state = min(positions)[1]
            table[state] += 1
            if state != "done":
                evidence.append((2, lineno, {"line": lineno, "signal": f"row-{state}", "rule": "table", "text": row[:160]}))
    rows = table["done"] + table["open"]
    if not rows:
        table_lean = "none"
    elif table["open"] == 0:
        table_lean = "done"
    elif table["open"] > table["done"]:
        table_lean = "open"
    else:
        table_lean = "mixed"
    finished = done["strong"] > 0 or table_lean == "done"
    unfinished = opened["strong"] > 0 or table_lean in ("open", "mixed")
    if finished and unfinished:
        lean = "mixed"
    elif finished:
        lean = "done"
    elif unfinished or opened["weak"] > 0:
        lean = "open"
    else:
        lean = "none"
    evidence.sort(key=lambda item: (item[0], item[1]))
    return {"lean": lean, "done": done, "open": opened, "table": table,
            "evidence": [item[2] for item in evidence[:STATUS_EVIDENCE]]}


def classify(repo: Repo, ruleset: RuleSet) -> None:
    archive_convention(repo, ruleset)
    discover_pairs(repo)
    candidates: list[Doc] = []
    for doc in repo.docs.values():
        structural = structural_class(repo, ruleset, doc)
        if structural:
            doc.cls = structural
            if structural == "archived":
                root = archive_root_of(repo, doc.path)
                _, role = repo.archive["_roots"][root]
                doc.archive = {"root": root, "role": role}
        else:
            candidates.append(doc)
    detect_dates(repo, candidates)
    detect_migrations(repo, ruleset, candidates)
    for doc in candidates:
        doc.hits = rule_hits(ruleset, doc)
        byproduct_strong = any(h["kind"] == "byproduct" and h["strength"] == "strong" for h in doc.hits)
        byproduct_weak = sum(1 for h in doc.hits if h["kind"] == "byproduct" and h["strength"] == "weak")
        if doc.tracked is False:
            byproduct_weak += 1
        historical_strong = any(h["kind"] == "historical" and h["strength"] == "strong" for h in doc.hits)
        historical_weak = sum(1 for h in doc.hits if h["kind"] == "historical" and h["strength"] == "weak")
        if doc.dated and doc.dated["from"] == "name" and not doc.dated["conventional"]:
            historical_weak += 1
        if doc.migration:
            historical_weak += 1
        if byproduct_strong or byproduct_weak >= 2:
            doc.cls = "byproduct"
        elif historical_strong or historical_weak >= 2:
            doc.cls = "historical"
        else:
            doc.cls = "guide"


# ---------------------------------------------------------------------------
# Link graph
# ---------------------------------------------------------------------------

SCHEME = re.compile(r"^[A-Za-z][A-Za-z0-9+.-]*:")
TEMPLATE_MARKERS = ("{{", "{%", "${", "%7B%7B", "%7b%7b")


@dataclass(frozen=True)
class Target:
    status: str                 # file | dir | ignored | broken | outside | skip
    path: str | None = None     # the repo-relative path that was looked up
    doc: str | None = None      # the document the link reaches, when it reaches one
    hint: str | None = None
    candidate: str | None = None
    style: str = "relative"     # relative | root | prefixed | absolute | home | basename


SKIP = Target("skip")


def split_destination(raw: str) -> tuple[str, str]:
    """``docs/a.md?x=1#part`` -> (``docs/a.md``, ``?x=1#part``)."""
    cut = len(raw)
    for marker in ("?", "#"):
        at = raw.find(marker)
        if 0 <= at < cut:
            cut = at
    return raw[:cut], raw[cut:]


def lookup(repo: Repo, joined: str, trailing: bool = False, style: str = "relative") -> Target:
    normalized = posixpath.normpath(joined) if joined else "."
    if normalized == ".":
        normalized = ""
    if normalized == ".." or normalized.startswith("../"):
        return Target("outside", normalized, style=style)
    if normalized in repo.files:
        if trailing:
            return Target("broken", normalized, hint="trailing-slash", style=style)
        return Target("file", normalized, normalized if normalized in repo.docs else None, style=style)
    if normalized == "" or normalized in repo.dirs:
        return Target("dir", normalized, repo.dir_index(normalized), style=style)
    if repo.exists_on_disk(normalized):
        return Target("ignored", normalized, style=style)
    folded = repo.files_casefold.get(normalized.casefold())
    if folded:
        return Target("broken", normalized, hint="case-mismatch", candidate=folded, style=style)
    matches = repo.by_basename.get(posixpath.basename(normalized)) or []
    if len(matches) == 1:
        return Target("broken", normalized, hint="moved-candidate", candidate=matches[0], style=style)
    return Target("broken", normalized, style=style)


def resolve_mention(repo: Repo, source: str, path_part: str) -> Target:
    if path_part.startswith(("~/", "/")):
        absolute = os.path.expanduser(path_part)
        root = str(repo.root) + "/"
        if not absolute.startswith(root):
            return SKIP
        rel = nfc(absolute[len(root):])
        style = "home" if path_part.startswith("~/") else "absolute"
        return _mention_target(repo, rel, style) or SKIP
    for joined, style in (
        (posixpath.join(posixpath.dirname(source), path_part), "relative"),
        (path_part, "root"),
        (path_part[len(repo.root.name) + 1:] if path_part.startswith(repo.root.name + "/") else None, "prefixed"),
    ):
        if joined is None:
            continue
        target = _mention_target(repo, posixpath.normpath(joined), style)
        if target:
            return target
    if "/" not in path_part:
        matches = repo.docs_by_basename.get(path_part) or []
        if len(matches) == 1:
            return Target("file", matches[0], matches[0], hint="basename", style="basename")
    return SKIP  # a mention that names nothing here is not a finding


def _mention_target(repo: Repo, rel: str, style: str) -> Target | None:
    if rel in repo.docs:
        return Target("file", rel, rel, style=style)
    if rel in repo.files:
        return Target("file", rel, None, style=style)
    return None


def resolve(repo: Repo, source: str, raw: str, form: str) -> Target:
    dest = raw.strip()
    if not dest or dest.startswith(("#", "//")) or SCHEME.match(dest):
        return SKIP
    if any(marker in dest for marker in TEMPLATE_MARKERS):
        return SKIP
    path_part, _ = split_destination(dest)
    if not path_part:
        return SKIP
    path_part = nfc(urllib.parse.unquote(path_part))
    if form == "mention":
        return resolve_mention(repo, source, path_part)
    has_extension = bool(posixpath.splitext(path_part.rstrip("/"))[1])
    if repo.kind == "site" and (path_part.startswith("/") or not has_extension):
        return SKIP  # routed by the site generator's permalinks
    if repo.kind == "wiki" and not has_extension:
        return SKIP  # wiki page names are a flat namespace; not resolved in v1
    if path_part.startswith(("/Users/", "/home/")):
        return Target("broken", path_part, hint="absolute-fs-path", style="absolute")
    trailing = path_part.endswith("/") and len(path_part) > 1
    if path_part.startswith("/"):
        return lookup(repo, path_part.lstrip("/"), trailing, style="root")
    return lookup(repo, posixpath.join(posixpath.dirname(source), path_part), trailing)


def build_graph(repo: Repo) -> None:
    for doc in repo.docs.values():
        if doc.cls == "fixture":
            continue
        source_kind = {"agent-config": "agent", "archived": "archive"}.get(doc.cls, "human")
        partners = set(doc.pair["partners"]) if doc.pair else set()
        for link in doc.parsed.links:
            target = resolve(repo, doc.path, link.raw, link.form)
            if target.status in ("skip", "outside"):
                continue
            doc.resolved.append((link, target))
            if link.form != "mention":
                doc.out_links += 1
            if target.status == "broken":
                if link.form != "mention":
                    entry = {"line": link.line, "col": link.col, "raw": link.raw, "resolved": target.path,
                             "form": link.form, "hint": target.hint}
                    if target.candidate:
                        entry["candidate"] = target.candidate
                    doc.broken.append(entry)
                continue
            if not target.doc or target.doc == doc.path:
                continue
            if target.doc in partners:
                kind = "partner"
            elif source_kind == "human" and link.form == "mention":
                kind = "mention"
            else:
                kind = source_kind
            form = "mention-basename" if target.hint == "basename" else link.form
            repo.docs[target.doc].inbound.append({
                "path": doc.path, "line": link.line, "col": link.col, "kind": kind, "form": form, "raw": link.raw,
            })
    for doc in repo.docs.values():
        doc.reach = compute_reach(repo, doc)


EXEMPT_CLASSES = frozenset({"generated", "platform", "agent-config", "fixture", "archived"})


def compute_reach(repo: Repo, doc: Doc) -> str:
    if doc.cls == "entry":
        return "entry"
    if doc.cls in EXEMPT_CLASSES or doc.keep:
        return "exempt"
    if repo.kind == "site" and SITE_EXEMPT_KEYS & set(doc.parsed.frontmatter or []):
        return "exempt"
    kinds = Counter(entry["kind"] for entry in doc.inbound)
    if kinds["human"]:
        return "linked"
    if kinds["mention"]:
        return "mention-only"
    if kinds["agent"]:
        return "agent-only"
    if kinds["archive"]:
        return "archive-only"
    return "orphan"


def inbound_counts(doc: Doc) -> dict[str, int]:
    kinds = Counter(entry["kind"] for entry in doc.inbound)
    return {kind: kinds[kind] for kind in ("human", "agent", "archive", "partner", "mention")}


# ---------------------------------------------------------------------------
# Link edits
# ---------------------------------------------------------------------------

QUOTE_SAFE = "/._-~"


def render_destination(raw: str, form: str, style: str, source_after: str, target_after: str, repo_name: str,
                       root: Path) -> str:
    """The destination ``raw`` rewritten to reach ``target_after`` from ``source_after``, in the same style."""
    if form == "mention":
        path_part, _, fragment = raw.partition("#")
        suffix = f"#{fragment}" if fragment else ""
    else:
        path_part, suffix = split_destination(raw)
    trailing = "/" if path_part.endswith("/") and len(path_part) > 1 else ""
    if style == "root":
        new = target_after if form == "mention" else "/" + target_after
    elif style == "prefixed":
        new = f"{repo_name}/{target_after}"
    elif style == "absolute":
        new = f"{root}/{target_after}"
    elif style == "home":
        home = str(Path.home())
        absolute = f"{root}/{target_after}"
        new = "~" + absolute[len(home):] if absolute.startswith(home + "/") else absolute
    elif style == "basename":
        new = posixpath.basename(target_after)
    else:
        new = posixpath.relpath(target_after or ".", posixpath.dirname(source_after) or ".")
        if path_part.startswith("./") and not new.startswith("../"):
            new = "./" + new
    if trailing and not new.endswith("/"):
        new += "/"
    if "%" in path_part:
        new = urllib.parse.quote(new, safe=QUOTE_SAFE)
    return new + suffix


def link_edit(doc: Doc, link: Link, new_raw: str, file_after: str, reason: str) -> dict[str, Any]:
    line_text = split_lines(doc.text)[link.line - 1]
    start = max(link.col - 1, 0)
    end = min(link.end + 1, len(line_text))
    old = line_text[start:end]
    new = line_text[start:link.col] + new_raw + line_text[link.end:end]
    return {"file": doc.path, "fileAfter": file_after, "old": old, "new": new, "count": 1, "replaceAll": False,
            "lines": [link.line], "form": link.form, "reason": reason, "_line": line_text}


def consolidate_edits(repo: Repo, edits: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Group identical substitutions and prove each one is safe to replace.

    A substitution is safe to apply with replace-all only when every copy of
    ``old`` in the file is one of the links being rewritten. A copy inside a
    code sample fails that test; it falls back to a whole-line edit when the
    line is unique, and to a manual edit otherwise.
    """
    grouped: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for edit in edits:
        grouped.setdefault((edit["file"], edit["old"]), []).append(edit)
    ready: list[dict[str, Any]] = []
    manual: list[dict[str, Any]] = []
    for (file, old), group in grouped.items():
        news = {edit["new"] for edit in group}
        text = repo.docs[file].text if file in repo.docs else (repo.root / file).read_text(encoding="utf-8", errors="replace")
        lines = sorted({line for edit in group for line in edit["lines"]})
        count = text.count(old)
        if len(news) == 1 and count == len(group):
            ready.append({**_public_edit(group[0]), "count": count, "replaceAll": count > 1, "lines": lines})
            continue
        for edit in group:
            line_text = edit["_line"]
            line_new = line_text.replace(edit["old"], edit["new"], 1)
            if line_text and text.count(line_text) == 1 and line_text.count(edit["old"]) == 1:
                ready.append({**_public_edit(edit), "old": line_text, "new": line_new, "count": 1, "replaceAll": False})
            else:
                manual.append({"file": file, "line": edit["lines"][0], "old": edit["old"], "new": edit["new"],
                               "why": "the same text appears elsewhere in the file (a code sample or a second link)"})
    ready.sort(key=lambda edit: (edit["file"], edit["lines"][0]))
    return ready, manual


def _public_edit(edit: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in edit.items() if not key.startswith("_")}


# ---------------------------------------------------------------------------
# Findings
# ---------------------------------------------------------------------------

CLUSTER_MIN_BROKEN = 10
CLUSTER_MIN_RATIO = 0.5
UNINDEXED_MIN = 3
UNINDEXED_RATIO = 0.5


def finding(code: str, severity: str, path: str, message: str, evidence: dict[str, Any] | None = None,
            **extra: Any) -> dict[str, Any]:
    item: dict[str, Any] = {"code": code, "severity": severity, "path": path, "message": message,
                            "evidence": evidence or {}}
    item.update({key: value for key, value in extra.items() if value is not None})
    return item


def _risk(doc: Doc) -> str | None:
    risk = doc.delete_risk
    return None if risk == "none" else risk


def link_findings(repo: Repo) -> list[dict[str, Any]]:
    findings: list[dict[str, Any]] = []
    by_dir: dict[str, list[Doc]] = {}
    for doc in repo.docs.values():
        if doc.cls != "fixture":
            by_dir.setdefault(posixpath.dirname(doc.path), []).append(doc)
    clustered: set[str] = set()
    for directory, docs in sorted(by_dir.items()):
        broken = sum(len(doc.broken) for doc in docs)
        relative = sum(doc.out_links for doc in docs)
        if broken >= CLUSTER_MIN_BROKEN and relative and broken / relative >= CLUSTER_MIN_RATIO:
            clustered.update(doc.path for doc in docs)
            findings.append(finding(
                "broken-link-cluster", "warning", directory or ".",
                f"{broken} of {relative} relative links in this directory are broken - a partial copy or vendored docs",
                {"dir": directory or ".", "broken": broken, "ratio": round(broken / relative, 2),
                 "files": {doc.path: len(doc.broken) for doc in docs if doc.broken}},
                suggest="review"))
    for doc in sorted(repo.docs.values(), key=lambda d: d.path):
        if doc.broken and doc.path not in clustered:
            severity = "suggestion" if doc.cls == "archived" else "critical"
            findings.append(finding(
                "broken-link", severity, doc.path, f"{len(doc.broken)} broken relative link(s)",
                {"links": doc.broken}, suggest="relink"))
    findings.extend(wrong_language_findings(repo))
    return findings


def wrong_language_findings(repo: Repo) -> list[dict[str, Any]]:
    findings = []
    for doc in sorted(repo.docs.values(), key=lambda d: d.path):
        if not doc.pair or doc.cls == "fixture":
            continue
        edits = []
        for link, target in doc.resolved:
            if target.status != "file" or not target.doc or link.form == "mention":
                continue
            if target.doc in doc.pair["partners"] or target.doc == doc.path:
                continue
            wanted = None
            if doc.pair["role"] == "mirror":
                code = doc.pair["lang"]
                wanted = repo.pairs.get(target.doc, {}).get(code)
            elif target.doc in repo.mirrors:
                wanted = repo.mirrors[target.doc][0]
            if not wanted or wanted == target.doc:
                continue
            new_raw = render_destination(link.raw, link.form, target.style, doc.path, wanted, repo.root.name, repo.root)
            edit = link_edit(doc, link, new_raw, doc.path, "wrong-language")
            if "#" in link.raw:
                edit["anchorNeedsReview"] = True
            edits.append(edit)
        if edits:
            ready, manual = consolidate_edits(repo, edits)
            findings.append(finding(
                "wrong-language-link", "warning", doc.path,
                f"{len(edits)} link(s) point at the other language's half of a pair",
                {"links": [{"line": e["lines"][0], "old": e["old"], "new": e["new"]} for e in ready],
                 "manual": manual},
                suggest="relink", fix=ready))
    return findings


def reach_findings(repo: Repo) -> list[dict[str, Any]]:
    findings: list[dict[str, Any]] = []
    orphan_severity = "suggestion" if repo.kind in ("site", "wiki") else "warning"
    seen_units: set[str] = set()
    orphan_units: list[list[str]] = []
    agent_only: dict[str, list[str]] = {}
    agent_sources: dict[str, set[str]] = {}
    mention_only: list[dict[str, Any]] = []
    for doc in sorted(repo.docs.values(), key=lambda d: d.path):
        if not doc.triageable or doc.keep:
            continue
        unit = repo.unit_of(doc.path)
        if unit in seen_units:
            continue
        seen_units.add(unit)
        paths = [p for p in repo.unit_paths(doc.path) if p in repo.docs]
        halves = [repo.docs[p] for p in paths]
        reaches = {half.reach for half in halves}
        if reaches == {"orphan"}:
            orphan_units.append(paths)
        elif reaches <= {"agent-only", "orphan"} and "agent-only" in reaches:
            index = nearest_index(repo, unit)
            agent_only.setdefault(index, []).extend(paths)
            agent_sources.setdefault(index, set()).update(
                entry["path"] for half in halves for entry in half.inbound if entry["kind"] == "agent")
        elif reaches == {"mention-only"}:
            mention_only.append({"path": unit, "from": sorted({e["path"] for h in halves for e in h.inbound if e["kind"] == "mention"})[:5]})
        if len(halves) > 1:
            findings.extend(unreachable_half_findings(repo, halves))

    by_dir: dict[str, list[list[str]]] = {}
    for paths in orphan_units:
        by_dir.setdefault(posixpath.dirname(paths[0]), []).append(paths)
    triageable_units: Counter[str] = Counter(
        posixpath.dirname(repo.unit_of(doc.path)) for doc in repo.docs.values()
        if doc.triageable and repo.unit_of(doc.path) == doc.path)
    for directory, units in sorted(by_dir.items()):
        if len(units) >= UNINDEXED_MIN and len(units) / max(triageable_units[directory], 1) >= UNINDEXED_RATIO:
            findings.append(finding(
                "unindexed-dir", orphan_severity, directory or ".",
                f"{len(units)} documents here are linked from nowhere - the directory has no index",
                {"dir": directory or ".", "hasReadme": repo.dir_index(directory) is not None,
                 "orphans": [paths[0] for paths in units]},
                paths=[p for paths in units for p in paths], suggest="index"))
            continue
        for paths in units:
            base = repo.docs[paths[0]]
            findings.append(finding(
                "orphan", orphan_severity, paths[0], "no document, index or config links here",
                {"bytes": base.bytes, "title": base.parsed.title["text"] if base.parsed.title else None,
                 "gitLast": base.git["last"] if base.git else None, "partners": paths[1:]},
                paths=paths if len(paths) > 1 else None, suggest="review", deleteRisk=_risk(base)))
    for index, paths in sorted(agent_only.items()):
        findings.append(finding(
            "agent-only-reachable", "suggestion", index,
            f"{len(paths)} document(s) are reachable only from agent config, not from an index a person reads",
            {"index": index, "agentSources": sorted(agent_sources[index])[:10]},
            paths=sorted(paths), suggest="index"))
    if mention_only:
        findings.append(finding(
            "mention-only", "suggestion", ".", f"{len(mention_only)} document(s) are only named in backticks, never linked",
            {"items": mention_only}, suggest="index"))
    return findings


def unreachable_half_findings(repo: Repo, halves: list[Doc]) -> list[dict[str, Any]]:
    findings = []
    base = halves[0]
    base_inbound = sum(1 for e in base.inbound if e["kind"] in ("human", "mention"))
    if not base_inbound:
        return findings
    for mirror in halves[1:]:
        own = sum(1 for e in mirror.inbound if e["kind"] in ("human", "mention"))
        if own:
            continue
        findings.append(finding(
            "unreachable-pair-half", "warning", mirror.path,
            f"the base half is linked {base_inbound} time(s); this half is linked from nowhere",
            {"partner": base.path, "partnerInbound": base_inbound}, suggest="relink"))
    return findings


def nearest_index(repo: Repo, rel: str) -> str:
    """The closest README above ``rel`` in the same language, or the directory itself."""
    mirror = repo.mirrors.get(rel)
    suffix = f"{mirror[2]}{mirror[1]}" if mirror else ""
    directory = posixpath.dirname(rel)
    while True:
        for name in INDEX_NAMES:
            stem, extension = posixpath.splitext(name)
            candidate_name = f"{stem}{suffix}{extension}" if suffix else name
            candidate = f"{directory}/{candidate_name}" if directory else candidate_name
            if candidate in repo.docs and candidate != rel:
                return candidate
        if not directory:
            return posixpath.dirname(rel) or "."
        directory = posixpath.dirname(directory)


def class_findings(repo: Repo) -> list[dict[str, Any]]:
    findings: list[dict[str, Any]] = []
    seen: set[str] = set()
    for doc in sorted(repo.docs.values(), key=lambda d: d.path):
        if doc.cls not in ("byproduct", "historical", "guide") or doc.keep:
            continue
        unit = repo.unit_of(doc.path)
        if unit in seen:
            continue
        paths = [p for p in repo.unit_paths(doc.path) if p in repo.docs]
        halves = [repo.docs[p] for p in paths]
        classes = {half.cls for half in halves}
        base = repo.docs[unit] if unit in repo.docs else doc
        hits = [dict(hit, path=half.path) for half in halves for hit in half.hits]
        inbound = {kind: sum(inbound_counts(half)[kind] for half in halves)
                   for kind in ("human", "agent", "archive", "partner", "mention")}
        if "byproduct" in classes:
            seen.add(unit)
            findings.append(finding(
                "byproduct", "warning", unit, "looks like a session byproduct",
                {"rules": hits, "tracked": base.tracked, "partners": paths[1:], "inbound": inbound},
                paths=paths if len(paths) > 1 else None, suggest="delete", deleteRisk=_risk(base)))
        elif "historical" in classes and not base.migration:
            seen.add(unit)
            findings.append(finding(
                "historical-record", "warning", unit, "looks like a record of finished work",
                {"dated": base.dated, "rules": hits, "archiveTarget": archive_target(repo.archive, unit),
                 "inbound": inbound},
                paths=paths if len(paths) > 1 else None, suggest="archive", deleteRisk=_risk(base)))
        elif base.dated and base.dated["from"] == "name" and not base.dated["conventional"] and not base.migration:
            seen.add(unit)
            findings.append(finding(
                "historical-record", "suggestion", unit, "a dated document, with no other sign it is finished",
                {"dated": base.dated, "rules": hits, "archiveTarget": archive_target(repo.archive, unit),
                 "inbound": inbound},
                paths=paths if len(paths) > 1 else None, suggest="review"))
    for directory, info in sorted(repo.migrations.items()):
        finished = info["status"]["lean"] == "done"
        findings.append(finding(
            "migration-folder", "warning" if finished else "suggestion", directory,
            "a migration folder whose status reads as finished" if finished else "a migration folder (status not finished)",
            {**info, "archiveTarget": archive_target(repo.archive, directory)},
            paths=info["files"], suggest="archive" if finished else "review"))
    historical = any(f["code"] in ("historical-record", "migration-folder") and f["severity"] == "warning" for f in findings)
    if historical and repo.archive["needsAsk"]:
        findings.append(finding(
            "no-archive-convention", "suggestion", ".",
            "finished history was found, but this repository has no single archive directory to move it to",
            {"reason": repo.archive["reason"], "dirs": repo.archive["dirs"]}, suggest="ask"))
    return findings


def list_findings(repo: Repo, split_kb: int, now: dt.date) -> list[dict[str, Any]]:
    findings: list[dict[str, Any]] = []
    oversize = []
    untracked = []
    root_clutter = []
    seen: set[str] = set()
    for doc in sorted(repo.docs.values(), key=lambda d: d.path):
        if doc.cls in ("fixture",):
            continue
        if doc.tracked is False:
            untracked.append({"path": doc.path, "ageDays": max((now - local_date(doc.mtime)).days, 0)})
        if "/" not in doc.path and doc.triageable and not doc.keep:
            root_clutter.append({"path": doc.path, "class": doc.cls})
        unit = repo.unit_of(doc.path)
        if doc.cls in ("guide", "byproduct", "historical", "entry") and unit not in seen:
            base = repo.docs.get(unit, doc)
            if base.bytes > split_kb * 1024:
                seen.add(unit)
                mirrors = [repo.docs[p].bytes for p in repo.unit_paths(unit)[1:] if p in repo.docs]
                oversize.append({"path": unit, "bytes": base.bytes, "mirrorBytes": mirrors, "sections": base.parsed.sections[:40]})
    if oversize:
        findings.append(finding("oversize", "suggestion", ".", f"{len(oversize)} document(s) exceed {split_kb} KB",
                                {"items": oversize, "splitKb": split_kb}, suggest="split"))
    if untracked:
        findings.append(finding("untracked", "suggestion", ".", f"{len(untracked)} document(s) were never committed",
                                {"items": untracked}, suggest="commit-or-delete", deleteRisk="untracked"))
    if root_clutter:
        findings.append(finding("root-clutter", "suggestion", ".",
                                f"{len(root_clutter)} document(s) at the repository root are not conventional root files",
                                {"items": root_clutter}, suggest="review"))
    return findings


def local_date(timestamp: float) -> dt.date:
    return dt.datetime.fromtimestamp(timestamp).date()


def summarize(findings: list[dict[str, Any]]) -> dict[str, Any]:
    severity = Counter(item["severity"] for item in findings)
    return {"critical": severity["critical"], "warning": severity["warning"], "suggestion": severity["suggestion"],
            "byCode": dict(sorted(Counter(item["code"] for item in findings).items()))}


def sort_findings(findings: list[dict[str, Any]]) -> list[dict[str, Any]]:
    def key(item: dict[str, Any]) -> tuple:
        links = item["evidence"].get("links") or [{}]
        return (SEVERITIES.index(item["severity"]), item["code"], item["path"], links[0].get("line", 0))
    return sorted(findings, key=key)


# ---------------------------------------------------------------------------
# scan
# ---------------------------------------------------------------------------

@dataclass
class ScanOptions:
    excludes: list[str] = field(default_factory=list)
    history: bool = True
    similarity: bool = True
    drift_days: int = 60
    bulk_md: int = 20
    split_kb: int = 30
    similarity_threshold: float = 0.80
    overlap_threshold: float = 0.50
    now: dt.date = field(default_factory=dt.date.today)


def analyze(path: Path, ruleset: RuleSet, options: ScanOptions) -> Repo:
    repo = discover(path, options.excludes)
    classify(repo, ruleset)
    build_graph(repo)
    load_history(repo, options)
    return repo


def collect_findings(repo: Repo, options: ScanOptions) -> list[dict[str, Any]]:
    findings = link_findings(repo)
    findings.extend(reach_findings(repo))
    findings.extend(class_findings(repo))
    findings.extend(list_findings(repo, options.split_kb, options.now))
    findings.extend(drift_findings(repo))
    if options.similarity:
        findings.extend(similarity_findings(repo, options))
    return sort_findings(findings)


def doc_slim(repo: Repo, doc: Doc) -> dict[str, Any]:
    return {
        "path": doc.path, "class": doc.cls, "reach": doc.reach,
        "title": doc.parsed.title["text"] if doc.parsed.title else None,
        "bytes": doc.bytes, "tracked": doc.tracked, "deleteRisk": doc.delete_risk,
        "gitLast": doc.git["last"] if doc.git else None,
        "partners": doc.pair["partners"] if doc.pair else [],
        "inbound": inbound_counts(doc),
        "rules": sorted({hit["id"] for hit in doc.hits}),
    }


def doc_record(repo: Repo, doc: Doc, now: dt.date, inbound_limit: int = 20, sections: bool | None = None,
               compact: bool = False) -> dict[str, Any]:
    """Everything measured about one document. ``compact`` is the shape a batch file carries."""
    record = doc_slim(repo, doc)
    record.pop("gitLast")
    record.pop("rules")
    record.update({
        "dirty": doc.dirty, "lines": doc.parsed.lines, "hangulRatio": doc.parsed.hangul_ratio,
        "mtime": dt.datetime.fromtimestamp(doc.mtime).isoformat(timespec="seconds"),
        "title": doc.parsed.title, "frontmatter": doc.parsed.frontmatter, "keep": doc.keep,
        "pair": doc.pair, "langPrefix": doc.lang_prefix, "dated": doc.dated, "archive": doc.archive,
        "migration": doc.migration, "git": doc.git, "drift": doc.drift,
        "links": {"out": doc.out_links, "broken": doc.broken},
        "inbound": {**inbound_counts(doc), "from": doc.inbound[:inbound_limit]},
        "rules": doc.hits,
    })
    with_sections = doc.bytes > 20 * 1024 if sections is None else sections
    if with_sections:
        record["sections"] = doc.parsed.sections[:60]
    if compact:
        for key in ("partners", "hangulRatio", "mtime"):
            record.pop(key)
        record["inbound"]["from"] = [f"{e['path']}:{e['line']} {e['kind']}" for e in doc.inbound[:inbound_limit]]
    return record


def envelope(command: str, ruleset: RuleSet | None, options: dict[str, Any], now: dt.date,
             warnings: list[str]) -> dict[str, Any]:
    return {
        "tool": TOOL, "schemaVersion": SCHEMA_VERSION, "command": command, "now": now.isoformat(),
        "rules": ruleset.describe() if ruleset else None, "options": options, "warnings": warnings,
    }


def scan_report(repo: Repo, ruleset: RuleSet, options: ScanOptions, findings: list[dict[str, Any]],
                focus: str | None, docs_mode: str) -> dict[str, Any]:
    if focus:
        findings = [f for f in findings if _in_focus(f, focus)]
    classes = Counter(doc.cls for doc in repo.docs.values())
    reaches = Counter(doc.reach for doc in repo.docs.values())
    report = envelope("scan", ruleset, {
        "excludes": options.excludes, "history": options.history, "similarity": options.similarity,
        "driftDays": options.drift_days, "bulkMd": options.bulk_md, "splitKb": options.split_kb,
        "similarityThreshold": options.similarity_threshold, "overlapThreshold": options.overlap_threshold,
    }, options.now, repo.warnings)
    named = _named_paths(findings)
    if docs_mode == "full":
        docs = [doc_record(repo, doc, options.now) for doc in sorted(repo.docs.values(), key=lambda d: d.path)]
    elif docs_mode == "slim":
        docs = [doc_slim(repo, repo.docs[p]) for p in sorted(named) if p in repo.docs]
    else:
        docs = []
    report.update({
        "root": str(repo.root),
        "focus": focus,
        "repo": {"git": repo.git, "kind": repo.kind, "site": repo.site, "fork": repo.fork, "shallow": repo.shallow,
                 "head": repo.head, "nestedRepos": repo.nested_repos},
        "inventory": {
            "docs": len(repo.docs),
            "tracked": sum(1 for d in repo.docs.values() if d.tracked),
            "untracked": sum(1 for d in repo.docs.values() if d.tracked is False),
            "dirty": sum(1 for d in repo.docs.values() if d.dirty),
            "byClass": dict(sorted(classes.items())),
            "byReach": dict(sorted(reaches.items())),
            "excluded": repo.excluded,
        },
        "archive": {key: value for key, value in repo.archive.items() if not key.startswith("_")},
        "pairs": repo.pair_info,
        "history": repo.history,
        "summary": summarize(findings),
        "findings": findings,
        "docs": docs,
    })
    return report


def _named_paths(findings: list[dict[str, Any]]) -> set[str]:
    named: set[str] = set()
    for item in findings:
        named.add(item["path"])
        named.update(item.get("paths") or [])
        for entry in item["evidence"].get("items") or []:
            if isinstance(entry, dict) and "path" in entry:
                named.add(entry["path"])
    return named


def _in_focus(item: dict[str, Any], focus: str) -> bool:
    prefix = focus.rstrip("/") + "/"
    paths = _named_paths([item])
    return any(p == focus or p.startswith(prefix) for p in paths)


# ---------------------------------------------------------------------------
# History: one git log pass
# ---------------------------------------------------------------------------

ASSET_EXTENSIONS = (".txt", ".rst", ".adoc", ".png", ".jpg", ".jpeg", ".gif", ".svg", ".drawio", ".excalidraw", ".webp")
DRIFT_CLASSES = frozenset({"guide", "byproduct", "historical", "entry"})


def is_code_path(rel: str) -> bool:
    """A path whose change can make a document describing its directory stale."""
    lowered = rel.lower()
    if lowered.endswith(DOC_EXTENSIONS) or lowered.endswith(ASSET_EXTENSIONS):
        return False
    return not any(part.casefold() in DOCS_DIR_NAMES for part in rel.split("/")[:-1])


def iso_day(timestamp: int) -> str:
    return dt.datetime.fromtimestamp(timestamp).date().isoformat()


def load_history(repo: Repo, options: ScanOptions) -> None:
    """Last-touched dates for documents, and code-change times per directory.

    A bulk commit - one that touches ``--bulk-md`` documents or more, like a
    repository-wide wording pass - resets every document's date without making
    any of them current. Drift therefore measures from each document's last
    commit that was not a bulk commit.
    """
    if not repo.git:
        repo.history = {"available": False, "commits": 0, "bulkCommits": 0, "reason": "not a git repository"}
        return
    if not options.history:
        repo.history = {"available": False, "commits": 0, "bulkCommits": 0, "reason": "disabled"}
        return
    output = run_git(repo.root, "log", "--no-renames", "--no-show-signature", "--no-color", "--name-only", "-z",
                     "--format=%x1e%H%x1f%at")
    if output is None:
        repo.warnings.append("git log failed or timed out; history is unavailable")
        repo.history = {"available": False, "commits": 0, "bulkCommits": 0, "reason": "git log failed"}
        return
    if repo.shallow:
        repo.warnings.append("shallow clone: history is truncated, dates may be too recent")
    stats: dict[str, dict[str, Any]] = {}
    code_times: dict[str, list[int]] = {}
    commits = bulk_commits = 0
    for chunk in output.decode("utf-8", "surrogateescape").split("\x1e"):
        if not chunk.strip():
            continue
        header, _, rest = chunk.partition("\0")
        try:
            when = int(header.partition("\x1f")[2].strip())
        except ValueError:
            continue
        commits += 1
        paths = [nfc(p) for p in rest.lstrip("\n").split("\0") if p]
        doc_paths = [p for p in paths if is_doc_path(p)]
        bulk = len(doc_paths) >= options.bulk_md
        bulk_commits += bulk
        for rel in doc_paths:
            entry = stats.setdefault(rel, {"last": None, "lastSubstantive": None, "commits": 0})
            if entry["last"] is None:
                entry["last"] = when
            if not bulk and entry["lastSubstantive"] is None:
                entry["lastSubstantive"] = when
            entry["commits"] += 1
        touched: set[str] = set()
        for rel in paths:
            if is_code_path(rel):
                parts = rel.split("/")
                touched.update("/".join(parts[:depth]) for depth in range(1, len(parts)))
        for directory in touched:
            code_times.setdefault(directory, []).append(when)
    for times in code_times.values():
        times.sort()
    repo.history = {"available": True, "commits": commits, "bulkCommits": bulk_commits, "reason": None}
    for doc in repo.docs.values():
        entry = stats.get(doc.path)
        if entry:
            doc.git = {"last": iso_day(entry["last"]),
                       "lastSubstantive": iso_day(entry["lastSubstantive"]) if entry["lastSubstantive"] else None,
                       "commits": entry["commits"]}
        if doc.cls in DRIFT_CLASSES and entry:
            doc.drift = compute_drift(doc, entry, code_times, options.drift_days)


def compute_drift(doc: Doc, entry: dict[str, Any], code_times: dict[str, list[int]], drift_days: int) -> dict[str, Any] | None:
    directories = doc.path.split("/")[:-1]
    if not directories or directories[0].casefold() in DOCS_DIR_NAMES:
        return None  # the repository root, or the top-level docs tree: no single directory it describes
    describes = "/".join(directories[:-1] if directories[-1].casefold() in DOCS_DIR_NAMES else directories)
    times = code_times.get(describes)
    doc_last = entry["lastSubstantive"] or entry["last"]
    if not describes or not times:
        return None
    days = (times[-1] - doc_last) // 86400
    if days < drift_days:
        return None
    return {"describes": describes, "docLast": iso_day(doc_last), "codeLast": iso_day(times[-1]), "days": days,
            "codeCommitsSince": len(times) - bisect_right(times, doc_last)}


def drift_findings(repo: Repo) -> list[dict[str, Any]]:
    items = [
        {"path": doc.path, **doc.drift} for doc in repo.docs.values()
        if doc.drift and doc.path not in repo.mirrors and not doc.keep
    ]
    if not items:
        return []
    items.sort(key=lambda item: (-item["days"], item["path"]))
    return [finding("drift", "suggestion", ".",
                    f"{len(items)} document(s) were last changed long before the code they describe",
                    {"items": items}, suggest="review")]


# ---------------------------------------------------------------------------
# Similarity
# ---------------------------------------------------------------------------

SIMILARITY_CLASSES = frozenset({"guide", "entry", "byproduct", "historical", "archived"})
SHINGLE_WORDS = 3
MIN_SHINGLES = 50
MAX_SIMILARITY_DOCS = 5000
CANDIDATE_SHARE = 0.30
WORD = re.compile(r"\w+")


def shingle_set(body: str) -> set[int]:
    words = WORD.findall(body.casefold())
    return {zlib.crc32(" ".join(words[i:i + SHINGLE_WORDS]).encode("utf-8"))
            for i in range(len(words) - SHINGLE_WORDS + 1)}


def similarity_findings(repo: Repo, options: ScanOptions) -> list[dict[str, Any]]:
    """Duplicates by exact body hash, near-duplicates by shingle Jaccard.

    Names and titles are never used: templated titles and a README in every
    directory make both useless as a signal. Candidates come from an inverted
    index over rare shingles, so the cost grows with the text, not with the
    square of the document count.
    """
    eligible = [doc for doc in repo.docs.values() if doc.cls in SIMILARITY_CLASSES and not doc.keep]
    if len(eligible) > MAX_SIMILARITY_DOCS:
        repo.warnings.append(f"similarity skipped: {len(eligible)} documents exceed {MAX_SIMILARITY_DOCS}")
        return []
    shingles: dict[str, set[int]] = {}
    digests: dict[str, list[str]] = {}
    for doc in eligible:
        grams = shingle_set(doc.parsed.body)
        if len(grams) < MIN_SHINGLES:
            continue
        shingles[doc.path] = grams
        normalized = _WHITESPACE.sub(" ", doc.parsed.body.casefold()).strip()
        digests.setdefault(hashlib.sha1(normalized.encode("utf-8")).hexdigest(), []).append(doc.path)

    findings: list[dict[str, Any]] = []
    grouped: set[tuple[str, str]] = set()
    for paths in digests.values():
        members = [p for p in sorted(paths) if not _partners(repo, p, paths)]
        if len(members) < 2:
            continue
        grouped.update((a, b) for a in members for b in members if a < b)
        active = [p for p in members if repo.docs[p].cls != "archived"]
        if not active:
            continue
        if len(active) < len(members):
            for archived in (p for p in members if p not in active):
                findings.append(_archive_copy(active[0], archived, 1.0))
            if len(active) < 2:
                continue
        findings.append(finding("duplicate", "warning", active[0], f"{len(active)} documents have identical text",
                                {"jaccard": 1.0, "identical": True, "sameTitle": True},
                                paths=active, suggest="delete"))

    cap = max(10, -(-len(shingles) * 2 // 100))
    shared: Counter[tuple[str, str]] = Counter()
    postings: dict[int, list[str]] = {}
    for path in sorted(shingles):
        for gram in shingles[path]:
            postings.setdefault(gram, []).append(path)
    for paths in postings.values():
        if 2 <= len(paths) <= cap:
            for i, first in enumerate(paths):
                for second in paths[i + 1:]:
                    shared[(first, second)] += 1
    overlaps = []
    for (first, second), count in sorted(shared.items()):
        if (first, second) in grouped or second in _partner_set(repo, first):
            continue
        a, b = shingles[first], shingles[second]
        if count / min(len(a), len(b)) < CANDIDATE_SHARE:
            continue
        score = round(len(a & b) / len(a | b), 2)
        archived = [p for p in (first, second) if repo.docs[p].cls == "archived"]
        if len(archived) == 2 or score < options.overlap_threshold:
            continue
        if score >= options.similarity_threshold:
            if archived:
                active = first if archived[0] == second else second
                findings.append(_archive_copy(active, archived[0], score))
            else:
                findings.append(finding(
                    "duplicate", "warning", first, f"near-duplicate of {second} (Jaccard {score})",
                    {"jaccard": score, "identical": False, "sameTitle": _same_title(repo, first, second)},
                    paths=[first, second], suggest="merge"))
        elif not archived:
            overlaps.append({"paths": [first, second], "jaccard": score})
    if overlaps:
        findings.append(finding("overlap", "suggestion", ".", f"{len(overlaps)} document pair(s) share much of their text",
                                {"items": overlaps}, suggest="merge"))
    return findings


def _partner_set(repo: Repo, rel: str) -> set[str]:
    return set(repo.unit_paths(rel)) - {rel}


def _partners(repo: Repo, rel: str, group: list[str]) -> bool:
    """True when ``rel`` is a mirror whose base is in the same group."""
    mirror = repo.mirrors.get(rel)
    return bool(mirror and mirror[0] in group)


def _same_title(repo: Repo, first: str, second: str) -> bool:
    titles = [repo.docs[p].parsed.title for p in (first, second)]
    return bool(titles[0] and titles[1] and titles[0]["text"] == titles[1]["text"])


def _archive_copy(active: str, archived: str, score: float) -> dict[str, Any]:
    return finding("archive-copy", "suggestion", active, f"an archived copy exists at {archived}",
                   {"active": active, "archived": archived, "jaccard": score}, paths=[active, archived], suggest="delete")


# ---------------------------------------------------------------------------
# Run directory: batches and baseline
# ---------------------------------------------------------------------------

BATCH_UNITS = 25
BATCH_INBOUND = 10


def _scoped_finding(item: dict[str, Any], paths: set[str]) -> dict[str, Any]:
    """A repository-wide list finding, cut down to the entries about one unit."""
    items = item["evidence"].get("items")
    if items is None:
        return item
    kept = [entry for entry in items
            if entry.get("path") in paths or paths & set(entry.get("paths") or [])]
    return {**item, "evidence": {**item["evidence"], "items": kept}}


def write_run(repo: Repo, report: dict[str, Any], run_dir: Path, options: ScanOptions) -> list[Path]:
    run_dir = run_dir.resolve()
    root = repo.root.resolve()
    if run_dir == root or root in run_dir.parents:
        raise UsageError(f"--out must be outside the scanned tree: {run_dir}")
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "scan.json").write_text(dump_json(report, pretty=True), encoding="utf-8")

    # A migration folder is decided as one unit: its plan, status and phase
    # reports are archived together or not at all.
    units: dict[str, dict[str, Any]] = {}
    owner: dict[str, str] = {}
    for item in report["findings"]:
        if item["code"] == "migration-folder":
            paths = [p for p in item.get("paths") or [] if p in repo.docs]
            units[item["path"]] = {"id": item["path"], "kind": "folder", "paths": paths, "findings": [item]}
            owner.update((p, item["path"]) for p in paths)
    repo_findings = []
    for item in report["findings"]:
        if item["code"] == "migration-folder":
            continue
        named = [p for p in _named_paths([item]) if p in repo.docs]
        if not named:
            repo_findings.append(item)
            continue
        for unit_id in sorted({owner.get(p) or repo.unit_of(p) for p in named}):
            entry = units.get(unit_id)
            if entry is None:
                entry = units[unit_id] = {"id": unit_id, "kind": "document", "findings": [],
                                          "paths": [p for p in repo.unit_paths(unit_id) if p in repo.docs]}
            entry["findings"].append(_scoped_finding(item, set(entry["paths"])))
    groups: dict[str, list[dict[str, Any]]] = {}
    for unit in sorted(units):
        top = unit.split("/")[0] if "/" in unit else "."
        groups.setdefault(top, []).append(units[unit])
    ordered = [unit for top in sorted(groups) for unit in groups[top]]
    batches = [ordered[i:i + BATCH_UNITS] for i in range(0, len(ordered), BATCH_UNITS)] or [[]]
    written = []
    context = {
        "root": report["root"], "repo": report["repo"], "archive": report["archive"],
        "pairs": {"conventions": report["pairs"].get("conventions", [])},
    }
    for number, batch in enumerate(batches, 1):
        payload = {
            "tool": TOOL, "schemaVersion": SCHEMA_VERSION, "command": "batch", "run": run_dir.name,
            "batch": number, "of": len(batches), **context,
            "units": [{**unit, "docs": [doc_record(repo, repo.docs[p], options.now, inbound_limit=BATCH_INBOUND,
                                                   sections=repo.docs[p].bytes > options.split_kb * 1024, compact=True)
                                        for p in unit["paths"]]}
                      for unit in batch],
            "repoFindings": repo_findings,
        }
        target = run_dir / f"batch-{number:02d}.json"
        target.write_text(json.dumps(payload, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
        written.append(target)
    return written


def baseline_delta(report: dict[str, Any], baseline: dict[str, Any]) -> dict[str, Any]:
    def broken_counts(source: dict[str, Any]) -> Counter[str]:
        counts: Counter[str] = Counter()
        for item in source.get("findings", []):
            if item["code"] == "broken-link":
                for link in item["evidence"].get("links", []):
                    counts[link.get("resolved") or link.get("raw")] += 1
            elif item["code"] == "broken-link-cluster":
                counts[f"cluster:{item['path']}"] += item["evidence"].get("broken", 0)
        return counts

    before, after = broken_counts(baseline), broken_counts(report)
    new_broken = []
    for item in report.get("findings", []):
        if item["code"] != "broken-link":
            continue
        for link in item["evidence"].get("links", []):
            key = link.get("resolved") or link.get("raw")
            if after[key] > before[key]:
                new_broken.append({"path": item["path"], "line": link["line"], "raw": link["raw"], "resolved": key})
    resolved = sum(max(before[key] - after[key], 0) for key in before)

    def finding_keys(source: dict[str, Any]) -> set[tuple[str, str]]:
        return {(item["code"], item["path"]) for item in source.get("findings", [])}

    before_keys, after_keys = finding_keys(baseline), finding_keys(report)
    before_orphans = set(baseline.get("pairs", {}).get("orphanMirrors", []))
    after_orphans = set(report.get("pairs", {}).get("orphanMirrors", []))
    return {
        "newBroken": new_broken,
        "resolvedBroken": resolved,
        "newFindings": len(after_keys - before_keys),
        "goneFindings": len(before_keys - after_keys),
        "splitPairs": sorted(after_orphans - before_orphans),
    }


# ---------------------------------------------------------------------------
# relink: every edit a move, archive, merge or delete needs
# ---------------------------------------------------------------------------

@dataclass
class Operation:
    op: str                       # move | delete | merge
    source: str
    target: str | None
    expanded_from: str | None = None


def _repo_relative(repo: Repo, value: str) -> str:
    candidate = Path(value)
    if candidate.is_absolute():
        try:
            return nfc(candidate.resolve().relative_to(repo.root).as_posix())
        except ValueError:
            return nfc(value)
    return nfc(posixpath.normpath(value.replace(os.sep, "/")))


def _files_under(repo: Repo, directory: str) -> list[str]:
    prefix = directory + "/"
    return sorted(rel for rel in repo.files if rel.startswith(prefix))


def expand_operations(repo: Repo, args: argparse.Namespace) -> tuple[list[Operation], list[dict[str, Any]]]:
    operations: list[Operation] = []
    refusals: list[dict[str, Any]] = []

    def refuse(op: str, source: str, target: str | None, reason: str, detail: str) -> None:
        refusals.append({"op": op, "from": source, "to": target, "reason": reason, "detail": detail})

    def add_move(source: str, target: str, expanded: str | None) -> None:
        if source in repo.dirs and source not in repo.files:
            if target == source or target.startswith(source + "/"):
                refuse("move", source, target, "into-itself", "a directory cannot move inside itself")
                return
            for rel in _files_under(repo, source):
                operations.append(Operation("move", rel, target + rel[len(source):], expanded or "dir"))
        else:
            operations.append(Operation("move", source, target, expanded))

    if len(args.from_paths) != len(args.to_paths):
        raise UsageError("--from and --to must be given the same number of times")
    for raw_source, raw_target in zip(args.from_paths, args.to_paths):
        source, target = _repo_relative(repo, raw_source), _repo_relative(repo, raw_target)
        if raw_target.endswith("/") or target in repo.dirs:
            target = posixpath.join(target, posixpath.basename(source))
        add_move(source, target, None)

    convention = dict(repo.archive)
    if args.archive_dir:
        archive_dir = _repo_relative(repo, args.archive_dir)
        known = next((d for d in repo.archive.get("dirs", []) if d["path"] == archive_dir), None)
        convention = {"preferred": archive_dir, "layout": known["layout"] if known and known["layout"] != "unknown" else "mirror"}
    for raw_source in args.archive:
        source = _repo_relative(repo, raw_source)
        if not convention.get("preferred"):
            reason = "ambiguous-archive" if repo.archive.get("reason") == "ambiguous" else "no-archive-convention"
            refuse("move", source, None, reason, "pass --archive-dir to choose where retired documents go")
            continue
        sources = _files_under(repo, source) if source in repo.dirs and source not in repo.files else [source]
        for rel in sources:
            operations.append(Operation("move", rel, archive_target(convention, rel), "archive"))

    for spec in args.merge:
        if ":" not in spec:
            raise UsageError(f"--merge expects SRC:DST, got {spec!r}")
        raw_source, raw_target = spec.split(":", 1)
        operations.append(Operation("merge", _repo_relative(repo, raw_source), _repo_relative(repo, raw_target)))
    for raw_source in args.delete:
        source = _repo_relative(repo, raw_source)
        sources = _files_under(repo, source) if source in repo.dirs and source not in repo.files else [source]
        operations.extend(Operation("delete", rel, None, "dir" if rel != source else None) for rel in sources)

    if not args.no_pair:
        operations = expand_pairs(repo, operations)
    return operations, refusals


def expand_pairs(repo: Repo, operations: list[Operation]) -> list[Operation]:
    """A language pair moves, merges and disappears as one unit."""
    named = {op.source for op in operations}
    expanded = list(operations)
    for op in operations:
        if op.source not in repo.docs:
            continue
        for partner in repo.unit_paths(op.source):
            if partner == op.source or partner in named or partner not in repo.docs:
                continue
            named.add(partner)
            if op.op == "delete":
                expanded.append(Operation("delete", partner, None, "pair"))
            elif op.op == "move":
                expanded.append(Operation("move", partner, _partner_path(repo, op.source, op.target, partner), "pair"))
            else:
                # A partner merges only into the destination's own partner in the
                # same language; folding both languages into one file is never wanted.
                target_partner = _partner_path(repo, op.source, op.target, partner)
                if target_partner in repo.docs and target_partner != op.target:
                    expanded.append(Operation("merge", partner, target_partner, "pair"))
                else:
                    expanded.append(Operation("delete", partner, None, "pair"))
    return expanded


def _partner_path(repo: Repo, source: str, target: str, partner: str) -> str:
    """Where ``partner`` goes when ``source`` goes to ``target``."""
    target_dir, target_name = posixpath.split(target)
    target_stem, _ = posixpath.splitext(target_name)
    mirror = repo.mirrors.get(source)
    if mirror:
        suffix = mirror[2] + mirror[1]
        if target_stem.casefold().endswith(suffix.casefold()):
            target_stem = target_stem[: -len(suffix)]
    partner_name = posixpath.basename(partner)
    partner_stem, partner_ext = posixpath.splitext(partner_name)
    partner_mirror = repo.mirrors.get(partner)
    if partner_mirror:
        partner_name = f"{target_stem}{partner_mirror[2]}{partner_stem[-len(partner_mirror[1]):]}{partner_ext}"
    else:
        partner_name = f"{target_stem}{partner_ext}"
    return posixpath.join(target_dir, partner_name)


def check_operations(repo: Repo, operations: list[Operation], refusals: list[dict[str, Any]]) -> list[Operation]:
    accepted: list[Operation] = []
    targets = Counter(op.target for op in operations if op.op == "move")
    sources = {op.source for op in operations}
    ignored = _ignored_paths(repo, [op.target for op in operations if op.op == "move" and op.target])
    for op in operations:
        def refuse(reason: str, detail: str) -> None:
            refusals.append({"op": op.op, "from": op.source, "to": op.target, "reason": reason, "detail": detail})

        full = repo.root / op.source
        if op.source not in repo.files or not os.path.lexists(full):
            refuse("missing", "not a file in this repository")
            continue
        if full.is_symlink():
            refuse("symlink", "symbolic links are not moved or deleted")
            continue
        if op.op == "merge":
            if op.target not in repo.docs:
                refuse("missing", f"merge target {op.target} is not a document here")
                continue
            accepted.append(op)
            continue
        if op.op == "delete":
            accepted.append(op)
            continue
        target = op.target or ""
        if target == ".." or target.startswith("../") or target.startswith("/"):
            refuse("outside-repo", "the destination is outside the repository")
        elif target == op.source or target.startswith(op.source + "/"):
            refuse("into-itself", "the destination is the source or inside it")
        elif targets[target] > 1:
            refuse("collision", "two sources would move to the same destination")
        elif target in sources:
            refuse("collision", "the destination is itself being moved; swaps are not supported")
        elif (target in repo.files or os.path.lexists(repo.root / target)) and target.casefold() != op.source.casefold():
            refuse("target-exists", "a file already exists at the destination")
        elif _crosses_nested_repo(repo, target):
            refuse("nested-repo", "the destination is inside another git repository")
        elif target in ignored:
            refuse("target-ignored", "git ignores the destination, so the moved file would vanish from the index")
        else:
            accepted.append(op)
    return accepted


def _ignored_paths(repo: Repo, paths: list[str]) -> set[str]:
    if not repo.git or not paths:
        return set()
    output = subprocess.run(["git", "-C", str(repo.root), "check-ignore", "-z", "--no-index", "--stdin"],
                            input="\0".join(paths).encode("utf-8") + b"\0", capture_output=True, check=False).stdout
    return set(split_z(output))


def _crosses_nested_repo(repo: Repo, target: str) -> bool:
    parts = target.split("/")[:-1]
    for depth in range(1, len(parts) + 1):
        if (repo.root / "/".join(parts[:depth]) / ".git").exists():
            return True
    return False


def relink_report(repo: Repo, ruleset: RuleSet, operations: list[Operation], refusals: list[dict[str, Any]],
                  now: dt.date) -> dict[str, Any]:
    moves = {op.source: op.target for op in operations if op.op == "move"}
    merges = {op.source: op.target for op in operations if op.op == "merge"}
    deletes = {op.source for op in operations if op.op in ("delete", "merge")}
    directory_moves = _directory_moves(repo, moves)
    edits: list[dict[str, Any]] = []
    breaks: list[dict[str, Any]] = []
    warnings: list[dict[str, Any]] = []
    for doc in sorted(repo.docs.values(), key=lambda d: d.path):
        if doc.cls == "fixture" or doc.path in deletes:
            continue
        source_after = moves.get(doc.path, doc.path)
        for link, target in doc.resolved:
            if target.hint == "absolute-fs-path":
                continue  # broken the same way wherever the source lives
            looked_up = target.path or ""
            if looked_up in deletes and target.status != "broken":
                if looked_up in merges:
                    new_target = merges[looked_up]
                    reason = "merged"
                else:
                    breaks.append({"file": doc.path, "line": link.line, "col": link.col, "raw": link.raw,
                                   "target": looked_up, "linkText": _link_text(doc, link),
                                   "unlinked": _link_text(doc, link)})
                    continue
            else:
                new_target = moves.get(looked_up) or directory_moves.get(looked_up) or looked_up
                if source_after != doc.path and new_target != looked_up:
                    reason = "both-moved"
                elif source_after != doc.path:
                    reason = "source-moved"
                else:
                    reason = "target-moved"
            if source_after == doc.path and new_target == looked_up:
                continue
            if target.style == "basename" and posixpath.basename(new_target) == posixpath.basename(looked_up):
                continue
            new_raw = render_destination(link.raw, link.form, target.style, source_after, new_target,
                                         repo.root.name, repo.root)
            if new_raw == link.raw:
                continue
            edit = link_edit(doc, link, new_raw, source_after, reason)
            if reason == "merged" and "#" in link.raw:
                edit["anchorNeedsReview"] = True
            edits.append(edit)
    ready, manual = consolidate_edits(repo, edits)

    operation_rows = []
    for op in operations:
        tracked = (op.source in repo.tracked) if repo.git else None
        if op.op == "move":
            via = "git-mv" if tracked else "mv"
            mkdirs = _missing_parents(repo, op.target)
        else:
            via = "git-rm" if tracked else "rm"
            mkdirs = []
        operation_rows.append({"op": "delete" if op.op == "merge" else op.op, "from": op.source,
                               "to": op.target if op.op == "move" else None, "mergedInto": merges.get(op.source),
                               "tracked": tracked, "via": via, "mkdirs": mkdirs, "expandedFrom": op.expanded_from})
        if tracked is False:
            warnings.append({"path": op.source, "reason": "untracked",
                             "detail": "not in git: a move uses mv, and a delete cannot be undone"})
        elif op.source in repo.dirty:
            warnings.append({"path": op.source, "reason": "uncommitted", "detail": "has uncommitted changes"})
        if op.op == "move" and op.target and op.source.casefold() == op.target.casefold():
            warnings.append({"path": op.source, "reason": "case-only-rename",
                             "detail": "applied in two steps on a case-insensitive file system"})
        if op.op == "move" and op.source in repo.docs and posixpath.basename(op.source) in INDEX_NAMES \
                and posixpath.dirname(op.source) not in directory_moves:
            warnings.append({"path": op.source, "reason": "dir-index-moved",
                             "detail": "links to its directory stay valid but no longer reach this document"})
    for edit in ready:
        if repo.docs.get(edit["file"]) and repo.docs[edit["file"]].cls == "generated":
            warnings.append({"path": edit["file"], "reason": "generated-source",
                             "detail": "a generator will overwrite this edit"})
    report = envelope("relink", ruleset, {}, now, repo.warnings)
    report.update({
        "root": str(repo.root),
        "operations": operation_rows,
        "refusals": refusals,
        "edits": ready,
        "manual": manual,
        "breaks": breaks,
        "textRefs": _text_refs(repo, [op.source for op in operations] + [d + "/" for d in sorted(directory_moves)],
                               ready + [{"file": m["file"], "lines": [m["line"]]} for m in manual]),
        "warnings": report["warnings"],
        "opWarnings": warnings,
        "preexistingBroken": sum(len(doc.broken) for doc in repo.docs.values()),
    })
    return report


def _directory_moves(repo: Repo, moves: dict[str, str]) -> dict[str, str]:
    """Directories whose every file moves under one new prefix."""
    result: dict[str, str] = {}
    candidates = {posixpath.dirname(source) for source in moves}
    for source in list(candidates):
        parts = source.split("/")
        candidates.update("/".join(parts[:depth]) for depth in range(1, len(parts)))
    for directory in sorted(candidates):
        if not directory:
            continue
        files = _files_under(repo, directory)
        if not files or any(rel not in moves for rel in files):
            continue
        prefixes = {moves[rel][: len(moves[rel]) - len(rel) + len(directory)] for rel in files
                    if moves[rel].endswith(rel[len(directory):])}
        if len(prefixes) == 1 and all(moves[rel].endswith(rel[len(directory):]) for rel in files):
            result[directory] = prefixes.pop()
    return result


def _missing_parents(repo: Repo, target: str | None) -> list[str]:
    if not target:
        return []
    missing = []
    parent = posixpath.dirname(target)
    while parent and parent not in repo.dirs and not (repo.root / parent).is_dir():
        missing.append(parent)
        parent = posixpath.dirname(parent)
    return sorted(missing)


def _link_text(doc: Doc, link: Link) -> str:
    line = split_lines(doc.text)[link.line - 1]
    if link.form in ("inline", "image"):
        close = line.rfind("](", 0, link.col)
        opener = _open_bracket(line, close) if close >= 0 else None
        if opener is not None:
            return line[opener + 1:close]
    return link.raw


def _text_refs(repo: Repo, sources: list[str], edits: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Plain-text mentions of moving paths that no edit covers: Makefiles, CI, scripts."""
    if not repo.git or not sources:
        return []
    args = ["grep", "-n", "-z", "-F", "-I"]
    for source in sources:
        args += ["-e", source]
    output = run_git(repo.root, *args)
    if not output:
        return []
    covered = {(edit["file"], line) for edit in edits for line in edit["lines"]}
    longest_first = sorted(sources, key=len, reverse=True)
    refs = []
    for record in output.decode("utf-8", "surrogateescape").split("\n"):
        parts = record.split("\0")
        if len(parts) < 3:
            continue
        file, lineno, text = nfc(parts[0]), parts[1], parts[2]
        if not lineno.isdigit() or (file, int(lineno)) in covered or file in sources:
            continue
        refs.append({"file": file, "line": int(lineno), "text": text.strip()[:200],
                     "path": next(s for s in longest_first if s in text)})
    return refs


# ---------------------------------------------------------------------------
# apply: the only command that writes
# ---------------------------------------------------------------------------

def apply_plan(plan: dict[str, Any], allow_staged: bool, allow_manual: bool) -> dict[str, Any]:
    """Apply a relink result exactly, or change nothing.

    Every check runs before the first write: each edit's text must appear
    exactly as many times as it did when the plan was computed, every source
    must exist, no destination may, and nothing may already be staged. A plan
    applied once fails the second time, because its sources are gone.
    """
    root = Path(plan.get("root", ""))
    problems: list[str] = []
    if plan.get("tool") != TOOL or plan.get("command") != "relink":
        raise UsageError("--plan must be a relink result produced by this script")
    top = git_toplevel(root) if root.is_dir() else None
    if top is None or top != root.resolve():
        raise UsageError(f"apply works only at the top of a git repository: {root}")
    if plan.get("refusals"):
        problems.append(f"the plan has {len(plan['refusals'])} refusal(s); fix them and compute it again")
    if plan.get("manual") and not allow_manual:
        problems.append(f"the plan has {len(plan['manual'])} manual edit(s); make them first, or pass --allow-manual")
    staged = split_z(run_git(root, "diff", "--cached", "--name-only", "-z"))
    if staged and not allow_staged:
        problems.append(f"{len(staged)} file(s) are already staged; commit or unstage them, or pass --allow-staged")

    by_file: dict[str, list[dict[str, Any]]] = {}
    for edit in plan.get("edits", []):
        by_file.setdefault(edit["file"], []).append(edit)
    new_texts: dict[str, str] = {}
    for file, edits in sorted(by_file.items()):
        path = root / file
        try:
            text = path.read_text(encoding="utf-8")
        except OSError:
            problems.append(f"{file}: cannot read")
            continue
        spans: list[tuple[int, int, str]] = []
        for edit in edits:
            positions = _find_all(text, edit["old"])
            expected = edit["count"] if edit.get("replaceAll") else 1
            if len(positions) != expected or (not edit.get("replaceAll") and edit["count"] != 1):
                problems.append(f"{file}:{edit['lines'][0]}: expected {expected} of {edit['old']!r}, found {len(positions)}")
                continue
            spans.extend((start, start + len(edit["old"]), edit["new"]) for start in positions)
        spans.sort()
        if any(a[1] > b[0] for a, b in zip(spans, spans[1:])):
            problems.append(f"{file}: two edits overlap")
            continue
        pieces, cursor = [], 0
        for start, end, new in spans:
            pieces.append(text[cursor:start])
            pieces.append(new)
            cursor = end
        pieces.append(text[cursor:])
        new_texts[file] = "".join(pieces)

    operations = plan.get("operations", [])
    modified = set(split_z(run_git(root, "diff", "--name-only", "-z")))
    for row in operations:
        source = root / row["from"]
        if not os.path.lexists(source):
            problems.append(f"{row['from']}: no longer exists")
        if row["op"] == "delete" and row["from"] in modified:
            problems.append(f"{row['from']}: has uncommitted changes that git rm would refuse to discard")
        if row["op"] == "move" and os.path.lexists(root / row["to"]) and row["to"].casefold() != row["from"].casefold():
            problems.append(f"{row['to']}: already exists")
        if row["op"] == "delete" and not row.get("tracked"):
            problems.append(f"{row['from']}: untracked - delete it yourself, after checking it, because git cannot restore it")
    if problems:
        return {"applied": False, "problems": problems}

    executed: list[dict[str, Any]] = []
    undo: list[str] = []
    for file, text in new_texts.items():
        (root / file).write_text(text, encoding="utf-8")
        executed.append({"op": "edit", "file": file, "edits": len(by_file[file])})
    failure = None
    for row in operations:
        try:
            if row["op"] == "move":
                for parent in row.get("mkdirs", []):
                    (root / parent).mkdir(parents=True, exist_ok=True)
                (root / row["to"]).parent.mkdir(parents=True, exist_ok=True)
                _move(root, row)
                undo.append(("git mv" if row["tracked"] else "mv") + f" {_q(row['to'])} {_q(row['from'])}")
            else:
                _git(root, "rm", "-q", "--", row["from"])
                undo.append(f"git restore --staged --worktree --source=HEAD -- {_q(row['from'])}")
        except (OSError, subprocess.CalledProcessError) as exc:
            failure = f"{row['op']} {row['from']}: {exc}"
            break
        executed.append({"op": row["op"], "from": row["from"], "to": row.get("to")})
    edited = [file for file in new_texts]
    if edited:
        undo.append("reverse the substitutions listed in the plan's edits (old <-> new) in: " + ", ".join(edited))
    return {"applied": failure is None, "problems": [failure] if failure else [], "executed": executed,
            "undo": list(reversed(undo)), "next": "scan --baseline <the scan.json taken before this plan>"}


def _find_all(text: str, needle: str) -> list[int]:
    positions, start = [], text.find(needle)
    while start >= 0 and needle:
        positions.append(start)
        start = text.find(needle, start + len(needle))
    return positions


def _q(path: str) -> str:
    return "'" + path.replace("'", "'\\''") + "'"


def _git(root: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(root), *args], check=True, capture_output=True)


def _move(root: Path, row: dict[str, Any]) -> None:
    source, target = row["from"], row["to"]
    if not row.get("tracked"):
        os.rename(root / source, root / target)
        return
    if source.casefold() == target.casefold():
        interim = f"{target}.doc-tidy-rename"
        _git(root, "mv", "--", source, interim)
        _git(root, "mv", "--", interim, target)
        return
    _git(root, "mv", "--", source, target)


# ---------------------------------------------------------------------------
# plans: Claude Code plan files near deletion, and references to deleted ones
# ---------------------------------------------------------------------------

DEFAULT_RETENTION_DAYS = 30
MANAGED_SETTINGS = {
    "darwin": Path("/Library/Application Support/ClaudeCode/managed-settings.json"),
    "linux": Path("/etc/claude-code/managed-settings.json"),
}
RETENTION_CAVEAT = (
    "Claude Code deletes top-level plan files whose modification time is older than cleanupPeriodDays during its "
    "startup housekeeping. A session whose merged settings set a shorter period deletes sooner, a custom "
    "plansDirectory is never swept, and a value below 1 fails settings validation, which skips the cleanup. "
    "Re-check after upgrading Claude Code."
)
PLAN_PATH_REF = re.compile(
    r"(?:(?:~|\$HOME|\$\{HOME\}|/(?:Users|home)/[^/\s`'\"]+)/\.claude/plans/|\$\{?CLAUDE_CONFIG_DIR\}?/plans/"
    r"|(?<![\w./-])\.claude/plans/)(?P<slug>[A-Za-z0-9][A-Za-z0-9._-]*?)\.md\b")
BARE_PLAN_REF = re.compile(r"(?<![\w./-])plans/(?P<slug>[A-Za-z0-9][A-Za-z0-9._-]*?)\.md\b")
REF_EXTENSIONS = (".md", ".json", ".sh", ".py", ".txt", ".yml", ".yaml")
REF_DIR_PATTERN = re.compile(r"^(?:agents|commands|skills|output-styles)(?:-[a-z]{2,3})?$")
REF_SKIP_DIRS = frozenset({"synced", "__pycache__", "node_modules", ".git"})
MAX_REF_BYTES = 5 * 1024 * 1024


def resolve_config_dir(value: Path | None) -> Path:
    if value:
        return value.expanduser().resolve()
    env = os.environ.get("CLAUDE_CONFIG_DIR")
    return Path(env).expanduser().resolve() if env else (Path.home() / ".claude").resolve()


def resolve_retention(config: Path, projects: list[Path], override: int | None) -> dict[str, Any]:
    candidates: list[Path] = []
    managed = MANAGED_SETTINGS.get(sys.platform)
    if managed:
        candidates.append(managed)
    for project in projects:
        candidates += [project / ".claude" / "settings.local.json", project / ".claude" / "settings.json"]
    candidates.append(config / "settings.json")
    sources = []
    days, source = None, "default"
    for path in candidates:
        if not path.is_file():
            continue
        entry: dict[str, Any] = {"file": str(path), "cleanupPeriodDays": None, "plansDirectory": None, "error": None}
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            entry["error"] = f"cannot parse: {exc}"
            sources.append(entry)
            continue
        if isinstance(data, dict):
            value = data.get("cleanupPeriodDays")
            if isinstance(value, int) and not isinstance(value, bool):
                entry["cleanupPeriodDays"] = value
            elif value is not None:
                entry["error"] = f"cleanupPeriodDays is not an integer: {value!r}"
            if isinstance(data.get("plansDirectory"), str):
                entry["plansDirectory"] = data["plansDirectory"]
        sources.append(entry)
        if days is None and entry["cleanupPeriodDays"] is not None:
            days, source = entry["cleanupPeriodDays"], str(path)
    if override is not None:
        days, source = override, "--retention-days"
    if days is None:
        days = DEFAULT_RETENTION_DAYS
    return {"days": days, "disabled": days < 1, "source": source, "sources": sources, "caveat": RETENTION_CAVEAT}


def custom_plans_dirs(projects: list[Path], retention: dict[str, Any]) -> list[dict[str, Any]]:
    result = []
    for project in projects:
        for entry in retention["sources"]:
            if entry["plansDirectory"] and entry["file"].startswith(str(project) + os.sep):
                path = (project / entry["plansDirectory"]).resolve()
                result.append({"project": str(project), "setting": entry["plansDirectory"], "path": str(path),
                               "withinProject": path == project or project in path.parents, "swept": False})
    return result


def config_ref_files(config: Path) -> list[tuple[Path, str]]:
    """The config files a durable reference can live in - never transcripts."""
    files: list[tuple[Path, str]] = []
    if not config.is_dir():
        return files
    for entry in sorted(config.iterdir()):
        if entry.is_file() and (entry.suffix == ".md" or entry.name == "settings.json"):
            files.append((entry, "config"))
        elif entry.is_dir() and (REF_DIR_PATTERN.match(entry.name) or entry.name == "scripts"):
            files.extend((path, "config") for path in _walk_ref_files(entry))
    plans = config / "plans"
    if plans.is_dir():
        for entry in sorted(plans.iterdir()):
            if entry.is_file() and entry.suffix == ".md":
                files.append((entry, "plan"))
            elif entry.is_dir():
                files.extend((path, "template") for path in _walk_ref_files(entry))
    projects = config / "projects"
    if projects.is_dir():
        for project in sorted(projects.iterdir()):
            memory = project / "memory"
            if memory.is_dir():
                files.extend((path, "memory") for path in _walk_ref_files(memory) if path.suffix == ".md")
    return files


def _walk_ref_files(directory: Path) -> list[Path]:
    found = []
    for dirpath, dirnames, filenames in os.walk(directory):
        dirnames[:] = sorted(d for d in dirnames if d not in REF_SKIP_DIRS)
        for name in sorted(filenames):
            path = Path(dirpath) / name
            if path.suffix in REF_EXTENSIONS:
                try:
                    if path.stat().st_size <= MAX_REF_BYTES:
                        found.append(path)
                except OSError:
                    continue
    return found


def repo_ref_files(root: Path) -> list[tuple[Path, str]]:
    """Agent config inside one repository, or inside every non-fork repository under a root."""
    root = root.expanduser().resolve()
    if (root / ".git").exists():
        return _agent_config_files(root)
    files = [(path, "refs-root") for path in sorted(root.glob("*.md")) if path.is_file()]
    if root.is_dir():
        for child in sorted(root.iterdir()):
            if child.is_dir() and (child / ".git").exists() and not is_fork(child):
                files.extend(_agent_config_files(child))
    return files


def _agent_config_files(repo_root: Path) -> list[tuple[Path, str]]:
    files = [(repo_root / name, "refs-root") for name in ("CLAUDE.md", "AGENTS.md") if (repo_root / name).is_file()]
    claude = repo_root / ".claude"
    if claude.is_dir():
        files.extend((path, "refs-root") for path in _walk_ref_files(claude))
    return files


def fence_lines(lines: list[str]) -> set[int]:
    inside: set[int] = set()
    fence: tuple[str, int] | None = None
    for number, line in enumerate(lines, 1):
        match = FENCE_OPEN.match(line)
        if fence is None:
            if match:
                fence = (match.group(1)[0], len(match.group(1)))
                inside.add(number)
        else:
            inside.add(number)
            if match and match.group(1)[0] == fence[0] and len(match.group(1)) >= fence[1] and not match.group(2).strip():
                fence = None
    return inside


def find_plan_refs(files: list[tuple[Path, str]], config: Path, ruleset: RuleSet,
                   live_slugs: list[str]) -> tuple[list[dict[str, Any]], dict[str, int]]:
    placeholders = ruleset.active("plan-placeholder")
    slug_pattern = None
    wordy = sorted((s for s in live_slugs if s.count("-") >= 2), key=len, reverse=True)
    if wordy:
        slug_pattern = re.compile(r"(?<![\w-])(" + "|".join(map(re.escape, wordy)) + r")(?![\w-])")
    refs: list[dict[str, Any]] = []
    scanned = {"files": 0, "bytes": 0, "placeholders": 0}
    seen: set[Path] = set()
    for path, source in files:
        if path in seen:
            continue
        seen.add(path)
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        scanned["files"] += 1
        scanned["bytes"] += len(text.encode("utf-8"))
        lines = split_lines(text)
        fenced = fence_lines(lines) if path.suffix == ".md" else set()
        inside_config = config == path.parent or config in path.parents
        own_slug = path.stem if source == "plan" else None
        for number, line in enumerate(lines, 1):
            spans: list[tuple[int, int]] = []
            matches = list(PLAN_PATH_REF.finditer(line))
            if inside_config:
                matches += [m for m in BARE_PLAN_REF.finditer(line)
                            if not any(m.start() >= o.start() and m.end() <= o.end() for o in matches)]
            for match in matches:
                slug = match.group("slug")
                spans.append((match.start(), match.end()))
                if any(rule.regex.search(slug) for rule in placeholders):
                    scanned["placeholders"] += 1
                    continue
                if slug == own_slug:
                    continue
                refs.append({"slug": slug, "file": str(path), "line": number, "col": match.start(),
                             "text": line.strip()[:200], "inFence": number in fenced, "source": source, "form": "path"})
            if slug_pattern:
                for match in slug_pattern.finditer(line):
                    if match.group(1) == own_slug or any(s <= match.start() < e for s, e in spans):
                        continue
                    refs.append({"slug": match.group(1), "file": str(path), "line": number, "col": match.start(),
                                 "text": line.strip()[:200], "inFence": number in fenced, "source": source,
                                 "form": "slug"})
    return refs, scanned


def recoverable_plans(specs: list[str]) -> dict[str, dict[str, Any]]:
    found: dict[str, dict[str, Any]] = {}
    for spec in specs:
        repo_part, subdir = spec, "plans"
        if ":" in spec and not Path(spec).exists():
            repo_part, subdir = spec.rsplit(":", 1)
        repo_path = Path(repo_part).expanduser().resolve()
        output = run_git(repo_path, "log", "--diff-filter=D", "--name-only", "-z", "--format=%x1e%H%x1f%as",
                         "--", subdir)
        if not output:
            continue
        prefix = subdir.rstrip("/") + "/"
        for chunk in output.decode("utf-8", "surrogateescape").split("\x1e"):
            header, _, rest = chunk.partition("\0")
            sha, _, date = header.partition("\x1f")
            for rel in rest.lstrip("\n").split("\0"):
                if not rel.startswith(prefix) or "/" in rel[len(prefix):] or not rel.endswith(".md"):
                    continue
                slug = rel[len(prefix):-3]
                found.setdefault(slug, {"repo": str(repo_path), "commit": sha, "date": date.strip(),
                                        "show": f"{sha}^:{rel}"})
    return found


def loose_doc_record(path: Path, ruleset: RuleSet, repo_names: list[str], now: dt.date) -> dict[str, Any]:
    text = path.read_text(encoding="utf-8", errors="replace")
    stat = path.stat()
    parsed = parse_markdown(text)
    doc = Doc(path=path.name, text=text, parsed=parsed, bytes=stat.st_size, mtime=stat.st_mtime, tracked=None, dirty=False)
    mentions = []
    for name in repo_names:
        path_hits = len(re.findall(rf"/{re.escape(name)}(?=[/`\s)]|$)", text, re.MULTILINE))
        token_hits = len(re.findall(rf"(?<![\w.-]){re.escape(name)}(?=[/`\s)]|$)", text, re.MULTILINE))
        if path_hits or token_hits:
            mentions.append({"repo": name, "pathHits": path_hits, "tokenHits": token_hits})
    mentions.sort(key=lambda m: (-m["pathHits"], -m["tokenHits"], m["repo"]))
    return {
        "path": str(path), "bytes": stat.st_size, "lines": parsed.lines,
        "mtime": dt.datetime.fromtimestamp(stat.st_mtime).isoformat(timespec="seconds"),
        "ageDays": max((now - local_date(stat.st_mtime)).days, 0),
        "title": parsed.title["text"] if parsed.title else None,
        "rules": rule_hits(ruleset, doc), "status": status_signals(ruleset, parsed),
        "mentionsRepos": mentions[:5], "deleteRisk": "no-git",
    }


def workspace_report(root: Path, ruleset: RuleSet, now: dt.date, excludes: list[str]) -> dict[str, Any]:
    root = root.expanduser().resolve()
    repos, non_git, loose = [], [], []
    for child in sorted(root.iterdir()) if root.is_dir() else []:
        if child.name.startswith(".") or child.name in SKIP_DIRS:
            continue
        if child.is_file() and is_doc_path(child.name):
            loose.append(child)
        elif child.is_dir() and not any(fnmatch.fnmatchcase(child.name, p) for p in excludes):
            if (child / ".git").exists():
                repos.append(child.name)
            else:
                docs = sum(1 for path in _limited_walk(child, 3) if is_doc_path(path.name))
                if docs:
                    non_git.append({"path": str(child), "docs": docs})
    return {"root": str(root), "repos": len(repos),
            "looseDocs": [loose_doc_record(path, ruleset, repos, now) for path in loose],
            "nonGitDirs": non_git}


def _limited_walk(directory: Path, depth: int) -> Iterable[Path]:
    base = len(directory.parts)
    for dirpath, dirnames, filenames in os.walk(directory):
        current = len(Path(dirpath).parts) - base
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS and not d.startswith(".") and current < depth]
        for name in filenames:
            yield Path(dirpath) / name


def plans_report(args: argparse.Namespace, ruleset: RuleSet) -> dict[str, Any]:
    now = args.now or dt.date.today()
    config = resolve_config_dir(args.config_dir)
    projects = [p.expanduser().resolve() for p in args.project]
    retention = resolve_retention(config, projects, args.retention_days)
    plans_dir = config / "plans"
    warnings: list[str] = []
    if not config.is_dir():
        warnings.append(f"config directory not found: {config}")

    plans = []
    live_slugs = []
    for path in sorted(plans_dir.glob("*.md")) if plans_dir.is_dir() else []:
        stat = path.stat()
        parsed = parse_markdown(path.read_text(encoding="utf-8", errors="replace"))
        modified = local_date(stat.st_mtime)
        expires = None if retention["disabled"] else modified + dt.timedelta(days=retention["days"])
        live_slugs.append(path.stem)
        doc = Doc(path=path.name, text="", parsed=parsed, bytes=stat.st_size, mtime=stat.st_mtime, tracked=None, dirty=False)
        plans.append({
            "path": str(path), "name": path.stem, "title": parsed.title["text"] if parsed.title else None,
            "bytes": stat.st_size, "lines": parsed.lines,
            "mtime": dt.datetime.fromtimestamp(stat.st_mtime).isoformat(timespec="seconds"),
            "ageDays": (now - modified).days, "swept": not retention["disabled"],
            "expiresOn": expires.isoformat() if expires else None,
            "daysLeft": (expires - now).days if expires else None,
            "status": status_signals(ruleset, parsed), "sections": parsed.sections[:60],
            "refs": {"durable": 0, "fromPlans": 0, "items": []}, "rules": rule_hits(ruleset, doc),
        })
    templates = sorted(p for p in plans_dir.glob("*/**/*.md")) if plans_dir.is_dir() else []

    files = config_ref_files(config)
    for root in args.refs_root:
        files.extend(repo_ref_files(root))
    refs, scanned = find_plan_refs(files, config, ruleset, live_slugs)
    by_slug = {plan["name"]: plan for plan in plans}
    dangling: dict[str, dict[str, Any]] = {}
    recoverable = recoverable_plans(args.recover_from) if args.recover_from else {}
    template_refs: Counter[str] = Counter()
    for ref in refs:
        if ref["source"] == "template":
            template_refs[ref["file"]] += 1
        plan = by_slug.get(ref["slug"])
        if plan:
            bucket = "fromPlans" if ref["source"] == "plan" else "durable"
            plan["refs"][bucket] += 1
            if len(plan["refs"]["items"]) < 30:
                plan["refs"]["items"].append({k: ref[k] for k in ("file", "line", "form", "inFence", "source")})
        elif ref["form"] == "path":
            entry = dangling.setdefault(ref["slug"], {"slug": ref["slug"], "expected": str(plans_dir / f"{ref['slug']}.md"),
                                                      "refs": [], "recoverable": recoverable.get(ref["slug"])})
            entry["refs"].append({k: ref[k] for k in ("file", "line", "col", "text", "inFence", "source")})

    findings: list[dict[str, Any]] = []
    for slug, entry in sorted(dangling.items()):
        files_named = sorted({r["file"] for r in entry["refs"]})
        findings.append(finding(
            "dangling-plan-ref", "critical", entry["expected"],
            f"{len(entry['refs'])} reference(s) to a plan that no longer exists",
            {"slug": slug, "refs": entry["refs"][:40], "recoverable": entry["recoverable"]},
            paths=files_named, suggest="relink"))
    for plan in plans:
        if plan["daysLeft"] is not None and plan["daysLeft"] <= args.expiring_days:
            severity = "critical" if plan["refs"]["durable"] else "warning"
            findings.append(finding(
                "plan-expiring", severity, plan["path"],
                f"deleted at the next housekeeping run on or after {plan['expiresOn']} unless modified",
                {"daysLeft": plan["daysLeft"], "expiresOn": plan["expiresOn"], "refs": plan["refs"],
                 "lean": plan["status"]["lean"], "title": plan["title"]}, suggest="extract"))
        elif plan["refs"]["durable"] and plan["swept"]:
            findings.append(finding(
                "plan-referenced", "warning", plan["path"],
                f"durable files point at a plan that expires on {plan['expiresOn']}",
                {"daysLeft": plan["daysLeft"], "refs": plan["refs"], "title": plan["title"]}, suggest="extract"))
    workspaces = [workspace_report(root, ruleset, now, []) for root in args.workspace]
    for workspace in workspaces:
        for doc in workspace["looseDocs"]:
            findings.append(finding(
                "loose-doc", "warning", doc["path"], "a document in a workspace root, outside every repository",
                {"mentionsRepos": doc["mentionsRepos"], "rules": doc["rules"], "status": doc["status"],
                 "ageDays": doc["ageDays"], "title": doc["title"]}, suggest="extract", deleteRisk="no-git"))
    findings = sort_findings(findings)
    report = envelope("plans", ruleset, {"expiringDays": args.expiring_days}, now, warnings)
    report.update({
        "configDir": str(config),
        "plansDir": {"path": str(plans_dir), "exists": plans_dir.is_dir(), "swept": True},
        "customPlansDirs": custom_plans_dirs(projects, retention),
        "retention": retention,
        "lastHousekeeping": _read_optional(config / ".last-cleanup"),
        "plans": plans,
        "templates": [{"path": str(p), "refsOut": template_refs[str(p)]} for p in templates],
        "dangling": sorted(dangling.values(), key=lambda e: e["slug"]),
        "refsScanned": {"roots": [str(config), *map(str, args.refs_root)], **scanned},
        "workspaces": workspaces,
        "summary": summarize(findings),
        "findings": findings,
    })
    return report


def _read_optional(path: Path) -> str | None:
    try:
        return path.read_text(encoding="utf-8").strip() or None
    except OSError:
        return None


# ---------------------------------------------------------------------------
# sweep: many repositories, cheaply
# ---------------------------------------------------------------------------

SCORE_WEIGHTS = {"critical": 5, "warning": 2, "suggestion": 0.5}


def sweep_report(args: argparse.Namespace, ruleset: RuleSet) -> dict[str, Any]:
    now = args.now or dt.date.today()
    options = ScanOptions(history=False, similarity=False, now=now)
    roots = []
    ranking = []
    for raw_root in args.roots:
        root = raw_root.expanduser().resolve()
        if not root.is_dir():
            raise UsageError(f"not a directory: {raw_root}")
        entry: dict[str, Any] = {"root": str(root), "repos": [], "skipped": [], "nonGitDirs": [], "looseDocs": []}
        children = [root] if (root / ".git").exists() else sorted(root.iterdir())
        for child in children:
            if child != root:
                if not child.is_dir():
                    continue
                if child.name.startswith("."):
                    entry["skipped"].append({"path": str(child), "reason": "hidden"})
                    continue
                if child.name in SKIP_DIRS:
                    entry["skipped"].append({"path": str(child), "reason": "skip-dir"})
                    continue
                if any(fnmatch.fnmatchcase(child.name, pattern) for pattern in args.exclude):
                    entry["skipped"].append({"path": str(child), "reason": "exclude"})
                    continue
                if not (child / ".git").exists():
                    docs = sum(1 for path in _limited_walk(child, 3) if is_doc_path(path.name))
                    if docs:
                        entry["nonGitDirs"].append({"path": str(child), "docs": docs})
                    continue
                if not args.include_forks and is_fork(child):
                    entry["skipped"].append({"path": str(child), "reason": "fork"})
                    continue
            repo = analyze(child, ruleset, options)
            findings = collect_findings(repo, options)
            severity = Counter(item["severity"] for item in findings)
            score = sum(SCORE_WEIGHTS[s] * severity[s] for s in SEVERITIES)
            entry["repos"].append({
                "path": str(repo.root), "name": repo.root.name, "kind": repo.kind, "shallow": repo.shallow,
                "docs": len(repo.docs), "untracked": sum(1 for d in repo.docs.values() if d.tracked is False),
                "dirty": sum(1 for d in repo.docs.values() if d.dirty),
                "byClass": dict(sorted(Counter(d.cls for d in repo.docs.values()).items())),
                "severity": {s: severity[s] for s in SEVERITIES},
                "byCode": dict(sorted(Counter(item["code"] for item in findings).items())),
                "archive": {"preferred": repo.archive.get("preferred"), "needsAsk": repo.archive.get("needsAsk")},
                "top": [{"code": item["code"], "path": item["path"]} for item in findings[:3]],
                "score": score,
            })
            ranking.append({"path": str(repo.root), "score": score})
        if not (root / ".git").exists():
            loose = [p for p in sorted(root.iterdir()) if p.is_file() and is_doc_path(p.name)]
            names = [Path(r["path"]).name for r in entry["repos"]]
            entry["looseDocs"] = [loose_doc_record(p, ruleset, names, now) for p in loose]
        roots.append(entry)
    ranking.sort(key=lambda item: (-item["score"], item["path"]))
    if args.top:
        ranking = ranking[: args.top]
    report = envelope("sweep", ruleset, {"excludes": args.exclude, "includeForks": args.include_forks,
                                         "scoreWeights": SCORE_WEIGHTS}, now, [])
    report.update({"roots": roots, "ranking": ranking})
    return report


# ---------------------------------------------------------------------------
# Output
# ---------------------------------------------------------------------------

def dump_json(payload: Any, pretty: bool) -> str:
    if pretty:
        return json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":")) + "\n"


def format_scan(report: dict[str, Any]) -> str:
    out = [f"doc-tidy scan: {report['root']}"]
    inventory = report["inventory"]
    classes = ", ".join(f"{name} {count}" for name, count in inventory["byClass"].items())
    out.append(f"  {inventory['docs']} documents ({classes}); {inventory['untracked']} untracked")
    archive = report["archive"]
    if archive.get("preferred"):
        out.append(f"  archive: {archive['preferred']} ({archive['layout']})")
    elif archive.get("dirs"):
        out.append(f"  archive: needs a decision ({archive['reason']}): " + ", ".join(d["path"] for d in archive["dirs"]))
    for warning in report["warnings"]:
        out.append(f"  warning: {warning}")
    summary = report["summary"]
    out.append(f"  {summary['critical']} critical, {summary['warning']} warning, {summary['suggestion']} suggestion")
    if "delta" in report:
        delta = report["delta"]
        out.append(f"  since baseline: {len(delta['newBroken'])} new broken link(s), "
                   f"{delta['resolvedBroken']} resolved, {len(delta['splitPairs'])} split pair(s)")
    shown: Counter[str] = Counter()
    for item in report["findings"]:
        shown[item["code"]] += 1
        if shown[item["code"]] > TEXT_LINES_PER_CODE:
            continue
        risk = " [UNTRACKED — delete is permanent]" if item.get("deleteRisk") == "untracked" else ""
        out.append(f"{SEVERITY_MARK[item['severity']]} {item['code']} · {item['path']} — {item['message']}{risk}")
        for link in (item["evidence"].get("links") or [])[:5]:
            if "raw" in link:
                hint = f" ({link['hint']}: {link.get('candidate', '')})" if link.get("hint") else ""
                out.append(f"     L{link['line']}: {link['raw']}{hint}")
    for code, count in shown.items():
        if count > TEXT_LINES_PER_CODE:
            out.append(f"   … {count - TEXT_LINES_PER_CODE} more {code}")
    return "\n".join(out)


def format_relink(report: dict[str, Any]) -> str:
    out = [f"doc-tidy relink: {report['root']}"]
    for refusal in report["refusals"]:
        out.append(f"🔴 refused {refusal['op']} {refusal['from']} -> {refusal['to']}: {refusal['reason']} ({refusal['detail']})")
    for row in report["operations"]:
        destination = f" -> {row['to']}" if row["to"] else (f" (merged into {row['mergedInto']})" if row["mergedInto"] else "")
        expanded = f"  [{row['expandedFrom']}]" if row["expandedFrom"] else ""
        out.append(f"  {row['via']} {row['from']}{destination}{expanded}")
    out.append(f"  {len(report['edits'])} edit(s), {len(report['manual'])} manual, {len(report['breaks'])} broken by deletion, "
               f"{len(report['textRefs'])} plain-text reference(s)")
    for edit in report["edits"]:
        times = f" ×{edit['count']}" if edit["replaceAll"] else ""
        out.append(f"     {edit['file']}:{edit['lines'][0]}  {edit['old']}  ->  {edit['new']}{times}")
    for item in report["manual"]:
        out.append(f"🟡 manual {item['file']}:{item['line']}  {item['old']}  ->  {item['new']}  ({item['why']})")
    for item in report["breaks"]:
        out.append(f"🟡 breaks {item['file']}:{item['line']}  {item['raw']}  (keep the text: {item['unlinked']!r})")
    for item in report["textRefs"]:
        out.append(f"🟡 text   {item['file']}:{item['line']}  {item['text']}")
    for item in report["opWarnings"]:
        out.append(f"   note {item['path']}: {item['reason']} - {item['detail']}")
    return "\n".join(out)


def format_apply(result: dict[str, Any]) -> str:
    if not result["applied"] and not result.get("executed"):
        return "doc-tidy apply: nothing changed\n" + "\n".join(f"🔴 {problem}" for problem in result["problems"])
    out = ["doc-tidy apply: " + ("done" if result["applied"] else "STOPPED PART-WAY")]
    out.extend(f"🔴 {problem}" for problem in result["problems"])
    for row in result["executed"]:
        if row["op"] == "edit":
            out.append(f"  edited {row['file']} ({row['edits']} substitution(s))")
        else:
            out.append(f"  {row['op']} {row['from']}" + (f" -> {row['to']}" if row.get("to") else ""))
    out.append("  undo, in order:")
    out.extend(f"    {command}" for command in result["undo"])
    out.append(f"  next: {result['next']}")
    return "\n".join(out)


def format_plans(report: dict[str, Any]) -> str:
    retention = report["retention"]
    days = "disabled" if retention["disabled"] else f"{retention['days']} days"
    out = [f"doc-tidy plans: {report['plansDir']['path']} (retention {days}, from {retention['source']})"]
    for plan in sorted(report["plans"], key=lambda p: (p["daysLeft"] if p["daysLeft"] is not None else 10 ** 6, p["name"])):
        left = "-" if plan["daysLeft"] is None else f"{plan['daysLeft']}d"
        out.append(f"  {left:>5}  {plan['name']}  [{plan['status']['lean']}]  refs {plan['refs']['durable']}"
                   f"  {plan['title'] or ''}")
    summary = report["summary"]
    out.append(f"  {summary['critical']} critical, {summary['warning']} warning, {summary['suggestion']} suggestion")
    for item in report["findings"]:
        out.append(f"{SEVERITY_MARK[item['severity']]} {item['code']} · {item['path']} — {item['message']}")
        if item["code"] == "dangling-plan-ref":
            for ref in item["evidence"]["refs"][:5]:
                out.append(f"     {ref['file']}:{ref['line']}")
            recoverable = item["evidence"]["recoverable"]
            if recoverable:
                out.append(f"     recoverable: git -C {recoverable['repo']} show {recoverable['show']}")
    return "\n".join(out)


def format_sweep(report: dict[str, Any]) -> str:
    out = ["doc-tidy sweep"]
    repos = {repo["path"]: repo for root in report["roots"] for repo in root["repos"]}
    for item in report["ranking"]:
        repo = repos[item["path"]]
        severity = repo["severity"]
        codes = ", ".join(f"{code} {count}" for code, count in repo["byCode"].items())
        out.append(f"  {item['score']:>6}  {repo['name']}  ({repo['docs']} docs; {severity['critical']}/"
                   f"{severity['warning']}/{severity['suggestion']})  {codes}")
    for root in report["roots"]:
        skipped = Counter(entry["reason"] for entry in root["skipped"])
        if skipped:
            out.append(f"  skipped under {root['root']}: " + ", ".join(f"{r} {n}" for r, n in sorted(skipped.items())))
        for doc in root["looseDocs"]:
            out.append(f"🟡 loose-doc · {doc['path']}")
    return "\n".join(out)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def parse_date(value: str) -> dt.date:
    try:
        return dt.date.fromisoformat(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(f"expected YYYY-MM-DD, got {value!r}") from exc


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="doctidy.py", description=__doc__.splitlines()[0])
    parser.add_argument("--check-rules", action="store_true", help="validate the rules and exit")
    parser.add_argument("--rules", type=Path, default=RULES_DIR, help=argparse.SUPPRESS)

    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--json", action="store_true", help="machine-readable output")
    common.add_argument("--pretty", action="store_true", help="indent JSON output")
    common.add_argument("--now", type=parse_date, default=None, help="fix today's date (YYYY-MM-DD)")
    common.add_argument("--rules", type=Path, default=argparse.SUPPRESS, help=argparse.SUPPRESS)

    sub = parser.add_subparsers(dest="command")
    scan = sub.add_parser("scan", parents=[common], help="inventory one repository")
    scan.add_argument("path", nargs="?", default=".", type=Path)
    scan.add_argument("--out", type=Path, help="write scan.json and batch-NN.json into this directory")
    scan.add_argument("--baseline", type=Path, help="compare with an earlier scan.json")
    scan.add_argument("--strict", action="store_true", help="exit 1 on any critical finding")
    scan.add_argument("--docs", choices=("slim", "full", "none"), default="slim")
    scan.add_argument("--exclude", action="append", default=[], metavar="GLOB")
    scan.add_argument("--no-history", action="store_true")
    scan.add_argument("--no-similarity", action="store_true")
    scan.add_argument("--drift-days", type=int, default=60)
    scan.add_argument("--bulk-md", type=int, default=20)
    scan.add_argument("--split-kb", type=int, default=30)
    scan.add_argument("--similarity", type=float, default=0.80)
    scan.add_argument("--overlap", type=float, default=0.50)

    sweep = sub.add_parser("sweep", parents=[common], help="rank every repository under one or more roots")
    sweep.add_argument("roots", nargs="+", type=Path)
    sweep.add_argument("--exclude", action="append", default=[], metavar="GLOB", help="skip child directories by name")
    sweep.add_argument("--include-forks", action="store_true")
    sweep.add_argument("--top", type=int, default=0)
    sweep.add_argument("--save", type=Path, metavar="FILE", help="also write the JSON report here")

    plans = sub.add_parser("plans", parents=[common], help="Claude Code plans near deletion, and references to deleted ones")
    plans.add_argument("--config-dir", type=Path)
    plans.add_argument("--project", action="append", default=[], type=Path)
    plans.add_argument("--refs-root", action="append", default=[], type=Path)
    plans.add_argument("--workspace", action="append", default=[], type=Path)
    plans.add_argument("--recover-from", action="append", default=[], metavar="REPO[:SUBDIR]")
    plans.add_argument("--retention-days", type=int)
    plans.add_argument("--expiring-days", type=int, default=7)
    plans.add_argument("--strict", action="store_true", help="exit 1 on any critical finding")
    plans.add_argument("--save", type=Path, metavar="FILE", help="also write the JSON report here")

    relink = sub.add_parser("relink", parents=[common], help="compute the link edits a move, archive, merge or delete needs")
    relink.add_argument("path", nargs="?", default=".", type=Path)
    relink.add_argument("--from", dest="from_paths", action="append", default=[], metavar="SRC")
    relink.add_argument("--to", dest="to_paths", action="append", default=[], metavar="DST")
    relink.add_argument("--archive", action="append", default=[], metavar="SRC")
    relink.add_argument("--archive-dir", metavar="DIR")
    relink.add_argument("--merge", action="append", default=[], metavar="SRC:DST")
    relink.add_argument("--delete", action="append", default=[], metavar="SRC")
    relink.add_argument("--no-pair", action="store_true", help="do not move a document's language partners with it")
    relink.add_argument("--exclude", action="append", default=[], metavar="GLOB")
    relink.add_argument("--save", type=Path, metavar="FILE", help="also write the JSON plan here (outside the repository)")

    apply = sub.add_parser("apply", parents=[common], help="apply an approved relink result - the only command that writes")
    apply.add_argument("--plan", type=Path, required=True)
    apply.add_argument("--allow-staged", action="store_true")
    apply.add_argument("--allow-manual", action="store_true")
    return parser


def cmd_scan(args: argparse.Namespace, ruleset: RuleSet) -> int:
    now = args.now or dt.date.today()
    options = ScanOptions(
        excludes=args.exclude, history=not args.no_history, similarity=not args.no_similarity,
        drift_days=args.drift_days, bulk_md=args.bulk_md, split_kb=args.split_kb,
        similarity_threshold=args.similarity, overlap_threshold=args.overlap, now=now,
    )
    target = args.path.resolve()
    if not target.exists():
        raise UsageError(f"path not found: {args.path}")
    repo = analyze(target if target.is_dir() else target.parent, ruleset, options)
    focus = None
    if target != repo.root:
        try:
            focus = target.relative_to(repo.root).as_posix()
        except ValueError:
            focus = None
    findings = collect_findings(repo, options)
    report = scan_report(repo, ruleset, options, findings, focus, args.docs)
    if args.baseline:
        try:
            baseline = json.loads(args.baseline.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise UsageError(f"cannot read baseline {args.baseline}: {exc}") from exc
        report["delta"] = baseline_delta(report, baseline)
    if args.out:
        write_run(repo, report, args.out, options)
    print(dump_json(report, args.pretty) if args.json else format_scan(report), end="" if args.json else "\n")
    if "delta" in report and (report["delta"]["newBroken"] or report["delta"]["splitPairs"]):
        return EXIT_FINDINGS
    if args.strict and report["summary"]["critical"]:
        return EXIT_FINDINGS
    return EXIT_OK


def emit(args: argparse.Namespace, report: dict[str, Any], formatter, forbidden_root: Path | None = None) -> None:
    """Print the report, and write its JSON to ``--save`` when given."""
    save = getattr(args, "save", None)
    if save:
        target = save.expanduser().resolve()
        if forbidden_root and (target == forbidden_root or forbidden_root in target.parents):
            raise UsageError(f"--save must be outside the repository: {target}")
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(dump_json(report, pretty=True), encoding="utf-8")
    if args.json:
        print(dump_json(report, args.pretty), end="")
    else:
        print(formatter(report))


def cmd_sweep(args: argparse.Namespace, ruleset: RuleSet) -> int:
    emit(args, sweep_report(args, ruleset), format_sweep)
    return EXIT_OK


def cmd_plans(args: argparse.Namespace, ruleset: RuleSet) -> int:
    report = plans_report(args, ruleset)
    emit(args, report, format_plans)
    return EXIT_FINDINGS if args.strict and report["summary"]["critical"] else EXIT_OK


def cmd_relink(args: argparse.Namespace, ruleset: RuleSet) -> int:
    now = args.now or dt.date.today()
    if not (args.from_paths or args.to_paths or args.archive or args.merge or args.delete):
        raise UsageError("give at least one of --from/--to, --archive, --merge or --delete")
    target = args.path.resolve()
    if not target.is_dir():
        raise UsageError(f"not a directory: {args.path}")
    repo = analyze(target, ruleset, ScanOptions(excludes=args.exclude, history=False, similarity=False, now=now))
    operations, refusals = expand_operations(repo, args)
    accepted = check_operations(repo, operations, refusals)
    report = relink_report(repo, ruleset, accepted, refusals, now)
    emit(args, report, format_relink, forbidden_root=repo.root)
    return EXIT_FINDINGS if refusals else EXIT_OK


def cmd_apply(args: argparse.Namespace, ruleset: RuleSet) -> int:
    try:
        plan = json.loads(args.plan.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise UsageError(f"cannot read plan {args.plan}: {exc}") from exc
    result = apply_plan(plan, args.allow_staged, args.allow_manual)
    result.update({"tool": TOOL, "schemaVersion": SCHEMA_VERSION, "command": "apply"})
    emit(args, result, format_apply)
    return EXIT_OK if result["applied"] else EXIT_FINDINGS


COMMANDS = {"scan": cmd_scan, "sweep": cmd_sweep, "plans": cmd_plans, "relink": cmd_relink, "apply": cmd_apply}


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        ruleset, problems = load_rules(args.rules)
    except RulesError as exc:
        print(f"doctidy.py: {exc}", file=sys.stderr)
        return EXIT_USAGE
    if args.check_rules:
        if problems:
            print("\n".join(problems))
            return EXIT_FINDINGS
        overrides = f" ({len(ruleset.overridden)} local overrides)" if ruleset.local else ""
        print(f"rules OK: {len(ruleset.rules)} rows{overrides}")
        return EXIT_OK
    if not args.command:
        parser.print_usage(sys.stderr)
        print("doctidy.py: choose a command, or --check-rules", file=sys.stderr)
        return EXIT_USAGE
    if problems:
        print("doctidy.py: rules have problems, run --check-rules:", file=sys.stderr)
        print("\n".join(problems), file=sys.stderr)
        return EXIT_USAGE
    try:
        return COMMANDS[args.command](args, ruleset)
    except UsageError as exc:
        print(f"doctidy.py: {exc}", file=sys.stderr)
        return EXIT_USAGE


if __name__ == "__main__":
    sys.exit(main())
