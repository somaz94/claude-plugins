"""Helpers that more than one plugin in this marketplace needs.

Vendored, not imported: a plugin is installed on its own, so nothing outside
its own directory is on disk at runtime. Every copy of this file is therefore
byte-identical, and `tests/consistency.sh` fails the build when they diverge.
That check is the whole point — the reason this file exists is that these jobs
were each written twice by hand, and the copies had already drifted:

  - `parse_frontmatter` disagreed about `''` escaping, so a description written
    the way strict YAML requires (`don''t`) rendered with the escape still in it
    in one tool and correctly in the other.
  - `measure` counted a `# comment` inside a fenced shell block as a heading in
    one tool and not the other, so a README with a code sample looked like it
    had drifted from its translation when it had not.

Edit the canonical copy — `plugins/census/scripts/_shared.py` — then run
`bash tests/sync-shared.sh` to propagate it to the others.

stdlib only, like everything else here: a tool that audits configuration must
not need an install step before it can be trusted.
"""

from __future__ import annotations

import re
from typing import NamedTuple

# Rough chars-per-token ratio for English prose. Used only for order-of-magnitude
# context-budget reporting, never for anything that must be exact, and always
# labelled as an estimate where it is shown.
CHARS_PER_TOKEN = 4

# A hook command word ending in one of these is a script even when it is written
# without a directory, which is what separates `guard.sh` from `jq`.
SCRIPT_SUFFIXES = (".sh", ".bash", ".zsh", ".py", ".js", ".mjs", ".ts", ".rb", ".pl")


# --------------------------------------------------------------------------
# frontmatter
# --------------------------------------------------------------------------

_FM_DELIM = re.compile(r"^---\s*$")
_FM_KEY = re.compile(r"^([A-Za-z0-9_-]+):\s*(.*)$")


def unquote_scalar(value: str) -> str:
    """Strip a YAML scalar's surrounding quotes and undo its escaping.

    A `description` is quoted on disk whenever it contains a `: ` or a leading
    indicator character, which is most of them. Undoing the escape matters as
    much as removing the quotes: a single-quoted scalar doubles its apostrophes,
    so `don''t` on disk is `don't` in the document, and a reader that only strips
    the outer quotes shows the escape to a human as if it were text.
    """
    value = value.strip()
    if len(value) >= 2 and value[0] == value[-1]:
        if value[0] == "'":
            return value[1:-1].replace("''", "'")
        if value[0] == '"':
            return re.sub(r"\\(.)", r"\1", value[1:-1])
    return value


def parse_frontmatter(text: str) -> tuple[dict[str, str], int]:
    """Parse a YAML-ish frontmatter block into (fields, body_offset).

    Deliberately a subset parser, not a YAML implementation: the stdlib has no
    YAML and pulling a dependency would break the zero-install contract. It
    handles what Claude Code frontmatter actually uses — `key: value` with
    optional quotes, indented continuation lines, and inline `[a, b]` lists.

    `body_offset` is the character index at which the body starts, so a caller
    that wants the body slices for it and a caller that only wants its length
    subtracts. A file without a block yields ({}, 0).

    An unindented line with no `key:` is folded into the previous value rather
    than dropped. It is not valid YAML, but Claude Code's own reader is tolerant
    there, and an audit that silently discards text under-reports what a session
    actually loads.
    """
    lines = text.splitlines(keepends=True)
    if not lines or not _FM_DELIM.match(lines[0].rstrip("\n")):
        return {}, 0

    fields: dict[str, str] = {}
    key: str | None = None
    consumed = len(lines[0])

    for line in lines[1:]:
        consumed += len(line)
        stripped = line.rstrip("\n")
        if _FM_DELIM.match(stripped):
            break
        match = _FM_KEY.match(stripped)
        # An INDENTED `key: value` is a nested mapping, which belongs to the key
        # above it rather than being one of its own.
        if match and not stripped.startswith((" ", "\t")):
            key = match.group(1)
            fields[key] = unquote_scalar(match.group(2))
        elif key and stripped.strip():
            # Continuation of the previous value (folded multi-line string or a
            # block list item). Joined with a space so length metrics stay honest.
            fields[key] = (fields[key] + " " + stripped.strip()).strip()

    return fields, consumed


# --------------------------------------------------------------------------
# document shape
# --------------------------------------------------------------------------


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

    @property
    def code_blocks(self) -> int:
        return self.fences // 2

    def comparable(self) -> dict[str, int]:
        """The subset worth comparing between two halves of a pair, labelled for
        a human to read in a finding."""
        return {
            "headings": self.headings,
            "code blocks": self.code_blocks,
            "table rows": self.table_rows,
            "list items": self.list_items,
            "links": len(self.links),
        }


def measure(text: str) -> Metrics:
    """Read a document's shape.

    Fenced regions are tracked because everything inside one is a sample, not
    structure: a `# comment` in a shell block is not a heading, and a `|` in a
    table of example output is not a table row. Counting them is how a README
    that merely documents its own CLI comes out looking structurally different
    from a faithful translation of itself.
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
