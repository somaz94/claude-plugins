#!/usr/bin/env python3
"""Candidate scanner for the Korean-naturalness reviewer.

Reads the lexicon that ships next to this script (``../lexicon/*.tsv``),
extracts Korean prose from Markdown, bilingual YAML (the ``ko:`` half only)
or plain text, and reports two kinds of candidates:

  Pass 1  token-pattern hits from ``patterns.tsv``
  Pass 2  one referent spelled several ways - seeded from ``terms.tsv``,
          plus unseeded discovery of English words written in more than
          one letter case

Every hit is a candidate, not a finding: the reviewer confirms each one by
reading the line in context. Standard library only.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass
from pathlib import Path

LEXICON_DIR = Path(__file__).resolve().parent.parent / "lexicon"

PATTERN_COLUMNS = ("id", "severity", "category", "regex", "min_count", "example", "why", "suggestion")
PATTERN_OPTIONAL_COLUMNS = ("counterexample",)
COUNTEREXAMPLE_SEPARATOR = "|"
TERM_COLUMNS = ("canonical", "variants", "kind", "note")
SEVERITIES = ("red", "yellow", "green")
TERM_KINDS = ("name", "k8s-kind", "concept", "keep")

EXIT_OK = 0
EXIT_LEXICON_PROBLEMS = 1
EXIT_USAGE = 2

HANGUL = "가-힣"
HANGUL_CHAR = re.compile(f"[{HANGUL}]")
LATIN_TOKEN = re.compile(r"(?<![A-Za-z0-9_-])[A-Za-z][A-Za-z0-9]*(?:-[A-Za-z0-9]+)*(?![A-Za-z0-9_])")
MIN_CASE_TOKEN_LEN = 3

# Markdown / inline markup that is content rather than prose. Each match is
# blanked with spaces so line and column positions survive.
FENCE = re.compile(r"^\s{0,3}(`{3,}|~{3,})")
CODE_TAG = re.compile(r"<code\b[^>]*>.*?</code>", re.IGNORECASE)
INLINE_CODE = re.compile(r"(`+)(?!`).*?(?<!`)\1(?!`)")
HTML_TAG = re.compile(r"</?[A-Za-z][^<>]*>")
LINK_TARGET = re.compile(r"(?<=\])\([^()\s]*(?:\s+\"[^\"]*\")?\)")
BARE_URL = re.compile(r"\b(?:https?|ftp)://[^\s<>)\]]+")

# A token counts as sitting at a label start when only list / heading /
# quote / table markers and an opening emphasis precede it on the line.
LABEL_PREFIX = re.compile(
    r"\s*(?:(?:[#>|]+|[-*+]|\d+[.)])\s*)*(?:\*\*|__|<strong>|<b>|[\"'])?\s*",
    re.IGNORECASE,
)

YAML_KEY_BLOCK = re.compile(r"^(?P<indent>\s*(?:-\s+)?)(?P<key>[A-Za-z0-9_.-]+):\s*[|>][-+0-9]*\s*(?:#.*)?$")
YAML_KO_VALUE = re.compile(
    r"""(?<![A-Za-z0-9_-])ko:[ \t]*
        (?:
            "(?P<dq>(?:[^"\\]|\\.)*)"
          | '(?P<sq>(?:[^']|'')*)'
          | (?P<plain>[^\s"'{}\[\],#|>][^{}\[\],#]*?)
        )
        [ \t]*(?=[,}\]]|\#|$)""",
    re.VERBOSE,
)


class LexiconError(Exception):
    """Raised when the lexicon cannot be loaded at all."""


RAW_TARGET_MARK = "(?#raw)"
FILE_SCOPE_PREFIX = "file:"


@dataclass(frozen=True)
class Pattern:
    """One lexicon row.

    A regex starting with ``(?#raw)`` runs against the line with its markup
    still in place (for spacing after ``</code>`` and the like); every other
    regex runs against the line with markup blanked out.

    ``min_count`` is counted per paragraph (a tic repeated in one place), or
    across the whole file when written ``file:N`` (a habit spread through a
    document).

    ``counterexamples`` are strings the regex must not match at all: the
    natural Korean a narrowed regex was narrowed to leave alone.
    """

    id: str
    severity: str
    category: str
    regex: re.Pattern[str]
    min_count: int
    per_file: bool
    example: str
    counterexamples: tuple[str, ...]
    why: str
    suggestion: str
    source: str

    @property
    def on_raw(self) -> bool:
        return self.regex.pattern.startswith(RAW_TARGET_MARK)


