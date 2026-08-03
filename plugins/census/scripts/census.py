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
  scan         emit the normalized asset graph as JSON
  catalog      render a Markdown catalog of everything found
  portability  grade each item by how tightly it is bound to this machine
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

# Git hosts shared by everyone. An `origin` on one of these says nothing about
# who you are, so only the account segment of such a remote is a marker.
PUBLIC_FORGES = {
    "github.com",
    "gitlab.com",
    "bitbucket.org",
    "codeberg.org",
    "git.sr.ht",
    "gitea.com",
}

# Shortest string accepted as a derived marker. Below this, substring matching
# produces more noise than signal.
MIN_MARKER_LEN = 4

# Portability tiers, ordered most to least shareable.
TIER_PORTABLE = "PORTABLE"
TIER_PARAM = "PARAMETERIZABLE"
TIER_PERSONAL = "PERSONAL"


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
# portability
# --------------------------------------------------------------------------


def derive_markers(config: dict[str, Any], roots: list[dict[str, Any]]) -> list[dict[str, str]]:
    """Work out which strings identify THIS machine and THIS owner.

    Nothing here is hardcoded: markers come from the config, the environment,
    and the git remotes of the repos being scanned. That is what lets the same
    check run for a different person and flag their identifiers instead.
    """
    found: dict[str, dict[str, str]] = {}

    def add(value: str | None, category: str, source: str) -> None:
        if not value or len(value) < MIN_MARKER_LEN:
            return
        found.setdefault(value, {"value": value, "category": category, "source": source})

    for marker in config["portability"].get("markers", []):
        add(marker, "configured", "config")

    add(os.environ.get("USER"), "user", "$USER")
    add(Path.home().name, "user", "$HOME")

    # Directory names the user invented to organize their repos: the parts of a
    # projectRoots pattern that sit BELOW home and above the first glob. Parts
    # at or above home (`/Users`, `/home`) are universal, not personal.
    home = Path.home()
    for pattern in config["projectRoots"]:
        expanded = Path(os.path.expanduser(pattern))
        try:
            relative = expanded.relative_to(home)
        except ValueError:
            relative = expanded
        for part in relative.parts:
            if any(ch in part for ch in "*?["):
                break
            if part not in (os.sep, "~", ".", ".."):
                add(part, "layout", f"projectRoots {pattern}")

    for root in roots:
        repo = root["path"].parent if root["scope"] == "repo" else None
        if repo is None:
            continue
        for owner, host in _remote_identities(repo):
            forge = _public_forge(host)
            if forge:
                # A public forge namespace is globally unique, so the account
                # name identifies its owner.
                add(owner, "account", f"git remote of {repo.name}")
                continue
            if not host:
                continue
            # On a self-hosted forge the "owner" is just a group name and is
            # often a generic word (`server`, `infra`, `platform`) that would
            # match prose everywhere. The HOST is the identifying part, so only
            # that is taken.
            add(host, "host", f"git remote of {repo.name}")
            labels = [p for p in host.split(".") if p not in ("www", "git", "gitlab", "github")]
            if labels:
                add(labels[0], "host", f"git remote of {repo.name}")

    return sorted(found.values(), key=lambda m: (-len(m["value"]), m["value"]))


def _public_forge(host: str | None) -> str | None:
    """Return the public forge a host refers to, if any.

    Handles SSH config aliases such as `github.com-work`, which resolve to a
    public forge but carry an account-specific suffix.
    """
    if not host:
        return None
    lowered = host.lower()
    for forge in PUBLIC_FORGES:
        if lowered == forge or lowered.startswith(forge + "-") or lowered.endswith("." + forge):
            return forge
    return None


