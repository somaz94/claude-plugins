#!/usr/bin/env python3
"""doc-mirror — keeps translated documentation pairs from drifting apart.

A repository that ships `README.md` alongside `README-ko.md` has made a promise
it has no way to keep. Editing one half and not the other passes every check
there is: the build is green, the linter is quiet, the diff looks deliberate,
and the review sees one file. The mirror rots in place, and the reader who
picked the other language gets last quarter's instructions.

What this reports is STRUCTURE, never meaning:

  - a document in a directory that keeps mirrors, with no mirror of its own
  - a mirror whose source has been deleted or renamed out from under it
  - a pair whose shape has diverged — headings, code blocks, tables, links
  - a relative link that resolves in one half and not the other

Deliberately NOT here:
  - Translation quality. Whether the Korean says what the English says is a
    human judgment, and a tool that guesses at it is worse than no tool.
  - Which half is stale in TIME. Two files with identical shape can still be out
    of date, and answering that needs commit history — `census drift` does it.
  - Writing anything. This reads and reports.

Design contract, shared with the rest of this marketplace:
  - stdlib only. A tool you reach for to check a repo must not need a setup.
  - The convention is DISCOVERED, not configured. A repo that pairs on
    `-ko.md` and a repo that pairs on `.ja.md` both work untouched, and a
    directory that keeps no mirrors is never told it is missing them.

Subcommands:
  check  human-readable report, grouped by severity
  scan   the same findings as JSON, for piping somewhere else
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any, Iterable, NamedTuple

# A mirror suffix: `README-ko.md`, `guide.ja.md`. Two or three lowercase letters,
# optionally with a region (`pt-br`, `zh-hans`). No list of known languages —
# the next one someone invents should work without an edit here.
LANG = r"[a-z]{2,3}(?:-[a-z]{2,4})?"
SUFFIX_PATTERNS = (
    re.compile(rf"^(?P<stem>.+)-(?P<lang>{LANG})$"),
    re.compile(rf"^(?P<stem>.+)\.(?P<lang>{LANG})$"),
)

# Directories that are never documentation, and whose contents would drown any
# real finding. `node_modules` alone can carry thousands of README translations.
SKIP_DIRS = {
    ".git", "node_modules", "vendor", "venv", ".venv", "__pycache__",
    "dist", "build", "target", ".next", ".tox", "site-packages",
}

# Documents that live beside a README and are almost never translated, because
# a machine writes them. Reporting a missing `RELEASE-ko.md` next to a
# `README-ko.md` is the fastest way to teach someone to ignore this tool.
GENERATED_DOCS = {
    "release", "changelog", "contributors", "authors", "license", "licence",
    "code_of_conduct", "notice", "security", "third_party_notices",
}

# Instructions addressed to a coding agent, not documentation addressed to a
# reader. They sit in the same directory as the README and are essentially never
# translated, because nobody reads them for comprehension.
AGENT_DOCS = {"claude", "agents", "agent"}

# Below this many documents, "most of this directory is mirrored" is not a
# statement about anything. One out of two is 50% and means nothing.
EVIDENCE_FLOOR = 3

# How far two halves of a pair may diverge on a structural metric before it is
# worth a human's attention. Below this, prose simply differs in length between
# languages; above it, a section is usually missing.
DRIFT_TOLERANCE = 0.30

# A directory keeping mirrors does not mean every file in it is meant to be
# translated. When most documents here are mirrored, one that is not is very
# likely an oversight; when only a couple are, it is likely deliberate. The
# report says which case it is rather than treating both as the same finding.
STRONG_EVIDENCE = 0.5

# A metric only means something once there is enough of it. Two headings vs
# three is a 50% divergence and says nothing at all.
DRIFT_FLOOR = 4

SEVERITY_ORDER = ("critical", "warning", "suggestion")


class Metrics(NamedTuple):
    """The shape of a Markdown file, with prose deliberately excluded.

    Every one of these survives translation unchanged: a heading is still a
    heading in Korean, a table still has the same number of rows, a fenced block
    still holds the same command. Word counts and character counts do not
    survive, which is exactly why they are not here.
    """

    headings: int
    heading_levels: tuple[int, ...]
    fences: int
    table_rows: int
    list_items: int
    links: list[str]
    spacers: int
    headings_needing_spacer: list[tuple[int, str]]

    def comparable(self) -> dict[str, int]:
        return {
            "headings": self.headings,
            "code blocks": self.fences // 2,
            "table rows": self.table_rows,
            "list items": self.list_items,
            "links": len(self.links),
        }


def measure(text: str) -> Metrics:
    """Read a document's shape.

    Fenced regions are tracked because everything inside one is a sample, not
    structure: a `# comment` in a shell block is not a heading, and a `|` in a
    table of example output is not a table row.
    """
    headings = 0
    levels: list[int] = []
    fences = 0
    table_rows = 0
    list_items = 0
    links: list[str] = []
    spacers = 0
    unspaced: list[tuple[int, str]] = []

    lines = text.splitlines()
    in_fence = False
    for index, line in enumerate(lines):
        if re.match(r"^\s*(```|~~~)", line):
            fences += 1
            in_fence = not in_fence
            continue
        if in_fence:
            continue

        stripped = line.strip()
        if stripped == "<br/>" or stripped == "<br>":
            spacers += 1
            continue

        heading = re.match(r"^(#{1,6})\s+(.*)$", line)
        if heading:
            headings += 1
            levels.append(len(heading.group(1)))
            # Only a section heading needs a spacer above it. The document's
            # own title has nothing to be separated from.
            if len(heading.group(1)) >= 2 and index > 0:
                previous = next(
                    (l.strip() for l in reversed(lines[:index]) if l.strip()), ""
                )
                if previous not in ("<br/>", "<br>"):
                    unspaced.append((index + 1, stripped))
            continue

        if stripped.startswith("|"):
            table_rows += 1
        if re.match(r"^\s*([-*+]|\d+[.)])\s+\S", line):
            list_items += 1
        links.extend(re.findall(r"\[[^\]\n]*\]\(([^)\s]+)", line))

    return Metrics(
        headings=headings,
        heading_levels=tuple(levels),
        fences=fences,
        table_rows=table_rows,
        list_items=list_items,
        links=links,
        spacers=spacers,
        headings_needing_spacer=unspaced,
    )


def split_suffix(stem: str) -> tuple[str, str] | None:
    """Split `README-ko` into ("README", "ko"). None when there is no suffix.

    A two-letter tail is not proof of a language: `docker-py`, `setup-ci` and
    `README-v2` all match the shape. The caller resolves the ambiguity by
    requiring that the un-suffixed file actually exist beside it — a mirror with
    nothing to mirror is not a mirror.
    """
    for pattern in SUFFIX_PATTERNS:
        match = pattern.match(stem)
        if match:
            return match.group("stem"), match.group("lang")
    return None


class Pair(NamedTuple):
    source: Path
    mirrors: dict[str, Path]


def discover(root: Path) -> tuple[dict[Path, Pair], dict[Path, set[str]], list[Path]]:
    """Find every source document, its mirrors, and what each directory does.

    Returns (pairs, languages-per-directory, orphans). The middle value is the
    load-bearing one: a document is only missing a Korean mirror if the
    directory it lives in keeps Korean mirrors at all. Without that, every repo
    on earth is missing every translation, and the report is noise.
    """
    by_dir: dict[Path, dict[str, Path]] = {}
    for path in sorted(root.rglob("*.md")):
        if any(part in SKIP_DIRS for part in path.relative_to(root).parts):
            continue
        by_dir.setdefault(path.parent, {})[path.stem] = path

    pairs: dict[Path, Pair] = {}
    languages: dict[Path, set[str]] = {}

    # Pass one: only a suffix with a source sitting beside it proves a language.
    # This is what separates `README-ko.md` from `06-followup-fluent-bit.md`,
    # whose tail is three lowercase letters and means nothing.
    for directory, stems in by_dir.items():
        for stem, path in sorted(stems.items()):
            split = split_suffix(stem)
            if split is None:
                continue
            base, lang = split
            source = stems.get(base)
            if source is None:
                continue
            languages.setdefault(directory, set()).add(lang)
            pair = pairs.setdefault(source, Pair(source=source, mirrors={}))
            pair.mirrors[lang] = path

    # Pass two: now that the repo's languages are known, a suffix in that set
    # with no source beside it is a mirror whose source went away. A suffix
    # outside the set is just a file name.
    known = {lang for langs in languages.values() for lang in langs}
    orphans: list[Path] = []
    for directory, stems in by_dir.items():
        for stem, path in sorted(stems.items()):
            split = split_suffix(stem)
            if split and split[1] in known and split[0] not in stems:
                orphans.append(path)

    # Every document in a directory that keeps mirrors, including the ones with
    # no mirror yet — those are the whole point of the report.
    for directory in languages:
        for stem, path in by_dir[directory].items():
            split = split_suffix(stem)
            if split and split[0] in by_dir[directory]:
                continue  # this file IS a mirror of a source sitting beside it
            pairs.setdefault(path, Pair(source=path, mirrors={}))

    return pairs, languages, orphans


def relative_link_target(link: str) -> str | None:
    """The file a relative Markdown link points at, or None if it is not one."""
    if re.match(r"^[a-z][a-z0-9+.-]*:", link, re.I):
        return None  # http:, mailto:, tel:
    if link.startswith("#"):
        return None  # same-document anchor
    return link.split("#", 1)[0] or None


def finding(severity: str, code: str, path: Path, root: Path, message: str, **extra) -> dict:
    return {
        "severity": severity,
        "code": code,
        "file": path.relative_to(root).as_posix(),
        "message": message,
        **extra,
    }


def check_pair(
    pair: Pair,
    langs: set[str],
    root: Path,
    require_spacer: bool,
    coverage: dict[tuple[Path, str], tuple[int, int]],
) -> list[dict]:
    """Everything that can be said about one source document and its mirrors."""
    out: list[dict] = []
    source_text = pair.source.read_text(encoding="utf-8", errors="replace")
    source_metrics = measure(source_text)

    for lang in sorted(langs - set(pair.mirrors)):
        if pair.source.stem.lower() in GENERATED_DOCS | AGENT_DOCS:
            continue
        have, total = coverage.get((pair.source.parent, lang), (0, 0))
        strong = total >= EVIDENCE_FLOOR and have / total >= STRONG_EVIDENCE
        out.append(
            finding(
                "critical" if strong else "warning",
                "missing-mirror", pair.source, root,
                f"no {lang} mirror — {have} of {total} documents in this "
                f"directory have one"
                + ("" if strong else ", so this may be deliberate"),
                lang=lang,
            )
        )

    for lang, mirror in sorted(pair.mirrors.items()):
        mirror_metrics = measure(mirror.read_text(encoding="utf-8", errors="replace"))
        drifted = []
        for name, source_value in source_metrics.comparable().items():
            mirror_value = mirror_metrics.comparable()[name]
            if max(source_value, mirror_value) < DRIFT_FLOOR:
                continue
            spread = abs(source_value - mirror_value) / max(source_value, mirror_value, 1)
            if spread >= DRIFT_TOLERANCE:
                drifted.append(f"{name} {source_value} vs {mirror_value}")
        if drifted:
            out.append(
                finding(
                    "warning", "structural-drift", mirror, root,
                    "shape has diverged from its source — " + ", ".join(drifted),
                    lang=lang, source=pair.source.relative_to(root).as_posix(),
                )
            )
        elif source_metrics.heading_levels != mirror_metrics.heading_levels:
            # Same counts, different outline: a section moved or changed depth.
            # Worth saying, but quieter than a count that does not match.
            out.append(
                finding(
                    "suggestion", "outline-drift", mirror, root,
                    "same number of headings as its source but in a different "
                    "order or depth",
                    lang=lang, source=pair.source.relative_to(root).as_posix(),
                )
            )

    targets = [(pair.source, source_metrics)] + [
        (m, measure(m.read_text(encoding="utf-8", errors="replace")))
        for m in pair.mirrors.values()
    ]
    for path, metrics in targets:
        for link in metrics.links:
            target = relative_link_target(link)
            if target is None:
                continue
            if target.startswith("~"):
                # `[the agent](~/.claude/agents/x.md)` means "a path on your
                # machine". No renderer resolves it — it looks for a directory
                # literally named `~` — but it is a deliberate idiom rather than
                # a mistake, so it is said quietly and named for what it is.
                out.append(
                    finding(
                        "suggestion", "home-path-link", path, root,
                        f"link points at a home path, which no renderer resolves: {link}",
                        link=link,
                    )
                )
            elif not (path.parent / target).exists():
                out.append(
                    finding(
                        "critical", "broken-link", path, root,
                        f"relative link does not resolve: {link}",
                        link=link,
                    )
                )
        if require_spacer:
            for line, heading in metrics.headings_needing_spacer:
                out.append(
                    finding(
                        "suggestion", "missing-spacer", path, root,
                        f"no <br/> spacer above `{heading}`",
                        line=line,
                    )
                )
    return out


def spacer_convention(pairs: Iterable[Pair]) -> bool:
    """Whether this repo puts a `<br/>` between heading sections.

    Another discovered convention rather than a configured one. It is a house
    style, not a rule of Markdown, so it is only enforced where it is already
    being followed — and only once there are enough headings for the answer to
    mean anything.
    """
    spaced = unspaced = 0
    for pair in pairs:
        for path in [pair.source, *pair.mirrors.values()]:
            metrics = measure(path.read_text(encoding="utf-8", errors="replace"))
            section_headings = sum(1 for level in metrics.heading_levels if level >= 2)
            unspaced += len(metrics.headings_needing_spacer)
            spaced += max(section_headings - len(metrics.headings_needing_spacer), 0)
    if spaced + unspaced < 8:
        return False
    return spaced / (spaced + unspaced) >= 0.8


def collect(root: Path, require_spacer: bool | None) -> dict[str, Any]:
    pairs, languages, orphans = discover(root)
    paired = {
        source: pair for source, pair in pairs.items()
        if languages.get(source.parent)
    }
    if require_spacer is None:
        require_spacer = spacer_convention(paired.values())

    # How thoroughly each directory actually mirrors, per language. This is the
    # evidence that decides whether a gap is an oversight or a decision.
    coverage: dict[tuple[Path, str], tuple[int, int]] = {}
    for directory, langs in languages.items():
        here = [p for p in paired.values() if p.source.parent == directory]
        for lang in langs:
            have = sum(1 for p in here if lang in p.mirrors)
            coverage[(directory, lang)] = (have, len(here))

    findings: list[dict] = []
    for orphan in orphans:
        findings.append(
            finding(
                "warning", "orphan-mirror", orphan, root,
                "looks like a mirror but has no source beside it — either the "
                "source was removed, or this suffix was never a language",
            )
        )
    for source, pair in sorted(paired.items()):
        findings.extend(
            check_pair(pair, languages[source.parent], root, require_spacer, coverage)
        )

    findings.sort(key=lambda f: (SEVERITY_ORDER.index(f["severity"]), f["file"]))
    counts = {level: 0 for level in SEVERITY_ORDER}
    for item in findings:
        counts[item["severity"]] += 1

    return {
        "root": str(root),
        "languages": sorted({lang for langs in languages.values() for lang in langs}),
        "directoriesWithMirrors": len(languages),
        "pairs": len(paired),
        "mirrors": sum(len(p.mirrors) for p in paired.values()),
        "spacerConvention": require_spacer,
        "counts": counts,
        "findings": findings,
    }


SEVERITY_MARK = {"critical": "🔴", "warning": "🟡", "suggestion": "🟢"}


def print_report(report: dict[str, Any]) -> None:
    if not report["languages"]:
        print(f"No translated documentation pairs found under {report['root']}.")
        print("Nothing to keep in sync — this repository keeps one language.")
        return

    langs = ", ".join(report["languages"])
    print(f"Root: {report['root']}")
    print(
        f"Pairs: {report['pairs']} documents with {report['mirrors']} mirrors "
        f"({langs}) across {report['directoriesWithMirrors']} directories"
    )
    if report["spacerConvention"]:
        print("Spacer convention: this repo puts <br/> between heading sections")
    print()

    if not report["findings"]:
        print("Every pair is structurally in sync.")
        return

    for severity in SEVERITY_ORDER:
        group = [f for f in report["findings"] if f["severity"] == severity]
        if not group:
            continue
        print(f"{SEVERITY_MARK[severity]} {severity.title()} ({len(group)})")
        for item in group:
            where = f"{item['file']}:{item['line']}" if "line" in item else item["file"]
            print(f"  {where}")
            print(f"    {item['message']}")
        print()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="docmirror",
        description="Report where translated documentation pairs have drifted apart.",
    )
    parser.add_argument("path", nargs="?", default=".", help="directory to check (default: cwd)")
    parser.add_argument(
        "--json", action="store_true", help="emit the findings as JSON instead of a report"
    )
    parser.add_argument(
        "--spacer",
        choices=("auto", "on", "off"),
        default="auto",
        help="check for a <br/> above each section heading (default: only where "
        "the repo already does it)",
    )
    parser.add_argument(
        "--strict",
        action="store_true",
        help="exit non-zero when there is any critical finding, for use in CI",
    )
    args = parser.parse_args(argv)

    root = Path(args.path).expanduser().resolve()
    if not root.is_dir():
        raise SystemExit(f"docmirror: {args.path} is not a directory")

    require = {"auto": None, "on": True, "off": False}[args.spacer]
    report = collect(root, require)

    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        print_report(report)

    if args.strict and report["counts"]["critical"]:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