@dataclass(frozen=True)
class Term:
    canonical: str
    surfaces: tuple[str, ...]
    kind: str
    note: str
    source: str


@dataclass(frozen=True)
class ProseLine:
    """One line of prose. ``raw`` keeps markup, ``clean`` has it blanked.

    Both strings are the same length so a column in one is a column in the
    other; everything that is not prose is already spaces in both. ``block``
    identifies the paragraph the line belongs to, the unit ``min_count``
    is counted in.
    """

    lineno: int
    raw: str
    clean: str
    block: int


# ---------------------------------------------------------------------------
# Lexicon
# ---------------------------------------------------------------------------


def read_tsv(
    path: Path, columns: tuple[str, ...], problems: list[str], optional: tuple[str, ...] = ()
) -> list[tuple[int, dict[str, str]]]:
    """Read a TSV whose first non-comment line is the header ``columns``.

    Trailing ``optional`` columns may be left out of the header, so a file
    written before they existed still reads. A row may also leave out the
    optional cells its header declares; every missing cell reads as ``""``.
    """
    rows: list[tuple[int, dict[str, str]]] = []
    header: tuple[str, ...] | None = None
    accepted = [columns + optional[:n] for n in range(len(optional) + 1)]
    for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        cells = line.split("\t")
        if header is None:
            if tuple(cells) not in accepted:
                shape = "<TAB>".join(columns) + "".join(f"[<TAB>{name}]" for name in optional)
                problems.append(f"{path.name}:{lineno}: header must be {shape}")
                return rows
            header = tuple(cells)
            continue
        if not len(columns) <= len(cells) <= len(header):
            expected = f"{len(columns)}" if len(header) == len(columns) else f"{len(columns)} to {len(header)}"
            problems.append(f"{path.name}:{lineno}: expected {expected} columns, got {len(cells)}")
            continue
        row = dict.fromkeys(columns + optional, "")
        row.update(zip(header, (c.strip() for c in cells)))
        rows.append((lineno, row))
    if header is None:
        problems.append(f"{path.name}: missing header row")
    return rows


def parse_patterns(path: Path, problems: list[str]) -> dict[str, Pattern]:
    patterns: dict[str, Pattern] = {}
    for lineno, row in read_tsv(path, PATTERN_COLUMNS, problems, PATTERN_OPTIONAL_COLUMNS):
        where = f"{path.name}:{lineno}"
        pid = row["id"]
        if not pid:
            problems.append(f"{where}: empty id")
            continue
        if pid in patterns:
            problems.append(f"{where}: duplicate id {pid!r}")
            continue
        if row["severity"] not in SEVERITIES:
            problems.append(f"{where}: severity {row['severity']!r} not in {SEVERITIES}")
            continue
        per_file = row["min_count"].startswith(FILE_SCOPE_PREFIX)
        count_text = row["min_count"][len(FILE_SCOPE_PREFIX):] if per_file else row["min_count"]
        try:
            min_count = int(count_text)
        except ValueError:
            problems.append(f"{where}: min_count {row['min_count']!r} is not N or file:N")
            continue
        if min_count < 1:
            problems.append(f"{where}: min_count must be >= 1")
            continue
        try:
            regex = re.compile(row["regex"])
        except re.error as exc:
            problems.append(f"{where}: regex does not compile: {exc}")
            continue
        if not row["example"]:
            problems.append(f"{where}: {pid} has no example")
            continue
        if not regex.search(row["example"]):
            problems.append(f"{where}: {pid} example {row['example']!r} does not match its regex")
            continue
        counterexamples = tuple(
            text.strip() for text in row["counterexample"].split(COUNTEREXAMPLE_SEPARATOR) if text.strip()
        )
        # Checked against what the scanner would see: markup blanked unless the row reads the raw line.
        on_raw = row["regex"].startswith(RAW_TARGET_MARK)
        matched = [text for text in counterexamples if regex.search(text if on_raw else strip_markup(text))]
        if matched:
            problems.append(f"{where}: {pid} counterexample {matched[0]!r} matches its regex")
            continue
        patterns[pid] = Pattern(
            id=pid,
            severity=row["severity"],
            category=row["category"],
            regex=regex,
            min_count=min_count,
            per_file=per_file,
            example=row["example"],
            counterexamples=counterexamples,
            why=row["why"],
            suggestion=row["suggestion"],
            source=path.name,
        )
    return patterns