def _remote_identities(repo: Path) -> list[tuple[str | None, str | None]]:
    """Return (owner, host) pairs parsed from the repo's git remotes."""
    try:
        out = subprocess.run(
            ["git", "-C", str(repo), "remote", "-v"],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return []

    identities: list[tuple[str | None, str | None]] = []
    for line in out.stdout.splitlines():
        parts = line.split()
        if len(parts) < 2:
            continue
        url = parts[1]
        # scp-style (git@host:owner/repo) and URL-style (scheme://host/owner/repo)
        match = re.match(r"^[\w.+-]+@([^:]+):([^/]+)/", url) or re.match(
            r"^[a-z]+://(?:[^@/]+@)?([^/:]+)(?::\d+)?/([^/]+)/", url
        )
        if match:
            host, owner = match.group(1), match.group(2)
            # SSH host aliases like `github.com-someone` carry the account too.
            identities.append((owner, host))
    return identities


def find_hits(path: Path, markers: list[dict[str, str]]) -> list[dict[str, Any]]:
    """Locate every marker occurrence, recording WHERE it lands.

    Placement is what separates a swappable literal from a baked-in assumption,
    so each hit is classified as frontmatter / code / prose.
    """
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return []

    _, offset = parse_frontmatter(text)
    frontmatter_lines = text[:offset].count("\n") if offset else 0

    hits: list[dict[str, Any]] = []
    in_fence = False
    for lineno, line in enumerate(text.splitlines(), start=1):
        if line.lstrip().startswith("```"):
            in_fence = not in_fence
            continue
        lowered = line.lower()
        for marker in markers:
            if marker["value"].lower() not in lowered:
                continue
            if lineno <= frontmatter_lines:
                placement = "frontmatter"
            elif in_fence or _in_inline_code(line, marker["value"]):
                placement = "code"
            else:
                placement = "prose"
            hits.append(
                {
                    "line": lineno,
                    "marker": marker["value"],
                    "category": marker["category"],
                    "placement": placement,
                    "text": line.strip()[:160],
                }
            )
    return hits


def _in_inline_code(line: str, marker: str) -> bool:
    """True when every occurrence of `marker` on this line sits inside backticks."""
    spans = [(m.start(), m.end()) for m in re.finditer(r"`[^`]*`", line)]
    lowered, needle = line.lower(), marker.lower()
    start = lowered.find(needle)
    while start != -1:
        if not any(a <= start and start + len(needle) <= b for a, b in spans):
            return False
        start = lowered.find(needle, start + 1)
    return True


def grade(hits: list[dict[str, Any]]) -> str:
    """Assign a portability tier from where the markers landed.

    The rule is mechanical on purpose — a reviewer can re-derive it from the
    evidence rather than trusting a judgment call:

      no hits                      -> PORTABLE
      hits only in code/paths      -> PARAMETERIZABLE (swap the literal for a setting)
      any hit in frontmatter/prose -> PERSONAL (the charter itself assumes this
                                      environment; routing or scope would have
                                      to be rewritten, not configured)
    """
    if not hits:
        return TIER_PORTABLE
    if any(h["placement"] in ("frontmatter", "prose") for h in hits):
        return TIER_PERSONAL
    return TIER_PARAM


def build_portability(graph: dict[str, Any], config: dict[str, Any]) -> dict[str, Any]:
    roots = [
        {"path": Path(r["path"]), "scope": r["scope"], "repo": r["repo"]}
        for r in graph["roots"]
    ]
    markers = derive_markers(config, roots)

    graded: list[dict[str, Any]] = []
    for item in graph["items"]:
        if item["kind"] == "hook":
            continue  # a hook is a settings.json registration, not a shareable unit
        hits = find_hits(Path(item["file"]), markers)
        graded.append(
            {
                "name": item["name"],
                "kind": item["kind"],
                "origin": item["origin"],
                "file": item["file"],
                "tier": grade(hits),
                "hits": hits,
                "markers": sorted({h["marker"] for h in hits}),
            }
        )

    tiers = {t: 0 for t in (TIER_PORTABLE, TIER_PARAM, TIER_PERSONAL)}
    for entry in graded:
        tiers[entry["tier"]] += 1

    return {"markers": markers, "items": graded, "tiers": tiers}


def render_portability(report: dict[str, Any], evidence: int) -> str:
    tiers = report["tiers"]
    total = sum(tiers.values())
    out = ["# Portability triage", ""]

    shareable = tiers[TIER_PORTABLE]
    out += [
        f"- Graded: {total} items (hooks excluded — a hook is a settings.json "
        "registration, not a shareable unit)",
        f"- 🟢 {TIER_PORTABLE}: **{shareable}**  ·  "
        f"🟡 {TIER_PARAM}: **{tiers[TIER_PARAM]}**  ·  "
        f"🔴 {TIER_PERSONAL}: **{tiers[TIER_PERSONAL]}**",
        f"- Share-ready without edits: {shareable}/{total}"
        f" ({shareable * 100 // total if total else 0}%)",
        "",
        "Derived markers — strings that identify this machine or owner:",
        "",
        "| Marker | Category | Derived from |",
        "|---|---|---|",
    ]
    for marker in report["markers"]:
        out.append(f"| `{marker['value']}` | {marker['category']} | {marker['source']} |")
    out.append("")

    for tier, emoji, note in (
        (TIER_PORTABLE, "🟢", "no machine-specific reference; promote as-is"),
        (TIER_PARAM, "🟡", "markers appear only as literals in code/paths — swap for a setting"),
        (TIER_PERSONAL, "🔴", "markers appear in frontmatter or prose — the charter assumes this environment"),
    ):
        group = [i for i in report["items"] if i["tier"] == tier]
        if not group:
            continue
        out += ["<br/>", "", f"## {emoji} {tier} ({len(group)})", "", f"_{note}_", ""]
        out += ["| Item | Kind | Origin | Hits | Markers |", "|---|---|---|---|---|"]
        for item in sorted(group, key=lambda i: (-len(i["hits"]), i["name"])):
            markers = ", ".join(f"`{m}`" for m in item["markers"]) or "—"
            out.append(
                f"| `{item['name']}` | {item['kind']} | {item['origin']} "
                f"| {len(item['hits'])} | {markers} |"
            )
        out.append("")

        if tier != TIER_PORTABLE and evidence:
            out += [f"### Evidence (first {evidence} hits per item)", ""]
            for item in sorted(group, key=lambda i: (-len(i["hits"]), i["name"])):
                out.append(f"**`{item['name']}`** — {item['file']}")
                for hit in item["hits"][:evidence]:
                    out.append(
                        f"- `{item['file']}:{hit['line']}` "
                        f"[{hit['placement']}/{hit['category']}] `{hit['marker']}` — "
                        f"{_truncate(hit['text'], 100)}"
                    )
                out.append("")

    return "\n".join(out)


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

    port = sub.add_parser("portability", help="grade items by machine-specific coupling")
    port.add_argument("--out", help="write to this file instead of stdout")
    port.add_argument(
        "--evidence", type=int, default=3, help="hits shown per item (0 to omit; default: 3)"
    )
    port.add_argument("--json", action="store_true", help="emit the raw report as JSON")

    args = parser.parse_args(argv)
    config, origin = load_config(args.config)
    graph = build_graph(config, origin)

    if args.command == "scan":
        json.dump(graph, sys.stdout, indent=2, ensure_ascii=False)
        sys.stdout.write("\n")
        return 0

    if args.command == "portability":
        report = build_portability(graph, config)
        if args.json:
            json.dump(report, sys.stdout, indent=2, ensure_ascii=False)
            sys.stdout.write("\n")
            return 0
        rendered = render_portability(report, args.evidence)
        if args.out:
            Path(args.out).write_text(rendered, encoding="utf-8")
            print(f"wrote {args.out} ({sum(report['tiers'].values())} items graded)")
        else:
            sys.stdout.write(rendered)
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
