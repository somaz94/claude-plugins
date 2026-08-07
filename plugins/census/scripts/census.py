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
  drift        report duplicates, translation-pair gaps and frontmatter defects
"""

from __future__ import annotations

import argparse
import fnmatch
import hashlib
import json
import os
import re
import shlex
import subprocess
import sys
from pathlib import Path
from typing import Any, Callable, Iterable

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
    # Translation mirror directories, keyed by the directory they mirror. Empty
    # by default: keeping mirrors is one workflow among many, and assuming it
    # reports a missing mirror for EVERY item of anyone who does not — a wall of
    # red that buries the findings that were actually worth reading. Opt in with
    # e.g. {"agents": "agents-ko"} and the whole pair axis switches on.
    "pairs": {},
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

# A hook command word ending in one of these is a script even when it is
# written without a directory, which is what separates `guard.sh` from `jq`.
SCRIPT_SUFFIXES = (".sh", ".bash", ".zsh", ".py", ".js", ".mjs", ".ts", ".rb", ".pl")

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
# tolerant readers
# --------------------------------------------------------------------------
#
# census reads config it did not write, by hand-edited JSON whose shape nothing
# validates. A file in an unexpected shape must degrade to "nothing found here"
# — never take down the audit of every OTHER root along with it. These coerce
# at the boundary so no reader downstream has to re-check.


def _as_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _as_list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _as_str(value: Any) -> str:
    return value if isinstance(value, str) else ""


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
            # A config this tool cannot parse is a user error, not a crash: a
            # traceback here would read as a bug in census rather than a typo in
            # the file it was told to load.
            try:
                with path.open(encoding="utf-8") as fh:
                    loaded = json.load(fh)
            except (json.JSONDecodeError, OSError) as exc:
                raise SystemExit(f"census: cannot read config {path}: {exc}") from exc
            if not isinstance(loaded, dict):
                raise SystemExit(f"census: config {path} must hold a JSON object")
            merged = {**DEFAULT_CONFIG, **loaded}
            # One level of nesting needs an explicit merge.
            merged["portability"] = {
                **DEFAULT_CONFIG["portability"],
                **_as_dict(loaded.get("portability")),
            }
            for key in ("userRoots", "projectRoots", "exclude"):
                merged[key] = _as_list(merged[key])
            merged["pairs"] = _as_dict(merged["pairs"])
            merged["portability"]["markers"] = _as_list(merged["portability"].get("markers"))
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


def git_output(directory: str | Path, *args: str, timeout: int = 10) -> str:
    """Stdout of a read-only git command, or ``""`` when it cannot be run.

    Every git call census makes is optional enrichment — a repo may not be a
    repo, git may not be installed, a huge history may time out. None of that is
    a reason to fail an audit, so a failure is indistinguishable from no answer.
    """
    try:
        out = subprocess.run(
            ["git", "-C", str(directory), *args],
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return ""
    return out.stdout


def has_upstream_remote(repo: Path) -> bool:
    """True when the repo has an `upstream` remote (the `gh repo fork` shape)."""
    return "upstream" in git_output(repo, "remote").split()


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


def project_repo_names(config: dict[str, Any]) -> list[str]:
    """Every repo directory `projectRoots` resolves to, by name.

    Deliberately wider than `discover_roots`, which keeps only directories that
    turned out to hold a `.claude/`. A repo identifies this machine whether or
    not it carries Claude config — an agent written about `acme-billing-api` is
    bound to it either way — and excluded repos count too: skipping a repo means
    not cataloguing its config, not pretending the repo is not there.
    """
    names: set[str] = set()
    for pattern in config["projectRoots"]:
        for path in _expand(pattern):
            if path.is_dir():
                names.add(path.name)
    return sorted(names)


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


def _digest(text: str) -> str:
    """Short content fingerprint. Identity of a document, never a security claim."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def collect_items(roots: list[dict[str, Any]], config: dict[str, Any]) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    pairs = config["pairs"]

    for root in roots:
        base: Path = root["path"]
        origin = root["repo"] or "user"

        # Flat and folder layouts differ only in what they glob and what names
        # an item: the mirror path is the same subpath under the mirror root
        # either way (`alpha.md`, or `demo/SKILL.md`).
        for subdir, kind in {**FLAT_KINDS, **FOLDER_KINDS}.items():
            source = base / subdir
            foldered = subdir in FOLDER_KINDS
            mirror = pairs.get(subdir)
            for path in sorted(source.glob("*/SKILL.md" if foldered else "*.md")):
                item = _read_item(
                    path, kind, root, origin, name=path.parent.name if foldered else None
                )
                item["pair"] = (
                    _pair_for(base / mirror / path.relative_to(source), base / mirror)
                    if mirror
                    else None
                )
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
        "expectedName": name or path.stem,
        "file": str(path),
        "scope": root["scope"],
        "origin": origin,
        "frontmatter": {k: fields[k] for k in KNOWN_KEYS if k in fields},
        "extraKeys": sorted(k for k in fields if k not in KNOWN_KEYS),
        "descriptionChars": len(description),
        "bodyChars": len(text) - offset,
        "digest": _digest(text),
        "structure": _structure(text),
        "pair": None,
    }


def _structure(text: str) -> dict[str, int]:
    """Coarse shape of a Markdown document.

    Used to compare a document against its translation without reading either:
    a mirror that lost a section, a code block or a table row has drifted even
    when both files still look plausible on their own.
    """
    return {
        "headings": len(re.findall(r"^#{1,6} ", text, re.M)),
        "codeBlocks": text.count("```") // 2,
        "tableRows": len(re.findall(r"^\|", text, re.M)),
    }


def _pair_for(path: Path, mirror_root: Path) -> dict[str, Any] | None:
    """Describe a translation mirror.

    `mirrorDir` is the mirror's own directory PATH, not just its name: house
    style is set per directory, and the same name (`agents-ko`) is written
    differently in different trees.
    """
    if not path.is_file():
        return {"file": str(path), "exists": False, "mirrorDir": str(mirror_root)}
    text = path.read_text(encoding="utf-8", errors="replace")
    return {
        "file": str(path),
        "exists": True,
        "mirrorDir": str(mirror_root),
        "digest": _digest(text),
        **_structure(text),
    }


def _hook_tokens(command: str) -> list[str]:
    """Split a hook command into words, tolerating shell it cannot lex."""
    if not command.strip():
        return []
    try:
        return shlex.split(command)
    except ValueError:
        return command.split()


def _expand_hook_token(token: str, base: Path) -> str:
    """Substitute the variables a hook command may be written in terms of."""
    home = str(Path.home())
    for variable in ("CLAUDE_PROJECT_DIR", "CLAUDE_PLUGIN_ROOT"):
        for form in (f"${{{variable}}}", f"${variable}"):
            token = token.replace(form, str(base.parent))
    return token.replace("$HOME", home).replace("${HOME}", home)