def parse_terms(path: Path, problems: list[str]) -> dict[str, Term]:
    terms: dict[str, Term] = {}
    for lineno, row in read_tsv(path, TERM_COLUMNS, problems):
        where = f"{path.name}:{lineno}"
        canonical = row["canonical"]
        if not canonical:
            problems.append(f"{where}: empty canonical")
            continue
        if canonical in terms:
            problems.append(f"{where}: duplicate canonical {canonical!r}")
            continue
        if row["kind"] not in TERM_KINDS:
            problems.append(f"{where}: kind {row['kind']!r} not in {TERM_KINDS}")
            continue
        surfaces = [canonical]
        for variant in row["variants"].split("|"):
            variant = variant.strip()
            if variant and variant not in surfaces:
                surfaces.append(variant)
        terms[canonical] = Term(canonical, tuple(surfaces), row["kind"], row["note"], path.name)
    return terms


def load_lexicon(directory: Path) -> tuple[list[Pattern], list[Term], list[str]]:
    """Load base TSVs plus optional ``*.local.tsv`` overlays.

    An overlay row whose id (patterns) or canonical (terms) already exists
    replaces the base row; any other overlay row is appended.
    """
    problems: list[str] = []
    patterns_path = directory / "patterns.tsv"
    terms_path = directory / "terms.tsv"
    for required in (patterns_path, terms_path):
        if not required.is_file():
            raise LexiconError(f"lexicon file not found: {required}")

    patterns = parse_patterns(patterns_path, problems)
    terms = parse_terms(terms_path, problems)

    local_patterns = directory / "patterns.local.tsv"
    if local_patterns.is_file():
        patterns.update(parse_patterns(local_patterns, problems))
    local_terms = directory / "terms.local.tsv"
    if local_terms.is_file():
        terms.update(parse_terms(local_terms, problems))

    pattern_list = list(patterns.values())
    term_list = list(terms.values())
    problems.extend(cross_check(pattern_list, term_list))
    return pattern_list, term_list, problems


def cross_check(patterns: list[Pattern], terms: list[Term]) -> list[str]:
    """Checks that span both files."""
    problems: list[str] = []
    owner: dict[str, str] = {}
    for term in terms:
        for surface in term.surfaces:
            key = surface.casefold()
            if key in owner and owner[key] != term.canonical:
                problems.append(
                    f"{term.source}: surface {surface!r} is claimed by both {owner[key]!r} and {term.canonical!r}"
                )
            owner.setdefault(key, term.canonical)
    # A term kept as a deliberate English label must never also be flagged as
    # an untranslated word: the two lists would contradict each other.
    for term in terms:
        if term.kind != "keep":
            continue
        for surface in term.surfaces:
            for pattern in patterns:
                if pattern.regex.search(surface):
                    problems.append(
                        f"{term.source}: keep term {surface!r} is flagged by pattern {pattern.id!r} ({pattern.source})"
                    )
    return problems


# ---------------------------------------------------------------------------
# Prose extraction
# ---------------------------------------------------------------------------


def _blank(text: str, span: tuple[int, int]) -> str:
    start, end = span
    return text[:start] + " " * (end - start) + text[end:]


def strip_markup(raw: str) -> str:
    """Blank inline code, code tags, HTML tags, link targets and bare URLs."""
    clean = raw
    for regex in (CODE_TAG, INLINE_CODE, LINK_TARGET, BARE_URL, HTML_TAG):
        for match in regex.finditer(clean):
            clean = _blank(clean, match.span())
    return clean


def extract_markdown(text: str) -> list[ProseLine]:
    lines = text.splitlines()
    out: list[ProseLine] = []
    index = 0
    if lines and lines[0].strip() == "---":
        for end in range(1, len(lines)):
            if lines[end].strip() in ("---", "..."):
                index = end + 1
                break
    fence: str | None = None
    block = 0
    for lineno in range(index + 1, len(lines) + 1):
        line = lines[lineno - 1]
        opener = FENCE.match(line)
        if fence is not None:
            if opener and opener.group(1)[0] == fence[0] and len(opener.group(1)) >= len(fence):
                fence = None
                block += 1
            continue
        if opener:
            fence = opener.group(1)
            block += 1
            continue
        if not line.strip():
            block += 1
            continue
        out.append(ProseLine(lineno, line, strip_markup(line), block))
    return out


