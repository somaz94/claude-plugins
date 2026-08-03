#!/usr/bin/env python3
"""census — read-only audit of scattered Claude Code configuration.

Collects agents, commands, skills and hooks from a user-level config directory
(``~/.claude``) plus every project-level ``.claude/`` directory, normalizes them
into a single asset graph, and renders reports from it.

Design contract:
  - This script SCANS and RENDERS. It never judges intent and never writes to
    a scanned tree. The only file it may write is an explicit ``--out`` target.
  - stdlib only. A tool that audits config must not need an install step.

Subcommands:
  scan      emit the normalized asset graph as JSON
  catalog   render a Markdown catalog of everything found
"""

from __future__ import annotations

import argparse
import fnmatch
import json
import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Any, Iterable

# Config file names, searched in this order. The first hit wins outright; the
# files are not merged, so a project-local config is a complete override.
CONFIG_NAMES = (".census.json", "census.json")

# Built-in defaults deliberately contain no personal paths — census must pass
# its own portability check. Anything environment-specific comes from config
# or is derived at runtime.
DEFAULT_CONFIG: dict[str, Any] = {
    # Directories that ARE a Claude config dir (they contain agents/, skills/...).
    "userRoots": ["~/.claude"],
    # Glob patterns of REPO directories whose .claude/ subdir should be scanned.
    "projectRoots": ["."],
    # Repo basename patterns to skip entirely.
    "exclude": [],
    # Skip repos with an `upstream` remote: those ship the upstream maintainers'
    # .claude/, not yours, and would pollute the catalog.
    "excludeOssForks": True,
    # Translation mirror directories, keyed by the directory they mirror.
    "pairs": {"agents": "agents-ko", "commands": "commands-ko", "skills": "skills-ko"},
    # Portability markers. Empty means "derive from the environment".
    "portability": {"markers": []},
}

# Subdirectories collected per config root, and how each is laid out.
FLAT_KINDS = {"agents": "agent", "commands": "command"}
FOLDER_KINDS = {"skills": "skill"}

# Frontmatter keys worth carrying into the asset graph. Everything else in the
# frontmatter is preserved under `extra` so reports can surface unknown keys.
KNOWN_KEYS = (
    "name",
    "description",
    "model",
    "tools",
    "allowed-tools",
    "argument-hint",
    "disable-model-invocation",
)

# Rough chars-per-token ratio for English prose. Used only for order-of-magnitude
# context-budget reporting, never for anything that must be exact.
CHARS_PER_TOKEN = 4


# --------------------------------------------------------------------------
# config
# --------------------------------------------------------------------------


def load_config(explicit: str | None) -> tuple[dict[str, Any], str]:
    """Return (config, origin). Missing keys fall back to DEFAULT_CONFIG."""
    candidates: list[Path] = []
    if explicit:
        candidates.append(Path(explicit).expanduser())
    else:
        for name in CONFIG_NAMES:
            candidates.append(Path.cwd() / name)
        for name in CONFIG_NAMES:
            candidates.append(Path.home() / ".claude" / name)

    for path in candidates:
        if path.is_file():
            with path.open(encoding="utf-8") as fh:
                loaded = json.load(fh)
            merged = {**DEFAULT_CONFIG, **loaded}
            # One level of nesting needs an explicit merge.
            merged["portability"] = {
                **DEFAULT_CONFIG["portability"],
                **loaded.get("portability", {}),
            }
            return merged, str(path)

    if explicit:
        raise SystemExit(f"census: config not found: {explicit}")
    return dict(DEFAULT_CONFIG), "(built-in defaults)"


# --------------------------------------------------------------------------
# frontmatter
# --------------------------------------------------------------------------

_FM_DELIM = re.compile(r"^---\s*$")
_FM_KEY = re.compile(r"^([A-Za-z0-9_-]+):\s*(.*)$")


def _unquote(value: str) -> str:
    value = value.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
        return value[1:-1]
    return value