def _hook_script(command: str, base: Path) -> Path | None:
    """Resolve the script a hook registration runs, when it runs one.

    A hook's real content is not the registration — it is the file the
    registration points at. Without resolving it, everything a hook actually
    does is invisible: the hardcoded paths inside it, and whether it is even
    there.

    A command may also be inline shell (`echo …`, `jq …`) that runs no script at
    all, which is why this can return None. Taking the first word regardless
    would invent a file that was never meant to exist and report every such hook
    as broken. So a word only counts as a script when it is written like a path
    or carries a script suffix — which also finds the script behind an
    interpreter prefix (`python3 guard.py`), where the first word never was one.
    """
    for token in _hook_tokens(command):
        if token.startswith("-"):
            continue
        expanded = _expand_hook_token(token, base)
        if "/" not in expanded and not expanded.endswith(SCRIPT_SUFFIXES):
            continue
        path = Path(os.path.expanduser(expanded))
        if not path.is_absolute():
            path = base.parent / path
        return path
    return None


def _hook_name(script: Path | None, tokens: list[str], event: str) -> str:
    """What to call a hook.

    The script it runs, falling back to the command itself and then to the
    event — a registered command may be nothing but whitespace, and there is
    still a registration to report.
    """
    if script is not None:
        return script.name
    if tokens:
        return Path(tokens[0]).name
    return event