def extract_yaml(text: str) -> list[ProseLine]:
    """Return only the ``ko:`` values, positioned where they sit in the file."""
    lines = text.splitlines()
    out: list[ProseLine] = []
    lineno = 0
    paragraph = 0
    while lineno < len(lines):
        line = lines[lineno]
        key_block = YAML_KEY_BLOCK.match(line)
        if key_block:
            key_column = len(key_block.group("indent"))
            emit = key_block.group("key") == "ko"
            paragraph += 1
            lineno += 1
            while lineno < len(lines):
                body = lines[lineno]
                if body.strip() and len(body) - len(body.lstrip()) <= key_column:
                    break
                if not body.strip():
                    paragraph += 1
                elif emit:
                    out.append(ProseLine(lineno + 1, body, strip_markup(body), paragraph))
                lineno += 1
            continue
        masked = " " * len(line)
        found = False
        for match in YAML_KO_VALUE.finditer(line):
            group = next(g for g in ("dq", "sq", "plain") if match.group(g) is not None)
            start, end = match.span(group)
            masked = masked[:start] + line[start:end] + masked[end:]
            found = True
        if found:
            paragraph += 1
            out.append(ProseLine(lineno + 1, masked, strip_markup(masked), paragraph))
        lineno += 1
    return out


def extract_text(text: str) -> list[ProseLine]:
    out: list[ProseLine] = []
    block = 0
    for lineno, line in enumerate(text.splitlines(), 1):
        if not line.strip():
            block += 1
            continue
        out.append(ProseLine(lineno, line, strip_markup(line), block))
    return out


EXTRACTORS = {"md": extract_markdown, "yaml": extract_yaml, "text": extract_text}


def detect_lane(path: Path) -> str:
    suffix = path.suffix.lower()
    if suffix in (".yml", ".yaml"):
        return "yaml"
    if suffix in (".md", ".markdown"):
        return "md"
    return "text"


# ---------------------------------------------------------------------------
# Passes
# ---------------------------------------------------------------------------


def scan_patterns(lines: list[ProseLine], patterns: list[Pattern]) -> list[dict]:
    results = []
    for pattern in patterns:
        per_block: dict[int, list[dict]] = {}
        for line in lines:
            for m in pattern.regex.finditer(line.raw if pattern.on_raw else line.clean):
                if m.group(0):
                    per_block.setdefault(-1 if pattern.per_file else line.block, []).append(
                        {"line": line.lineno, "col": m.start() + 1, "match": m.group(0)}
                    )
        occurrences = [o for found in per_block.values() if len(found) >= pattern.min_count for o in found]
        if occurrences:
            results.append(
                {
                    "id": pattern.id,
                    "severity": pattern.severity,
                    "category": pattern.category,
                    "count": len(occurrences),
                    "min_count": pattern.min_count,
                    "count_scope": "file" if pattern.per_file else "paragraph",
                    "why": pattern.why,
                    "suggestion": pattern.suggestion,
                    "occurrences": occurrences,
                }
            )
    order = {s: i for i, s in enumerate(SEVERITIES)}
    results.sort(key=lambda r: (order[r["severity"]], r["id"]))
    return results


def _is_latin(surface: str) -> bool:
    return bool(re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9 ./_-]*", surface))


def surface_regex(surface: str) -> re.Pattern[str]:
    """Latin surfaces match case-insensitively with an optional plural so the
    actual spelling in the text can be bucketed; Hangul surfaces match as-is."""
    escaped = re.escape(surface)
    if _is_latin(surface):
        return re.compile(rf"(?<![A-Za-z0-9_-]){escaped}(?:e?s)?(?![A-Za-z0-9_-])", re.IGNORECASE)
    return re.compile(rf"(?<![{HANGUL}]){escaped}")


def _singular(form: str, surface: str) -> str:
    if len(form) > len(surface) and form[: len(surface)].casefold() == surface.casefold():
        return form[: len(surface)]
    return form