def parse_frontmatter(text: str) -> tuple[dict[str, str], int]:
    """Parse a YAML-ish frontmatter block.

    Deliberately a subset parser, not a YAML implementation: the stdlib has no
    YAML and pulling a dependency would break the zero-install contract. It
    handles what Claude Code frontmatter actually uses — ``key: value`` with
    optional quotes, indented continuation lines, and inline ``[a, b]`` lists.

    Returns (fields, body_offset) where body_offset is the character index at
    which the body starts. Files without frontmatter yield ({}, 0).
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
        if match and not stripped.startswith((" ", "\t")):
            key = match.group(1)
            fields[key] = _unquote(match.group(2))
        elif key and stripped.strip():
            # Continuation of the previous value (folded multi-line string or a
            # block list item). Join with a space so length metrics stay honest.
            fields[key] = (fields[key] + " " + stripped.strip()).strip()

    return fields, consumed


# --------------------------------------------------------------------------
# discovery
# --------------------------------------------------------------------------


def is_excluded(name: str, patterns: Iterable[str]) -> bool:
    return any(fnmatch.fnmatch(name, pat) for pat in patterns)


def has_upstream_remote(repo: Path) -> bool:
    """True when the repo has an `upstream` remote (the `gh repo fork` shape)."""
    try:
        out = subprocess.run(
            ["git", "-C", str(repo), "remote"],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    return "upstream" in out.stdout.split()


def discover_roots(config: dict[str, Any]) -> tuple[list[dict[str, Any]], list[dict[str, str]]]:
    """Resolve config roots into concrete config directories.

    Returns (roots, skipped) where each root is
    ``{"path": Path, "scope": "global"|"repo", "repo": str|None}``.
    """
    roots: list[dict[str, Any]] = []
    skipped: list[dict[str, str]] = []
    seen: set[Path] = set()

    for pattern in config["userRoots"]:
        for path in sorted(_expand(pattern)):
            if path.is_dir() and path not in seen:
                seen.add(path)
                roots.append({"path": path, "scope": "global", "repo": None})

    for pattern in config["projectRoots"]:
        for repo in sorted(_expand(pattern)):
            claude_dir = repo / ".claude"
            if not claude_dir.is_dir() or claude_dir in seen:
                continue
            name = repo.name
            if is_excluded(name, config["exclude"]):
                skipped.append({"repo": name, "reason": "exclude pattern"})
                continue
            if config["excludeOssForks"] and has_upstream_remote(repo):
                skipped.append({"repo": name, "reason": "external fork (upstream remote)"})
                continue
            seen.add(claude_dir)
            roots.append({"path": claude_dir, "scope": "repo", "repo": name})

    return roots, skipped


def _expand(pattern: str) -> list[Path]:
    """Expand ``~`` and glob metacharacters into concrete paths."""
    expanded = os.path.expanduser(pattern)
    if any(ch in expanded for ch in "*?["):
        base = Path(expanded)
        anchor = Path(base.anchor) if base.is_absolute() else Path.cwd()
        relative = str(base.relative_to(base.anchor)) if base.is_absolute() else expanded
        return [p for p in anchor.glob(relative) if p.is_dir()]
    path = Path(expanded)
    return [path] if path.exists() else []


# --------------------------------------------------------------------------
# collection
# --------------------------------------------------------------------------


def collect_items(roots: list[dict[str, Any]], config: dict[str, Any]) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    pairs = config["pairs"]

    for root in roots:
        base: Path = root["path"]
        origin = root["repo"] or "user"

        for subdir, kind in FLAT_KINDS.items():
            for path in sorted((base / subdir).glob("*.md")):
                item = _read_item(path, kind, root, origin)
                mirror = pairs.get(subdir)
                item["pair"] = _pair_for(base / mirror / path.name) if mirror else None
                items.append(item)

        for subdir, kind in FOLDER_KINDS.items():
            for skill_md in sorted((base / subdir).glob("*/SKILL.md")):
                item = _read_item(skill_md, kind, root, origin, name=skill_md.parent.name)
                mirror = pairs.get(subdir)
                if mirror:
                    item["pair"] = _pair_for(base / mirror / skill_md.parent.name / "SKILL.md")
                else:
                    item["pair"] = None
                items.append(item)

        items.extend(_read_hooks(base / "settings.json", root, origin))

    return items


def _read_item(
    path: Path,
    kind: str,
    root: dict[str, Any],
    origin: str,
    name: str | None = None,
) -> dict[str, Any]:
    text = path.read_text(encoding="utf-8", errors="replace")
    fields, offset = parse_frontmatter(text)
    description = fields.get("description", "")
    declared = fields.get("name", "")
    resolved = name or declared or path.stem

    return {
        "kind": kind,
        "name": resolved,
        "declaredName": declared or None,
        "file": str(path),
        "scope": root["scope"],
        "origin": origin,
        "frontmatter": {k: fields[k] for k in KNOWN_KEYS if k in fields},
        "extraKeys": sorted(k for k in fields if k not in KNOWN_KEYS),
        "descriptionChars": len(description),
        "bodyChars": len(text) - offset,
        "pair": None,
    }


def _pair_for(path: Path) -> dict[str, Any] | None:
    if not path.is_file():
        return {"file": str(path), "exists": False}
    text = path.read_text(encoding="utf-8", errors="replace")
    return {
        "file": str(path),
        "exists": True,
        "headings": len(re.findall(r"^#{1,6} ", text, re.M)),
        "codeBlocks": text.count("```") // 2,
        "tableRows": len(re.findall(r"^\|", text, re.M)),
    }


def _read_hooks(settings: Path, root: dict[str, Any], origin: str) -> list[dict[str, Any]]:
    """Extract hook registrations from a settings.json.

    Hooks carry no frontmatter and no description, so they contribute nothing to
    the always-on context budget — they are cataloged for completeness only.
    """
    if not settings.is_file():
        return []
    try:
        with settings.open(encoding="utf-8") as fh:
            data = json.load(fh)
    except (json.JSONDecodeError, OSError):
        return []

    found: list[dict[str, Any]] = []
    for event, entries in (data.get("hooks") or {}).items():
        for entry in entries:
            for hook in entry.get("hooks", []):
                command = hook.get("command", "")
                found.append(
                    {
                        "kind": "hook",
                        "name": Path(command.split()[0]).name if command else event,
                        "declaredName": None,
                        "file": str(settings),
                        "scope": root["scope"],
                        "origin": origin,
                        "frontmatter": {
                            "event": event,
                            "matcher": entry.get("matcher", "*"),
                        },
                        "extraKeys": [],
                        "descriptionChars": 0,
                        "bodyChars": len(command),
                        "pair": None,
                    }
                )
    return found


# --------------------------------------------------------------------------
# reporting
# --------------------------------------------------------------------------


def build_graph(config: dict[str, Any], origin: str) -> dict[str, Any]:
    roots, skipped = discover_roots(config)
    items = collect_items(roots, config)
    return {
        "configOrigin": origin,
        "roots": [
            {"path": str(r["path"]), "scope": r["scope"], "repo": r["repo"]} for r in roots
        ],
        "skipped": skipped,
        "items": items,
        "stats": summarize(items),
    }


def summarize(items: list[dict[str, Any]]) -> dict[str, Any]:
    """Aggregate counts and the always-on context cost.

    Only ``name`` + ``description`` of agents, commands and skills is resident in
    every session's system prompt; bodies load on invocation. That resident sum
    is the number worth watching, and nothing else surfaces it.
    """
    by_kind: dict[str, int] = {}
    resident = 0
    for item in items:
        by_kind[item["kind"]] = by_kind.get(item["kind"], 0) + 1
        if item["kind"] != "hook":
            resident += item["descriptionChars"] + len(item["name"])

    return {
        "total": len(items),
        "byKind": by_kind,
        "residentChars": resident,
        "residentTokensApprox": resident // CHARS_PER_TOKEN,
    }


def render_catalog(graph: dict[str, Any], top: int) -> str:
    stats = graph["stats"]
    out: list[str] = ["# Claude config census", ""]

    counts = ", ".join(f"{v} {k}s" for k, v in sorted(stats["byKind"].items()))
    out += [
        f"- Config source: `{graph['configOrigin']}`",
        f"- Roots scanned: {len(graph['roots'])}",
        f"- Items: {stats['total']} ({counts})",
        f"- Always-on context: ~{stats['residentTokensApprox']:,} tokens "
        f"({stats['residentChars']:,} chars of name + description)",
        "",
    ]

    if graph["skipped"]:
        out.append("Skipped repos: " + ", ".join(
            f"`{s['repo']}` ({s['reason']})" for s in graph["skipped"]
        ))
        out.append("")

    for scope, title in (("global", "Global"), ("repo", "Repo-scoped")):
        scoped = [i for i in graph["items"] if i["scope"] == scope]
        if not scoped:
            continue
        out += ["<br/>", "", f"## {title}", ""]
        for kind in ("command", "agent", "skill", "hook"):
            group = [i for i in scoped if i["kind"] == kind]
            if not group:
                continue
            out += [f"### {kind.capitalize()}s ({len(group)})", ""]
            out.append("| Name | Origin | Description |")
            out.append("|---|---|---|")
            for item in sorted(group, key=lambda i: (i["origin"], i["name"])):
                desc = item["frontmatter"].get("description", "")
                out.append(
                    f"| `{item['name']}` | {item['origin']} | {_truncate(desc, 110)} |"
                )
            out.append("")

    ranked = sorted(
        (i for i in graph["items"] if i["kind"] != "hook"),
        key=lambda i: i["descriptionChars"],
        reverse=True,
    )[:top]
    if ranked:
        out += ["<br/>", "", f"## Context budget — top {len(ranked)} by description size", ""]
        out.append("| Item | Kind | Chars | ~Tokens |")
        out.append("|---|---|---|---|")
        for item in ranked:
            out.append(
                f"| `{item['name']}` | {item['kind']} | {item['descriptionChars']:,} "
                f"| {item['descriptionChars'] // CHARS_PER_TOKEN:,} |"
            )
        out.append("")

    return "\n".join(out)


def _truncate(text: str, limit: int) -> str:
    flat = " ".join(text.split()).replace("|", "\\|")
    return flat if len(flat) <= limit else flat[: limit - 1] + "…"


# --------------------------------------------------------------------------
# cli
# --------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="census", description=__doc__.splitlines()[0])
    parser.add_argument("--config", help="path to a census config file")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("scan", help="emit the normalized asset graph as JSON")

    catalog = sub.add_parser("catalog", help="render a Markdown catalog")
    catalog.add_argument("--out", help="write to this file instead of stdout")
    catalog.add_argument(
        "--top", type=int, default=10, help="context-budget ranking size (default: 10)"
    )

    args = parser.parse_args(argv)
    config, origin = load_config(args.config)
    graph = build_graph(config, origin)

    if args.command == "scan":
        json.dump(graph, sys.stdout, indent=2, ensure_ascii=False)
        sys.stdout.write("\n")
        return 0

    rendered = render_catalog(graph, args.top)
    if args.out:
        Path(args.out).write_text(rendered, encoding="utf-8")
        print(f"wrote {args.out} ({graph['stats']['total']} items)")
    else:
        sys.stdout.write(rendered)
    return 0


if __name__ == "__main__":
    sys.exit(main())