def _read_hooks(settings: Path, root: dict[str, Any], origin: str) -> list[dict[str, Any]]:
    """Extract hook registrations from a settings.json.

    Hooks carry no frontmatter and no description, so they contribute nothing to
    the always-on context budget. They are still resolved to the script they
    run, because that script is where a hook's machine-specific coupling lives.
    """
    if not settings.is_file():
        return []
    try:
        with settings.open(encoding="utf-8") as fh:
            data = json.load(fh)
    except (json.JSONDecodeError, OSError):
        return []

    found: list[dict[str, Any]] = []
    for event, entries in _as_dict(data.get("hooks")).items():
        for raw_entry in _as_list(entries):
            entry = _as_dict(raw_entry)
            for raw_hook in _as_list(entry.get("hooks")):
                command = _as_str(_as_dict(raw_hook).get("command"))
                script = _hook_script(command, root["path"])
                found.append(
                    {
                        "kind": "hook",
                        "name": _hook_name(script, _hook_tokens(command), event),
                        "declaredName": None,
                        "file": str(settings),
                        "script": str(script) if script else None,
                        "scriptExists": bool(script and script.is_file()),
                        "command": command,
                        "scope": root["scope"],
                        "origin": origin,
                        "frontmatter": {
                            "event": event,
                            "matcher": entry.get("matcher", "*"),
                        },
                        "extraKeys": [],
                        "descriptionChars": 0,
                        "bodyChars": len(command),
                        # The registration IS the hook here, so the command line
                        # is what identifies it across mirrored settings.json.
                        "digest": _digest(f"{event}:{command}"),
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
        # A pattern with a glob names a CONTAINER of repos, so every part before
        # the glob is layout. A pattern without one names a single repo, and its
        # last segment is that repo's name — taking it as a global marker would
        # match the name everywhere, which is the generic-word trap. Repo names
        # are handled per-repo instead, by `origin_markers`.
        parts = relative.parts
        if not any(ch in str(relative) for ch in "*?["):
            parts = parts[:-1]
        for part in parts:
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


def origin_markers(origins: list[str], global_values: set[str]) -> list[dict[str, str]]:
    """Markers that identify the repo an item was found in.

    Global markers only catch identifiers that are unique machine-wide — an
    account name, a home-relative layout directory. They are blind to the most
    common coupling of all: a repo-scoped agent naming its OWN repo, by a path
    relative to it. `Reviews changes inside acme-platform/storage/` contains no
    global identifier at all, so it scores as perfectly portable while being
    one of the least portable items there is.

    The repo name is the missing marker, but only WITHIN that repo. Applying it
    globally would be the generic-word trap that already forced self-hosted
    forge owners to be dropped: a repo called `docs` or `tools` would match
    prose everywhere. Scoped to its own items, a match means what it says.
    """
    markers = []
    for origin in origins:
        if origin == "user" or origin in global_values or len(origin) < MIN_MARKER_LEN:
            continue
        markers.append({
            "value": origin,
            "category": "repo",
            "source": f"the repo this item lives in ({origin})",
        })
    return markers


def _is_distinctive(name: str) -> bool:
    """True when a repo name is specific enough to match on outside its own repo.

    A single-word repo (`docs`, `tools`, `meshery`) is also an English word, or
    close enough, and matching it across a whole config would fire on prose.
    A multi-token name (`ansible-k8s-iac-tool`, `terraform_modules`) is one
    nobody writes by accident. Restricting to those is why this can be applied
    where `origin_markers` deliberately refused to go.
    """
    return len(name) >= MIN_MARKER_LEN and ("-" in name or "_" in name)


def named_repo_markers(
    repo_names: Iterable[str], global_values: set[str]
) -> list[dict[str, str]]:
    """Repo names as markers for items that live OUTSIDE any repo.

    `origin_markers` covers a repo-scoped item naming its own repo. It cannot
    cover the mirror image: a USER-level item — one shared by every session on
    the machine — that is written about a handful of specific repos. Nothing
    else supplies those names, so such an item carries no marker at all and
    grades as perfectly portable while being useless to anyone who does not
    have those exact repos.

    Only distinctive names qualify; see `_is_distinctive` for why.
    """
    markers = []
    for name in sorted(set(repo_names)):
        if name in global_values or not _is_distinctive(name):
            continue
        markers.append({
            "value": name,
            "category": "named-repo",
            "source": f"a repo on this machine ({name})",
        })
    return markers


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
    identities: list[tuple[str | None, str | None]] = []
    for line in git_output(repo, "remote", "-v").splitlines():
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


def _always_code(_marker: str) -> str:
    """Placement resolver for a source that is code end to end."""
    return "code"


def _line_hits(
    line: str,
    lineno: int,
    markers: list[dict[str, str]],
    placement: Callable[[str], str],
    file: str | None = None,
) -> list[dict[str, Any]]:
    """Every marker occurring in one line, each recorded with where it landed.

    Placement is resolved per MARKER rather than per line: two markers on the
    same line of Markdown can land differently, one inside backticks and one in
    the surrounding prose.
    """
    lowered = line.lower()
    hits: list[dict[str, Any]] = []
    for marker in markers:
        value = marker["value"]
        if value.lower() not in lowered:
            continue
        hit = {
            "line": lineno,
            "marker": value,
            "category": marker["category"],
            "placement": placement(value),
            "text": line.strip()[:160],
        }
        if file is not None:
            hit["file"] = file
        hits.append(hit)
    return hits


def _command_hits(
    command: str, file: str, markers: list[dict[str, str]]
) -> list[dict[str, Any]]:
    """Markers inside a hook's registered command string."""
    return _line_hits(command, 1, markers, _always_code, file)


def find_hits(
    path: Path, markers: list[dict[str, str]], markdown: bool = True
) -> list[dict[str, Any]]:
    """Locate every marker occurrence, recording WHERE it lands.

    Placement is what separates a swappable literal from a baked-in assumption,
    so each hit is classified as frontmatter / code / prose.

    With ``markdown=False`` the source is a script, not a document: there is no
    frontmatter and no fence, and every line is code. A hardcoded path there is
    always a literal that could be lifted into a variable.
    """
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return []

    hits: list[dict[str, Any]] = []

    if not markdown:
        for lineno, line in enumerate(text.splitlines(), start=1):
            hits += _line_hits(line, lineno, markers, _always_code, str(path))
        return hits

    _, offset = parse_frontmatter(text)
    frontmatter_lines = text[:offset].count("\n") if offset else 0

    in_fence = False
    for lineno, line in enumerate(text.splitlines(), start=1):
        if line.lstrip().startswith("```"):
            in_fence = not in_fence
            continue

        # Closes over this iteration's line state, and is consumed inside the
        # same iteration — there is no later call to bind stale values.
        def placement(value: str) -> str:
            if lineno <= frontmatter_lines:
                return "frontmatter"
            if in_fence or _in_inline_code(line, value):
                return "code"
            return "prose"

        hits += _line_hits(line, lineno, markers, placement)
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


def _referenced_items(path: Path, names: Iterable[str], own: str) -> list[str]:
    """Names of other catalogued items this one hands work to.

    Matched only inside backticks. Every item that delegates writes the target
    the same way — ``delegate to `md-doc-writer` `` — and requiring the code
    span is what keeps short names (`release`, `scan`, `handoff`) from firing on
    ordinary prose. A missed reference is a far cheaper mistake here than an
    invented one.
    """
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return []
    quoted = {span.strip() for span in re.findall(r"`([^`\n]{1,80})`", text)}
    return sorted(name for name in set(names) if name != own and name in quoted)


def config_scripts(roots: list[dict[str, Any]]) -> dict[str, str]:
    """Every script living inside a config root, indexed by basename.

    This is the lookup table that separates a real dependency from an example.
    `find-sensitive.sh` is in it because the file is there; the `foo.sh` an agent
    invents to illustrate its output format is not. Matching against what exists
    is what lets the reference check stay quiet on prose.
    """
    index: dict[str, str] = {}
    for root in roots:
        base: Path = root["path"]
        for path in sorted(base.rglob("*")):
            if path.is_file() and path.name.endswith(SCRIPT_SUFFIXES):
                index.setdefault(path.name, str(path))
    return index


def _external_scripts(
    path: Path, index: dict[str, str], own: str, markdown: bool = True
) -> list[dict[str, str]]:
    """Scripts this item calls that are not part of the item.

    A hook that shells out to a shared script, or an agent whose workflow runs
    one, has a dependency that no name in the asset graph represents: the script
    is not an agent, command, skill or hook, so it is invisible to the catalogue
    and to `_referenced_items`. Publish the item alone and the call resolves to
    nothing — the same failure as a missing dependency, one layer down.
    """
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return []

    if markdown:
        tokens = {span.strip() for span in re.findall(r"`([^`\n]{1,120})`", text)}
    else:
        # Comment lines are dropped before tokenizing. A script's header
        # routinely names its siblings in prose — "the command-side sibling of
        # pre-edit-release-automation-guard.sh" — and counting that as a call
        # would report a dependency that does not exist at runtime.
        code = "\n".join(
            line for line in text.splitlines() if not line.lstrip().startswith("#")
        )
        tokens = set(re.findall(r"[\w./~$@{}-]+", code))

    found: dict[str, str] = {}
    for token in tokens:
        name = token.rsplit("/", 1)[-1]
        if name == own or name == path.name or not name.endswith(SCRIPT_SUFFIXES):
            continue
        target = index.get(name)
        if target and target != str(path):
            found.setdefault(name, target)
    return [{"name": n, "file": found[n]} for n in sorted(found)]


def _item_scripts(item: dict[str, Any], index: dict[str, str]) -> list[dict[str, str]]:
    """Scripts this item shells out to, read from wherever its calls actually are.

    A hook IS a script, so what it runs is read as code. Everything else is
    Markdown, where only a backticked reference counts — see `_external_scripts`
    for why that distinction has to be made at all.
    """
    if item["kind"] != "hook":
        return _external_scripts(Path(item["file"]), index, item["name"])
    if not item.get("scriptExists"):
        return []
    return _external_scripts(Path(item["script"]), index, item["name"], markdown=False)


def build_portability(graph: dict[str, Any], config: dict[str, Any]) -> dict[str, Any]:
    roots = [
        {"path": Path(r["path"]), "scope": r["scope"], "repo": r["repo"]}
        for r in graph["roots"]
    ]
    markers = derive_markers(config, roots)

    graded: list[dict[str, Any]] = []
    global_values = {m["value"] for m in markers}
    scoped_seen: set[str] = set()
    named_seen: set[str] = set()

    # Not just the scanned roots — see `project_repo_names`.
    repo_names = graph.get("repoNames") or [
        r["repo"] for r in graph["roots"] if r["scope"] == "repo" and r["repo"]
    ]
    catalogued = {i["name"] for i in graph["items"] if i["kind"] != "hook"}
    scripts = config_scripts(roots)

    # Graded per ASSET, not per file: identical copies in a mirrored repo pair
    # score identically, and grading both would double every tier population.
    for item in _collapse_copies(graph["items"]):
        scoped = origin_markers(item["origins"], global_values)
        scoped_seen.update(m["value"] for m in scoped)
        every = markers + scoped

        # A user-level item belongs to no repo, so `origin_markers` gives it
        # nothing — yet those are exactly the items that get written about a
        # named handful of repos. Repo-scoped items are already covered above
        # and must not be re-matched against every OTHER repo's name.
        if item["origins"] == ["user"]:
            named = named_repo_markers(repo_names, global_values)
            named_seen.update(m["value"] for m in named)
            every = every + named

        if item["kind"] == "hook":
            # A hook's coupling lives in two places: the command recorded in
            # settings.json, and the script it points at. Neither is Markdown,
            # so there is no frontmatter to land in and the tier tops out at
            # PARAMETERIZABLE — a path in a script is a literal you can lift
            # into a variable, never a routing description you would rewrite.
            hits = _command_hits(item.get("command", ""), item["file"], every)
            if item.get("scriptExists"):
                hits += find_hits(Path(item["script"]), every, markdown=False)
            target = item.get("script") or item["file"]
        else:
            hits = find_hits(Path(item["file"]), every)
            target = item["file"]

        graded.append(
            {
                "name": item["name"],
                "kind": item["kind"],
                "origins": item["origins"],
                "file": target,
                "copies": item["files"],
                "tier": grade(hits),
                "hits": hits,
                "markers": sorted({h["marker"] for h in hits}),
                "dependsOn": (
                    [] if item["kind"] == "hook"
                    else _referenced_items(Path(item["file"]), catalogued, item["name"])
                ),
                "externalScripts": _item_scripts(item, scripts),
            }
        )

    # Shareability is not a property of one file. A command whose whole body is
    # "delegate to `x`" carries no marker of its own and grades PORTABLE, while
    # being worth nothing without `x`. Resolve that only now, once every item
    # has a tier to look up.
    by_name = {entry["name"]: entry for entry in graded}
    for entry in graded:
        entry["blockedBy"] = sorted(
            dep for dep in entry["dependsOn"]
            if by_name.get(dep, {}).get("tier", TIER_PORTABLE) != TIER_PORTABLE
        )
        entry["shareable"] = (
            entry["tier"] == TIER_PORTABLE
            and not entry["blockedBy"]
            and not entry["externalScripts"]
        )

    tiers = {t: 0 for t in (TIER_PORTABLE, TIER_PARAM, TIER_PERSONAL)}
    for entry in graded:
        tiers[entry["tier"]] += 1

    return {
        "markers": markers,
        "scopedMarkers": sorted(scoped_seen),
        "namedRepoMarkers": sorted(named_seen),
        "items": graded,
        "tiers": tiers,
        "shareable": sum(1 for e in graded if e["shareable"]),
        "blocked": sorted(e["name"] for e in graded if e["tier"] == TIER_PORTABLE
                          and e["blockedBy"]),
        "callsScripts": sorted(e["name"] for e in graded if e["externalScripts"]),
    }


def _table(headers: Iterable[str], rows: Iterable[Iterable[str]]) -> list[str]:
    """A Markdown table as report lines.

    The alignment row is the part every table here was spelling out by hand, and
    the one thing that silently breaks a table when its column count changes.
    """
    columns = list(headers)
    return [
        "| " + " | ".join(columns) + " |",
        "|" + "---|" * len(columns),
        *("| " + " | ".join(row) + " |" for row in rows),
    ]


def _section(
    heading: str, note: str, headers: Iterable[str], rows: Iterable[Iterable[str]]
) -> list[str]:
    """A report section: a spacer, a heading, an italic note, then a table.

    Every section of both reports has this shape. Stating it once is also what
    keeps the `<br/>` between heading sections — the house style this repo's own
    doc checker enforces — from being forgotten in one section out of five.
    """
    return ["<br/>", "", f"## {heading}", "", f"_{note}_", "", *_table(headers, rows), ""]


def render_portability(report: dict[str, Any], evidence: int) -> str:
    tiers = report["tiers"]
    total = sum(tiers.values())
    out = ["# Portability triage", ""]

    portable = tiers[TIER_PORTABLE]
    shareable = report["shareable"]
    out += [
        f"- Graded: {total} items — agents, commands, skills, and hooks "
        "(graded through the script they run, where their coupling actually lives)",
        f"- 🟢 {TIER_PORTABLE}: **{portable}**  ·  "
        f"🟡 {TIER_PARAM}: **{tiers[TIER_PARAM]}**  ·  "
        f"🔴 {TIER_PERSONAL}: **{tiers[TIER_PERSONAL]}**",
        f"- Share-ready without edits: {shareable}/{total}"
        f" ({shareable * 100 // total if total else 0}%)",
    ]
    if report["blocked"]:
        out.append(
            f"- Of the {portable} 🟢 items, **{len(report['blocked'])} cannot ship alone** — "
            "they carry no marker themselves but delegate to an item that does. "
            "See _Blocked by a dependency_ below."
        )
    out += [
        "",
        "Derived markers — strings that identify this machine or owner:",
        "",
        *_table(
            ("Marker", "Category", "Derived from"),
            (
                (f"`{m['value']}`", m["category"], m["source"])
                for m in report["markers"]
            ),
        ),
        "",
    ]

    scoped = report.get("scopedMarkers") or []
    if scoped:
        out += [
            f"Plus {len(scoped)} **repo-scoped** markers — each repo's own name, matched only "
            "against items living in it. This is what catches an agent that names its own repo "
            "by a relative path and carries no machine-wide identifier at all:",
            "",
            ", ".join(f"`{value}`" for value in scoped),
            "",
        ]

    named = report.get("namedRepoMarkers") or []
    if named:
        out += [
            f"Plus {len(named)} **named-repo** markers — repos on this machine, matched only "
            "against USER-level items. A user-level item belongs to no repo, so nothing else "
            "supplies these names, and one written about a specific handful of repos would "
            "otherwise carry no marker at all. Only multi-token names qualify, so a repo "
            "called `docs` never fires on prose:",
            "",
            ", ".join(f"`{value}`" for value in named),
            "",
        ]

    blocked = [i for i in report["items"] if i["tier"] == TIER_PORTABLE and i["blockedBy"]]
    if blocked:
        out += _section(
            f"⛔ Blocked by a dependency ({len(blocked)})",
            "Graded 🟢 on their own contents, but they hand work to an item that is not "
            "portable. Publishing one without its dependency ships a name that resolves to "
            "nothing.",
            ("Item", "Kind", "Origin", "Blocked by"),
            (
                (
                    f"`{item['name']}`",
                    item["kind"],
                    ", ".join(item["origins"]),
                    ", ".join(f"`{d}`" for d in item["blockedBy"]),
                )
                for item in sorted(blocked, key=lambda i: (-len(i["blockedBy"]), i["name"]))
            ),
        )

    calls = [i for i in report["items"] if i["externalScripts"]]
    if calls:
        out += _section(
            f"🧩 Calls a script it does not contain ({len(calls)})",
            "Each of these shells out to a script that is not part of the item and is not "
            "catalogued as one. Publish the item alone and the call resolves to nothing — the "
            "same failure as a missing dependency, one layer down. Only scripts that actually "
            "exist in a config root are counted, so an example filename never appears here.",
            ("Item", "Kind", "Calls"),
            (
                (
                    f"`{item['name']}`",
                    item["kind"],
                    ", ".join(f"`{s['name']}`" for s in item["externalScripts"]),
                )
                for item in sorted(calls, key=lambda i: (-len(i["externalScripts"]), i["name"]))
            ),
        )

    for tier, emoji, note in (
        (TIER_PORTABLE, "🟢", "no machine-specific reference; promote as-is"),
        (TIER_PARAM, "🟡", "markers appear only as literals in code/paths — swap for a setting"),
        (TIER_PERSONAL, "🔴", "markers appear in frontmatter or prose — the charter assumes this environment"),
    ):
        group = [i for i in report["items"] if i["tier"] == tier]
        if not group:
            continue
        # Worst-coupled first, and the same order for the evidence below.
        group.sort(key=lambda i: (-len(i["hits"]), i["name"]))

        out += _section(
            f"{emoji} {tier} ({len(group)})",
            note,
            ("Item", "Kind", "Origin", "Hits", "Markers"),
            (
                (
                    f"`{item['name']}`",
                    item["kind"],
                    ", ".join(item["origins"]),
                    str(len(item["hits"])),
                    ", ".join(f"`{m}`" for m in item["markers"]) or "—",
                )
                for item in group
            ),
        )

        if tier != TIER_PORTABLE and evidence:
            out += [f"### Evidence (first {evidence} hits per item)", ""]
            for item in group:
                out.append(f"**`{item['name']}`** — {item['file']}")
                for hit in item["hits"][:evidence]:
                    # A hook's hits span two files (settings.json and the script
                    # it runs), so each hit carries its own source.
                    out.append(
                        f"- `{hit.get('file', item['file'])}:{hit['line']}` "
                        f"[{hit['placement']}/{hit['category']}] `{hit['marker']}` — "
                        f"{_truncate(hit['text'], 100)}"
                    )
                out.append("")

    return "\n".join(out)


# --------------------------------------------------------------------------
# drift
# --------------------------------------------------------------------------

SEV_HIGH, SEV_MED, SEV_LOW = "high", "medium", "low"

# Structural metrics compared between a document and its translation mirror.
METRICS = ("headings", "codeBlocks", "tableRows")

# A source edited at least this long after its mirror is treated as having moved
# on without it. Below a day the two are almost always the same editing session.
STALE_PAIR_DAYS = 1

# Below this many pairs there is not enough evidence to tell a house style from
# an accident, so comparison falls back to requiring an exact match.
CALIBRATION_MIN = 3


def _finding(
    severity: str,
    axis: str,
    code: str,
    title: str,
    detail: str,
    refs: list[str],
    dedupe: tuple[Any, ...] | None = None,
) -> dict[str, Any]:
    """One drift finding.

    ``dedupe``, when given, is the identity under which findings describing the
    same problem are folded together — see ``_merge_duplicate_findings``.
    """
    finding: dict[str, Any] = {
        "severity": severity,
        "axis": axis,
        "code": code,
        "title": title,
        "detail": detail,
        "refs": refs,
    }
    if dedupe is not None:
        finding["dedupe"] = dedupe
    return finding


def _normalize_key(key: str) -> str:
    return re.sub(r"[-_\s]", "", key).lower()


def build_drift(graph: dict[str, Any]) -> dict[str, Any]:
    """Find the disagreements a catalog alone cannot show.

    Three axes: the same name defined more than once, a translation mirror that
    no longer matches its source, and frontmatter that would misroute or fail
    to route at all.
    """
    items = [i for i in graph["items"] if i["kind"] != "hook"]
    hooks = [i for i in graph["items"] if i["kind"] == "hook"]
    commit_times = git_commit_times(
        [i["file"] for i in items] + [i["pair"]["file"] for i in items if i.get("pair")]
    )
    findings: list[dict[str, Any]] = []

    findings += _duplicate_findings(items)
    findings += _pair_findings(items, commit_times)
    findings += _frontmatter_findings(items)
    findings += _hook_findings(hooks)
    findings = _merge_duplicate_findings(findings)

    counts = {SEV_HIGH: 0, SEV_MED: 0, SEV_LOW: 0}
    for finding in findings:
        counts[finding["severity"]] += 1

    order = {SEV_HIGH: 0, SEV_MED: 1, SEV_LOW: 2}
    findings.sort(key=lambda f: (order[f["severity"]], f["axis"], f["title"]))
    return {
        "findings": findings,
        "counts": counts,
        "graded": len({logical_key(i) for i in items}),
        "files": len(items),
        "hooks": len({logical_key(h) for h in hooks}),
    }


def _duplicate_findings(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """One name defined in several places.

    Severity is decided by whether the copies AGREE, not by the fact that they
    are duplicated: a mirrored repo pair holding byte-identical copies is the
    intended state, while the same name resolving to different content is what
    silently changes behavior depending on where you are.
    """
    groups: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for item in items:
        groups.setdefault((item["kind"], item["name"]), []).append(item)

    findings = []
    for (kind, name), group in sorted(groups.items()):
        if len(group) < 2:
            continue
        origins = ", ".join(sorted(i["origin"] for i in group))
        refs = sorted(i["file"] for i in group)
        agree = len({i["digest"] for i in group}) == 1
        shadowing = {i["scope"] for i in group} == {"global", "repo"}

        if agree:
            findings.append(_finding(
                SEV_LOW,
                "duplicates",
                "mirror-consistent",
                f"`{name}` ({kind}) defined in {len(group)} places, all identical",
                f"Origins: {origins}. Copies agree byte for byte — the intended mirror state.",
                refs,
            ))
        elif shadowing:
            findings.append(_finding(
                SEV_HIGH,
                "duplicates",
                "shadowed",
                f"`{name}` ({kind}) exists at both user and project level with different content",
                f"Origins: {origins}. A project-level definition overrides the user-level one "
                "inside that repo, so the same name behaves differently depending on where "
                "the session is started — and nothing reports which copy won.",
                refs,
            ))
        else:
            findings.append(_finding(
                SEV_MED,
                "duplicates",
                "mirror-drift",
                f"`{name}` ({kind}) defined in {len(group)} places that disagree",
                f"Origins: {origins}. These look like a mirrored pair whose copies have "
                "diverged; whichever one is the source of truth, the other is stale.",
                refs,
            ))
    return findings


def git_commit_times(files: Iterable[str]) -> dict[str, int]:
    """Last commit timestamp for each file, keyed by absolute path.

    Content cannot answer whether a translation is stale — the text is SUPPOSED
    to differ, so every attempt to diff it reports translation as drift. History
    can: if the source has been committed since the mirror last was, the mirror
    is behind, and that holds no matter what language either side is written in.

    Files outside a git repo, or not yet committed, simply get no entry.
    """
    parents = {str(Path(file).parent) for file in files}
    times: dict[str, int] = {}
    for top in {t for t in (_git_toplevel(p) for p in parents) if t}:
        times.update(_git_log_times(top))
    return times


def _resolved(path: str) -> str:
    """Canonical form of a path, so a symlinked root still matches git's view.

    `git rev-parse --show-toplevel` always answers with symlinks resolved. On
    macOS a `/var/...` root comes back as `/private/var/...`, and without this
    the two spellings never meet — the git signal would go quietly missing for
    any config root reached through a link.
    """
    try:
        return str(Path(path).resolve())
    except OSError:
        return path


def _git_toplevel(directory: str) -> str | None:
    return git_output(directory, "rev-parse", "--show-toplevel").strip() or None


def _git_log_times(toplevel: str) -> dict[str, int]:
    """One `git log` per repo, newest first, so the first sighting wins."""
    log = git_output(toplevel, "log", "--format=%ct", "--name-only", timeout=60)

    times: dict[str, int] = {}
    stamp = 0
    for line in log.splitlines():
        line = line.strip()
        if not line:
            continue
        if line.isdigit():
            stamp = int(line)
            continue
        times.setdefault(_resolved(str(Path(toplevel) / line)), stamp)
    return times


def _baseline_deltas(group: list[dict[str, Any]]) -> dict[str, int]:
    """The structural offset a mirror directory's pairs share by convention.

    Most common delta per metric wins; a tie resolves toward 0 so an evenly
    split directory is not declared to have a convention at all. Under
    ``CALIBRATION_MIN`` pairs there is no majority to read, and the baseline
    stays at zero — an exact match.
    """
    if len(group) < CALIBRATION_MIN:
        return {metric: 0 for metric in METRICS}

    baseline: dict[str, int] = {}
    for metric in METRICS:
        values = [comparison["deltas"][metric] for comparison in group]
        baseline[metric] = sorted(set(values), key=lambda v: (-values.count(v), abs(v)))[0]
    return baseline


def _pair_findings(
    items: list[dict[str, Any]], commit_times: dict[str, int]
) -> list[dict[str, Any]]:
    """Translation mirrors that are absent or no longer shaped like the source.

    A mirror usually differs from its source by a CONSTANT: a house style may
    prepend a "this is a translation" banner, or show the frontmatter as a
    fenced block so the file is not loaded as a real definition. Comparing
    shapes naively reports that convention once per file and buries the real
    drift underneath.

    So the offset is calibrated per mirror DIRECTORY — house style is set where
    the files are authored, and the same directory name can be written
    differently in different trees. Whatever delta the majority of a directory's
    pairs share is its convention, and only files that deviate from their own
    siblings are findings. This needs no knowledge of any particular house
    style, so it works the same for a convention this tool has never seen.
    """
    findings: list[dict[str, Any]] = []
    comparisons: dict[str, list[dict[str, Any]]] = {}

    for item in items:
        pair = item.get("pair")
        if not pair:
            continue
        if not pair["exists"]:
            findings.append(_finding(
                SEV_HIGH,
                "pairs",
                "pair-missing",
                f"`{item['name']}` ({item['kind']}) has no translation mirror",
                f"Expected at {pair['file']}.",
                [item["file"]],
            ))
            continue
        comparisons.setdefault(pair["mirrorDir"], []).append({
            "item": item,
            "pair": pair,
            "deltas": {m: pair[m] - item["structure"][m] for m in METRICS},
        })

    for mirror_dir, group in sorted(comparisons.items()):
        baseline = _baseline_deltas(group)

        described = ", ".join(
            f"{m} {baseline[m]:+d}" for m in METRICS if baseline[m]
        )
        if described:
            findings.append(_finding(
                SEV_LOW,
                "pairs",
                "pair-convention",
                f"`{mirror_dir}/` mirrors differ from their source by a constant",
                f"Across {len(group)} pairs the usual offset is {described}. Treated as "
                "house style and calibrated away; only pairs that deviate are reported.",
                [],
            ))

        for comparison in group:
            item, pair = comparison["item"], comparison["pair"]

            source_at = commit_times.get(_resolved(item["file"]))
            mirror_at = commit_times.get(_resolved(pair["file"]))
            if source_at and mirror_at:
                behind = (source_at - mirror_at) // 86400
                if behind >= STALE_PAIR_DAYS:
                    findings.append(_finding(
                        SEV_MED,
                        "pairs",
                        "pair-stale",
                        f"`{item['name']}` ({item['kind']}) mirror is {behind} day(s) "
                        "behind its source",
                        "The source has been committed since the mirror last was. Shapes can "
                        "still match — this is the drift a structural comparison cannot see, "
                        "and content cannot answer it because a translation is meant to differ.",
                        [item["file"], pair["file"]],
                        dedupe=(
                            "pair-stale", item["kind"], item["name"],
                            item["digest"], pair["digest"],
                        ),
                    ))

            deviations = [
                f"{m}: {comparison['deltas'][m]:+d} where this mirror usually has "
                f"{baseline[m]:+d}"
                for m in METRICS
                if comparison["deltas"][m] != baseline[m]
            ]
            if deviations:
                findings.append(_finding(
                    SEV_MED,
                    "pairs",
                    "pair-structure",
                    f"`{item['name']}` ({item['kind']}) deviates from its mirror convention",
                    "; ".join(deviations) + " — one side gained or lost content.",
                    [item["file"], pair["file"]],
                    # A mirrored repo pair holds the same source and the same
                    # translation twice over, so the identical deviation is
                    # found once per tree. It is one problem with one fix.
                    dedupe=(
                        "pair-structure",
                        item["kind"],
                        item["name"],
                        item["digest"],
                        pair["digest"],
                    ),
                ))

    return findings


def _hook_findings(hooks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Hook registrations that point at nothing, or at a file that cannot run.

    The other axes do not apply to hooks — no frontmatter, no translation
    mirror — but a hook has a failure mode the others do not: it is a pointer,
    and the thing it points at can be missing. Nothing in Claude Code reports a
    hook whose script is absent; it simply does not fire, and the guard the user
    believes is protecting them is not there.
    """
    findings = []
    for hook in hooks:
        script = hook.get("script")
        if not script or hook.get("scriptExists"):
            continue
        event = hook["frontmatter"].get("event", "?")
        findings.append(_finding(
            SEV_HIGH,
            "hooks",
            "hook-missing-script",
            f"`{hook['name']}` ({event}) is registered but its script is missing",
            f"Expected at {script}. The registration stays in settings.json, so the hook "
            "looks configured while doing nothing on every matching event.",
            [hook["file"]],
            dedupe=("hook-missing-script", hook["name"], event, script),
        ))
    return findings


def _merge_duplicate_findings(findings: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Fold findings that describe the same problem into one, unioning refs."""
    merged: list[dict[str, Any]] = []
    by_key: dict[tuple[Any, ...], dict[str, Any]] = {}

    for finding in findings:
        key = finding.pop("dedupe", None)
        if key is None:
            merged.append(finding)
            continue
        existing = by_key.get(key)
        if existing is None:
            by_key[key] = finding
            merged.append(finding)
        else:
            for ref in finding["refs"]:
                if ref not in existing["refs"]:
                    existing["refs"].append(ref)

    return merged


def _frontmatter_findings(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Frontmatter that would misroute, or fail to route at all."""
    known = {_normalize_key(k) for k in KNOWN_KEYS}
    findings = []

    for item in items:
        if not item["frontmatter"].get("description"):
            findings.append(_finding(
                SEV_HIGH,
                "frontmatter",
                "no-description",
                f"`{item['name']}` ({item['kind']}) declares no description",
                "The description is the only signal Claude has for when to reach for this "
                "item, so without one it is effectively unreachable unless named explicitly.",
                [item["file"]],
            ))

        declared = item.get("declaredName")
        expected = item.get("expectedName")
        if declared and expected and declared != expected:
            findings.append(_finding(
                SEV_MED,
                "frontmatter",
                "name-mismatch",
                f"`{expected}` declares the name `{declared}`",
                "The file or folder name is what the item is invoked by; a differing "
                "`name:` field misleads anyone reading the frontmatter.",
                [item["file"]],
            ))

        for key in item.get("extraKeys", []):
            if _normalize_key(key) in known:
                findings.append(_finding(
                    SEV_MED,
                    "frontmatter",
                    "key-typo",
                    f"`{item['name']}` ({item['kind']}) uses `{key}`",
                    "Normalizes to a known key but is not spelled like one, so it is read "
                    "as an unknown field and silently ignored.",
                    [item["file"]],
                ))
            else:
                findings.append(_finding(
                    SEV_LOW,
                    "frontmatter",
                    "unknown-key",
                    f"`{item['name']}` ({item['kind']}) declares `{key}`",
                    "Not a key census knows about; harmless if intentional.",
                    [item["file"]],
                ))
    return findings


def render_drift(report: dict[str, Any], limit: int) -> str:
    counts = report["counts"]
    out = ["# Drift report", ""]
    out += [
        f"- Checked: {report['graded']} assets across {report['files']} files, "
        f"plus {report['hooks']} hook registrations (checked only for a missing "
        "script — hooks have no frontmatter and no mirror)",
        f"- 🔴 {counts[SEV_HIGH]}  ·  🟡 {counts[SEV_MED]}  ·  🟢 {counts[SEV_LOW]}",
        "",
    ]

    if not report["findings"]:
        out.append("No drift found.")
        return "\n".join(out)

    for severity, emoji, heading in (
        (SEV_HIGH, "🔴", "Behavior differs or routing is broken"),
        (SEV_MED, "🟡", "Copies disagree or frontmatter misleads"),
        (SEV_LOW, "🟢", "Informational"),
    ):
        group = [f for f in report["findings"] if f["severity"] == severity]
        if not group:
            continue
        out += ["<br/>", "", f"## {emoji} {heading} ({len(group)})", ""]
        shown = group if limit <= 0 else group[:limit]
        for finding in shown:
            out.append(f"**[{finding['code']}]** {finding['title']}")
            out.append("")
            out.append(f"{finding['detail']}")
            out.append("")
            for ref in finding["refs"]:
                out.append(f"- `{ref}`")
            out.append("")
        if len(group) > len(shown):
            out.append(f"_…and {len(group) - len(shown)} more (raise `--limit` to see them)._")
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
        "repoNames": project_repo_names(config),
        "roots": [
            {"path": str(r["path"]), "scope": r["scope"], "repo": r["repo"]} for r in roots
        ],
        "skipped": skipped,
        "items": items,
        "stats": summarize(items),
    }


def logical_key(item: dict[str, Any]) -> tuple[str, str, str]:
    """Identity of an ASSET, as distinct from the file that holds it.

    A mirrored repo pair stores byte-identical copies of one agent in two trees.
    That is two files but one asset, and counting each file separately inflates
    every total downstream — the item count, the tier populations, the ranking.
    Same kind, same name, same bytes means the same thing was found twice.
    """
    return (item["kind"], item["name"], item["digest"])


def summarize(items: list[dict[str, Any]]) -> dict[str, Any]:
    """Aggregate counts and the always-on context cost.

    Only ``name`` + ``description`` of agents, commands and skills is resident in
    every session's system prompt; bodies load on invocation.

    That resident cost is reported PER SESSION, not as a grand total. A session
    loads the global root plus the one repo it was started in — never all of
    them — so summing every root describes a session nobody ever has. The
    all-roots figure is kept, clearly labelled, because it still answers "how
    much config have I accumulated".
    """
    by_kind: dict[str, int] = {}
    unique_by_kind: dict[str, int] = {}
    seen: set[tuple[str, str, str]] = set()

    resident_all = 0
    resident_global = 0
    resident_by_repo: dict[str, int] = {}

    for item in items:
        kind = item["kind"]
        by_kind[kind] = by_kind.get(kind, 0) + 1
        key = logical_key(item)
        if key not in seen:
            seen.add(key)
            unique_by_kind[kind] = unique_by_kind.get(kind, 0) + 1

        if kind == "hook":
            continue
        cost = item["descriptionChars"] + len(item["name"])
        resident_all += cost
        if item["scope"] == "global":
            resident_global += cost
        else:
            resident_by_repo[item["origin"]] = resident_by_repo.get(item["origin"], 0) + cost

    heaviest_repo, heaviest_cost = (
        max(resident_by_repo.items(), key=lambda kv: kv[1]) if resident_by_repo else (None, 0)
    )

    return {
        "total": len(items),
        "uniqueTotal": len(seen),
        "byKind": by_kind,
        "uniqueByKind": unique_by_kind,
        "residentChars": resident_all,
        "residentTokensApprox": resident_all // CHARS_PER_TOKEN,
        "residentGlobalChars": resident_global,
        "residentGlobalTokensApprox": resident_global // CHARS_PER_TOKEN,
        "residentByRepo": dict(sorted(resident_by_repo.items(), key=lambda kv: -kv[1])),
        "heaviestRepo": heaviest_repo,
        "sessionWorstChars": resident_global + heaviest_cost,
        "sessionWorstTokensApprox": (resident_global + heaviest_cost) // CHARS_PER_TOKEN,
    }


def _describe(item: dict[str, Any]) -> str:
    """What to show in the catalog's description column.

    A hook has no frontmatter description, which used to leave its row blank —
    the one kind of item the catalog said nothing about. Its event, matcher and
    target are the equivalent facts, so they stand in.
    """
    if item["kind"] != "hook":
        return item["frontmatter"].get("description", "")

    front = item["frontmatter"]
    parts = [f"{front.get('event', '?')} on {front.get('matcher', '*')}"]
    script = item.get("script")
    if script and not item.get("scriptExists"):
        parts.append(f"SCRIPT MISSING: {script}")
    elif script and Path(script).name != item["name"]:
        # The name is normally the script's basename; only say it when it is not.
        parts.append(f"runs {Path(script).name}")
    return " — ".join(parts)


def _collapse_copies(items: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    """Fold identical copies of one asset into a single entry.

    The entry keeps every origin it was found under, so a mirrored pair reads as
    one row naming both repos rather than two rows that look like two assets.
    """
    merged: dict[tuple[str, str, str], dict[str, Any]] = {}
    for item in items:
        key = logical_key(item)
        existing = merged.get(key)
        if existing is None:
            merged[key] = {**item, "origins": [item["origin"]], "files": [item["file"]]}
        else:
            if item["origin"] not in existing["origins"]:
                existing["origins"].append(item["origin"])
            existing["files"].append(item["file"])
    for entry in merged.values():
        entry["origins"].sort()
    return list(merged.values())


def render_catalog(graph: dict[str, Any], top: int) -> str:
    stats = graph["stats"]
    out: list[str] = ["# Claude config census", ""]

    counts = ", ".join(f"{v} {k}s" for k, v in sorted(stats["uniqueByKind"].items()))
    copies = stats["total"] - stats["uniqueTotal"]
    item_line = f"- Assets: {stats['uniqueTotal']} ({counts})"
    if copies:
        plural = "is an identical copy" if copies == 1 else "are identical copies"
        item_line += (
            f" — found in {stats['total']} files; {copies} {plural} across mirrored repos"
        )

    session = [
        f"- Per-session context: ~{stats['residentGlobalTokensApprox']:,} tokens from the "
        "global root"
    ]
    if stats["heaviestRepo"]:
        session.append(
            f", rising to ~{stats['sessionWorstTokensApprox']:,} in the heaviest repo "
            f"(`{stats['heaviestRepo']}`)"
        )

    out += [
        f"- Config source: `{graph['configOrigin']}`",
        f"- Roots scanned: {len(graph['roots'])}",
        item_line,
        "".join(session),
        f"- Across all roots: ~{stats['residentTokensApprox']:,} tokens "
        f"({stats['residentChars']:,} chars) — accumulated total, **not** a session cost: "
        "a session loads the global root plus the one repo it started in",
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
            group = _collapse_copies(i for i in scoped if i["kind"] == kind)
            if not group:
                continue
            out += [
                f"### {kind.capitalize()}s ({len(group)})",
                "",
                *_table(
                    ("Name", "Origin", "Description"),
                    (
                        (
                            f"`{item['name']}`",
                            ", ".join(item["origins"]),
                            _truncate(_describe(item), 110),
                        )
                        for item in sorted(group, key=lambda i: (i["origins"][0], i["name"]))
                    ),
                ),
                "",
            ]

    ranked = sorted(
        _collapse_copies(i for i in graph["items"] if i["kind"] != "hook"),
        key=lambda i: i["descriptionChars"],
        reverse=True,
    )[:top]
    if ranked:
        out += [
            "<br/>",
            "",
            f"## Context budget — top {len(ranked)} by description size",
            "",
            *_table(
                ("Item", "Kind", "Chars", "~Tokens"),
                (
                    (
                        f"`{item['name']}`",
                        item["kind"],
                        f"{item['descriptionChars']:,}",
                        f"{item['descriptionChars'] // CHARS_PER_TOKEN:,}",
                    )
                    for item in ranked
                ),
            ),
            "",
        ]

    return "\n".join(out)


def _truncate(text: str, limit: int) -> str:
    flat = " ".join(text.split()).replace("|", "\\|")
    return flat if len(flat) <= limit else flat[: limit - 1] + "…"


def emit_json(payload: dict[str, Any]) -> int:
    """Write a report to stdout as JSON. Always succeeds, so the exit code is 0."""
    json.dump(payload, sys.stdout, indent=2, ensure_ascii=False)
    sys.stdout.write("\n")
    return 0


def write_out(target: str, rendered: str, graph: dict[str, Any], summary: str) -> int:
    """Write an ``--out`` report, refusing to land inside a scanned tree.

    A report names every asset it found, so writing one back into a config root
    would make the next run inventory its own output — and, in a repo that
    publishes its `.claude/`, would commit real machine paths. The promise not
    to write into a scanned tree only holds if it is enforced here.
    """
    # `resolve()` already anchors a relative path to the working directory.
    resolved = Path(target).expanduser().resolve()

    for root in graph["roots"]:
        root_path = Path(root["path"]).resolve()
        if resolved == root_path or root_path in resolved.parents:
            print(
                f"refusing to write {resolved}: inside the scanned config root "
                f"{root_path}. Choose a path outside it.",
                file=sys.stderr,
            )
            return 2

    resolved.write_text(rendered, encoding="utf-8")
    print(f"wrote {resolved} ({summary})")
    return 0


# --------------------------------------------------------------------------
# cli
# --------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="census", description=__doc__.splitlines()[0])
    parser.add_argument("--config", help="path to a census config file")
    sub = parser.add_subparsers(dest="command", required=True)

    def add_config(subparser: argparse.ArgumentParser) -> argparse.ArgumentParser:
        """Accept --config on either side of the subcommand.

        argparse only sees a top-level option before the subcommand, but that is
        not where anyone types it. SUPPRESS is what makes both positions work:
        without it the subparser's own default would overwrite a value already
        parsed from the top level.
        """
        subparser.add_argument(
            "--config", default=argparse.SUPPRESS, help="path to a census config file"
        )
        return subparser

    add_config(sub.add_parser("scan", help="emit the normalized asset graph as JSON"))

    catalog = add_config(sub.add_parser("catalog", help="render a Markdown catalog"))
    catalog.add_argument("--out", help="write to this file instead of stdout")
    catalog.add_argument(
        "--top", type=int, default=10, help="context-budget ranking size (default: 10)"
    )

    port = add_config(
        sub.add_parser("portability", help="grade items by machine-specific coupling")
    )
    port.add_argument("--out", help="write to this file instead of stdout")
    port.add_argument(
        "--evidence", type=int, default=3, help="hits shown per item (0 to omit; default: 3)"
    )
    port.add_argument("--json", action="store_true", help="emit the raw report as JSON")

    drift = add_config(
        sub.add_parser("drift", help="report duplicates, pair gaps and frontmatter defects")
    )
    drift.add_argument("--out", help="write to this file instead of stdout")
    drift.add_argument(
        "--limit", type=int, default=15, help="findings shown per severity (0 for all; default: 15)"
    )
    drift.add_argument("--json", action="store_true", help="emit the raw report as JSON")

    args = parser.parse_args(argv)
    config, origin = load_config(args.config)
    graph = build_graph(config, origin)

    if args.command == "scan":
        return emit_json(graph)

    # Each report decides only what it renders and how to summarize itself; the
    # three ways of emitting it are the same for all of them and stated once.
    if args.command == "portability":
        report = build_portability(graph, config)
        if args.json:
            return emit_json(report)
        rendered = render_portability(report, args.evidence)
        summary = f"{sum(report['tiers'].values())} items graded"
    elif args.command == "drift":
        report = build_drift(graph)
        if args.json:
            return emit_json(report)
        rendered = render_drift(report, args.limit)
        summary = f"{len(report['findings'])} findings"
    else:
        rendered = render_catalog(graph, args.top)
        summary = f"{graph['stats']['total']} items"

    if args.out:
        return write_out(args.out, rendered, graph, summary)
    sys.stdout.write(rendered)
    return 0


if __name__ == "__main__":
    sys.exit(main())