def scan_terms(lines: list[ProseLine], terms: list[Term]) -> list[dict]:
    results = []
    for term in terms:
        forms: dict[str, list[int]] = {}
        taken: dict[int, list[tuple[int, int]]] = {}
        # Longest surface first so `Blue-Green` is not also counted as `Blue`.
        for surface in sorted(term.surfaces, key=len, reverse=True):
            regex = surface_regex(surface)
            for line in lines:
                spans = taken.setdefault(line.lineno, [])
                for m in regex.finditer(line.clean):
                    if any(m.start() < end and start < m.end() for start, end in spans):
                        continue
                    spans.append(m.span())
                    forms.setdefault(_singular(m.group(0), surface), []).append(line.lineno)
        if len(forms) >= 2:
            results.append(
                {
                    "canonical": term.canonical,
                    "kind": term.kind,
                    "note": term.note,
                    "forms": [
                        {"form": form, "count": len(found), "lines": found}
                        for form, found in sorted(forms.items(), key=lambda kv: (-len(kv[1]), kv[0]))
                    ],
                }
            )
    return results


def is_label_start(raw: str, column: int) -> bool:
    return LABEL_PREFIX.fullmatch(raw[:column]) is not None


def scan_case_variants(lines: list[ProseLine], terms: list[Term]) -> list[dict]:
    seeded = {s.casefold() for t in terms for s in t.surfaces if _is_latin(s)}
    groups: dict[str, dict[str, dict]] = {}
    for line in lines:
        tokens = list(LATIN_TOKEN.finditer(line.clean))
        for index, m in enumerate(tokens):
            token = m.group(0)
            key = token.casefold()
            if len(token) < MIN_CASE_TOKEN_LEN or key in seeded:
                continue
            entry = groups.setdefault(key, {}).setdefault(
                token, {"count": 0, "lines": [], "label_start": 0, "in_phrase": 0}
            )
            entry["count"] += 1
            entry["lines"].append(line.lineno)
            if is_label_start(line.raw, m.start()):
                entry["label_start"] += 1
            neighbours = [tokens[i] for i in (index - 1, index + 1) if 0 <= i < len(tokens)]
            if any(_capitalised(n.group(0)) and _adjacent(line.clean, m, n) for n in neighbours):
                entry["in_phrase"] += 1
    results = []
    for key, forms in sorted(groups.items()):
        if len(forms) < 2:
            continue
        results.append(
            {
                "key": key,
                "hint": _case_hint(forms),
                "forms": [{"form": form, **data} for form, data in sorted(forms.items())],
            }
        )
    return results


def _capitalised(token: str) -> bool:
    return token[0].isupper()


def _adjacent(text: str, a: re.Match[str], b: re.Match[str]) -> bool:
    left, right = sorted((a, b), key=lambda m: m.start())
    return text[left.end():right.start()] == " "


def _case_hint(forms: dict[str, dict]) -> str | None:
    """Why a two-spelling group is probably not drift, or None.

    ``label-capitalization``  the capitalised spelling only opens labels
    ``title-case-phrase``     it only appears inside a run of capitalised
                              words, such as a setting name
    """
    if len(forms) != 2:
        return None
    lower = [f for f in forms if f == f.lower()]
    capital = [f for f in forms if f[0].isupper() and f[1:] == f[1:].lower()]
    if len(lower) != 1 or len(capital) != 1:
        return None
    cap = forms[capital[0]]
    if cap["label_start"] == cap["count"]:
        return "label-capitalization"
    if cap["in_phrase"] == cap["count"]:
        return "title-case-phrase"
    return None


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def select_patterns(patterns: list[Pattern], severity: str | None, categories: set[str]) -> list[Pattern]:
    """Narrow the pattern pass to a severity floor and a set of categories.

    The spelling and letter-case passes are not patterns and are unaffected.
    """
    if severity is not None:
        floor = SEVERITIES.index(severity)
        patterns = [p for p in patterns if SEVERITIES.index(p.severity) <= floor]
    if categories:
        patterns = [p for p in patterns if p.category in categories]
    return patterns


def korean_lines(lines: list[ProseLine]) -> list[ProseLine]:
    """Keep only lines with Korean in their prose.

    A line of pure English inside a Korean document is either a command, a
    code-like table cell, or an English sentence kept on purpose - all out of
    scope for a Korean reviewer, and the main source of case-variant noise.
    """
    return [line for line in lines if HANGUL_CHAR.search(line.clean)]


def scan_text(text: str, lane: str, patterns: list[Pattern], terms: list[Term]) -> dict:
    lines = korean_lines(EXTRACTORS[lane](text))
    return {
        "lane": lane,
        "patterns": scan_patterns(lines, patterns),
        "terms": scan_terms(lines, terms),
        "case_variants": scan_case_variants(lines, terms),
    }


def format_text(report: dict) -> str:
    out: list[str] = []
    for item in report["files"]:
        out.append(f"== {item['path']} ({item['lane']})")
        if not (item["patterns"] or item["terms"] or item["case_variants"]):
            out.append("  (no candidates)")
            continue
        for hit in item["patterns"]:
            lines = ", ".join(str(o["line"]) for o in hit["occurrences"][:12])
            more = " …" if hit["count"] > 12 else ""
            out.append(f"  [{hit['severity']}] {hit['id']} ×{hit['count']}  lines {lines}{more}")
            out.append(f"      why: {hit['why']}")
            out.append(f"      try: {hit['suggestion']}")
        for term in item["terms"]:
            forms = ", ".join(f"{f['form']}×{f['count']}" for f in term["forms"])
            out.append(f"  [spelling] {term['canonical']} ({term['kind']}): {forms}")
        hinted = [g for g in item["case_variants"] if g["hint"]]
        for group in (g for g in item["case_variants"] if not g["hint"]):
            forms = ", ".join(f"{f['form']}×{f['count']}" for f in group["forms"])
            out.append(f"  [case] {forms}")
        for hint in sorted({g["hint"] for g in hinted}):
            out.append(f"  [case, likely {hint}] " + "; ".join(
                "/".join(f["form"] for f in g["forms"]) for g in hinted if g["hint"] == hint
            ))
    return "\n".join(out)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("files", nargs="*", type=Path, help="files to scan")
    parser.add_argument("--lane", choices=("auto", "md", "yaml", "text"), default="auto")
    parser.add_argument("--stdin", action="store_true", help="scan text from standard input")
    parser.add_argument("--json", action="store_true", help="machine-readable output")
    parser.add_argument(
        "--severity",
        choices=SEVERITIES,
        help="report pattern hits at this severity or above (red > yellow > green)",
    )
    parser.add_argument(
        "--category",
        action="append",
        metavar="NAME",
        help="report pattern hits in these categories only; repeat it or separate names with commas",
    )
    parser.add_argument("--check-lexicon", action="store_true", help="validate the lexicon and exit")
    parser.add_argument("--lexicon", type=Path, default=LEXICON_DIR, help=argparse.SUPPRESS)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    try:
        patterns, terms, problems = load_lexicon(args.lexicon)
    except LexiconError as exc:
        print(f"scan.py: {exc}", file=sys.stderr)
        return EXIT_USAGE

    if args.check_lexicon:
        if problems:
            print("\n".join(problems))
            return EXIT_LEXICON_PROBLEMS
        print(f"lexicon OK: {len(patterns)} patterns, {len(terms)} terms")
        return EXIT_OK

    if problems:
        print("scan.py: lexicon has problems, run --check-lexicon:", file=sys.stderr)
        print("\n".join(problems), file=sys.stderr)
        return EXIT_USAGE

    if not args.stdin and not args.files:
        parser.print_usage(sys.stderr)
        print("scan.py: give one or more files, or --stdin", file=sys.stderr)
        return EXIT_USAGE

    categories = {name.strip() for value in args.category or () for name in value.split(",") if name.strip()}
    unknown = categories - {p.category for p in patterns}
    if unknown:
        known = ", ".join(sorted({p.category for p in patterns}))
        print(f"scan.py: unknown category {', '.join(sorted(unknown))}; known: {known}", file=sys.stderr)
        return EXIT_USAGE
    patterns = select_patterns(patterns, args.severity, categories)

    report: dict = {"files": []}
    if args.stdin:
        lane = "text" if args.lane == "auto" else args.lane
        report["files"].append({"path": "<stdin>", **scan_text(sys.stdin.read(), lane, patterns, terms)})
    for path in args.files:
        if not path.is_file():
            print(f"scan.py: not a file: {path}", file=sys.stderr)
            return EXIT_USAGE
        lane = detect_lane(path) if args.lane == "auto" else args.lane
        text = path.read_text(encoding="utf-8")
        report["files"].append({"path": str(path), **scan_text(text, lane, patterns, terms)})

    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        print(format_text(report))
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
