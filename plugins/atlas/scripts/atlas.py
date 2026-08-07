#!/usr/bin/env python3
"""atlas — a browsable map of every Claude Code resource a project can reach.

Answers a different question from a config audit. An audit asks "what have I
accumulated across every root"; this asks "standing in THIS directory, what is
actually reachable right now, and where does each piece come from". A session
resolves four layers at once and reports none of them together:

  1. the user config directory      (~/.claude, or $CLAUDE_CONFIG_DIR)
  2. this project's .claude/        (plus settings.local.json)
  3. every installed plugin         (~/.claude/plugins/installed_plugins.json)
  4. project MCP servers            (.mcp.json)

Design contract:
  - This script SCANS and RENDERS. It never edits a scanned tree. The only files
    it touches are the viewers it produces — which includes deleting its own
    stale ones out of the temp directory, since nothing else ever would.
  - stdlib only. A tool you reach for to understand your setup must not need a
    setup of its own.
  - The viewer it PRODUCES is one self-contained file. No server, no CDN, no
    build step: every byte of CSS and JS ends up inline, so it opens offline and
    can be handed to someone else as a single attachment. The source of that
    page is templates/page.html next to this script, not a literal in it.

Subcommands:
  view    build the HTML viewer and optionally open it in a browser. Name other
          projects to get one file each, plus a global-only map to compare them
          against.
  scan    emit the same graph as JSON, for piping somewhere else
  budget  rank what the always-on context is actually spent on
  diff    compare the current state against a scan saved earlier
"""

from __future__ import annotations

import argparse
import html
import json
import os
import platform
import re
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, NamedTuple

# Same ratio the rest of this marketplace uses when it estimates resident cost.
# It is an estimate and is always labelled as one.
CHARS_PER_TOKEN = 4

# Frontmatter keys worth surfacing as their own chip in the viewer. Everything
# else parsed out of the block is still carried, under `extra`.
SURFACED_KEYS = ("model", "tools", "allowed-tools", "argument-hint", "disable-model-invocation")

# Hook events, in the order a session would encounter them. Anything not listed
# still renders; this only fixes the order of the ones we know.
HOOK_EVENT_ORDER = (
    "SessionStart",
    "UserPromptSubmit",
    "PreToolUse",
    "PostToolUse",
    "Notification",
    "PreCompact",
    "Stop",
    "SubagentStop",
    "SessionEnd",
)

KIND_ORDER = ("command", "agent", "skill", "hook", "mcp", "memory", "plugin")

# The three scopes an item can come from, in resolution order. The JSON keeps
# `user` — it is the name Claude Code's own docs use for the config directory —
# but everywhere a person reads it, it is labelled "Global", which is what it
# actually is next to a repo's own `.claude/`.
LAYER_ORDER = ("user", "project", "plugin")
LAYER_LABEL = {"user": "Global", "project": "Project", "plugin": "Plugins"}

# A translation mirror directory: `agents-ko`, `commands-ja`, `skills-zh`. The
# language is whatever suffix is there — no list of known codes, because the
# next one someone invents should work without an edit here.
LANG_DIR = re.compile(r"^(agents|commands|skills)-([a-z]{2,3})$")

# Plural forms, because "1 memorys" reads like a bug in the tool rather than a
# detail of English.
KIND_PLURAL = {
    "command": "commands",
    "agent": "agents",
    "skill": "skills",
    "hook": "hooks",
    "mcp": "MCP servers",
    "memory": "memory files",
    "plugin": "plugins",
}


# --------------------------------------------------------------------------
# frontmatter
# --------------------------------------------------------------------------


def unquote_scalar(value: str) -> str:
    """Strip a YAML scalar's surrounding quotes and undo its escaping.

    A `description` is quoted on disk whenever it contains a `: ` or a leading
    indicator character, which is most of them. Without this the viewer would
    render the quoting syntax itself as part of the text.
    """
    value = value.strip()
    if len(value) >= 2 and value[0] == value[-1]:
        if value[0] == "'":
            return value[1:-1].replace("''", "'")
        if value[0] == '"':
            return re.sub(r"\\(.)", r"\1", value[1:-1])
    return value


def parse_frontmatter(text: str) -> tuple[dict[str, str], str]:
    """Return (frontmatter, body). A file without a block yields ({}, text).

    Deliberately a line parser rather than a YAML one: `yaml` is not in the
    standard library, and requiring an install to read a file that Claude Code
    itself reads with a tolerant parser would be the wrong trade. Continuation
    lines of a folded scalar are appended to the key they belong to, so a
    multi-line description survives instead of being truncated at the newline.
    """
    if not text.startswith("---"):
        return {}, text

    parts = text.split("---", 2)
    if len(parts) < 3:
        return {}, text

    block, body = parts[1], parts[2]
    data: dict[str, str] = {}
    key: str | None = None
    for line in block.splitlines():
        if not line.strip():
            continue
        match = re.match(r"^([A-Za-z0-9_-]+):\s*(.*)$", line)
        if match:
            key = match.group(1)
            data[key] = unquote_scalar(match.group(2))
        elif key and line[:1] in " \t":
            data[key] = (data[key] + " " + line.strip()).strip()
    return data, body.lstrip("\n")


# --------------------------------------------------------------------------
# paths
# --------------------------------------------------------------------------


def collapse_home(path: Path) -> str:
    """Render a path with ~ for the home directory, so the viewer is shareable."""
    home = str(Path.home())
    text = str(path)
    if text == home:
        return "~"
    if text.startswith(home + os.sep):
        return "~" + text[len(home) :]
    return text


def expand_home(text: str) -> Path:
    """Inverse of collapse_home. Only a leading ~ is a home marker — a tilde
    anywhere else is an ordinary character in a file name."""
    if text == "~":
        return Path.home()
    if text.startswith("~" + os.sep):
        return Path.home() / text[2:]
    return Path(text)


def user_config_dir(explicit: str | None) -> Path:
    if explicit:
        return Path(explicit).expanduser().resolve()
    env = os.environ.get("CLAUDE_CONFIG_DIR")
    if env:
        return Path(env).expanduser().resolve()
    return Path.home() / ".claude"


def read_json(path: Path) -> Any:
    """Read JSON, returning None rather than raising. A malformed settings file
    is a finding to report, not a reason for the whole map to fail."""
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""


# --------------------------------------------------------------------------
# collection
# --------------------------------------------------------------------------


class _Origin(NamedTuple):
    """Where a directory's items come from, carried as one value.

    The three travel together through every collector and are written onto every
    item unchanged; passing them separately meant repeating `namespace or ""` at
    each call site, which is exactly the kind of thing that drifts.
    """

    layer: str
    origin: str
    namespace: str

    def fields(self) -> dict[str, str]:
        return {"layer": self.layer, "origin": self.origin, "namespace": self.namespace}


# Translation mirrors, keyed by the (kind, key) of the item they translate.
Mirrors = dict[tuple[str, str], dict[str, Any]]


class Collector:
    """Builds the flat item list. One instance per run."""

    def __init__(self, max_body: int, include_bodies: bool) -> None:
        self.items: list[dict[str, Any]] = []
        self.notes: list[str] = []
        self.languages: set[str] = set()
        self.max_body = max_body
        self.include_bodies = include_bodies

    # -- helpers ---------------------------------------------------------

    def _body(self, text: str) -> tuple[str, bool]:
        if not self.include_bodies:
            return "", False
        text = text.strip()
        if len(text) > self.max_body:
            return text[: self.max_body], True
        return text, False

    def add(self, **item: Any) -> None:
        description = item.get("description") or ""
        item.setdefault("extra", {})
        item["descriptionChars"] = len(item.get("name") or "") + len(description)
        item["descriptionTokens"] = item["descriptionChars"] // CHARS_PER_TOKEN
        self.items.append(item)

    # -- markdown-defined resources --------------------------------------

    def collect_translations(self, config_dir: Path) -> Mirrors:
        """Collect `<kind>-<lang>` mirror directories, keyed by (kind, key).

        A translation mirror is not a second resource — Claude Code loads only
        the source directory, so the mirror costs nothing at runtime and must
        not be counted as another agent. It is attached to the item it mirrors
        and swapped in by the viewer's language selector.

        Matching is by path, never by the `name:` field: a mirror whose
        frontmatter name was translated too would otherwise fail to pair with
        the file it is plainly a translation of.
        """
        found: Mirrors = {}
        if not config_dir.is_dir():
            return found

        for directory in sorted(config_dir.iterdir()):
            if not directory.is_dir():
                continue
            match = LANG_DIR.match(directory.name)
            if not match:
                continue
            kind_dir, lang = match.group(1), match.group(2)
            kind = {"agents": "agent", "commands": "command", "skills": "skill"}[kind_dir]
            paths = (
                directory.rglob("SKILL.md") if kind == "skill" else directory.rglob("*.md")
            )
            for path in sorted(paths):
                if kind == "skill":
                    key = path.parent.relative_to(directory).as_posix()
                else:
                    key = ":".join(path.relative_to(directory).with_suffix("").parts)
                meta, body = parse_frontmatter(read_text(path))
                text, _ = self._body(body)
                entry = found.setdefault((kind, key), {})
                entry[lang] = {
                    "description": meta.get("description", ""),
                    "body": text,
                    "path": collapse_home(path),
                }
                self.languages.add(lang)
        return found

    def collect_config_dir(
        self, config_dir: Path, layer: str, origin: str, namespace: str | None = None
    ) -> dict[str, int]:
        """Collect agents, commands and skills laid out the standard way.

        `namespace` is the plugin name when the directory belongs to a plugin:
        its commands and skills are addressed as /<plugin>:<name>, which is what
        makes them unable to collide with a user's own.
        """
        if not config_dir.is_dir():
            return {"agent": 0, "command": 0, "skill": 0}

        mirrors = self.collect_translations(config_dir)
        # Which languages this directory keeps mirrors in at all. An item here
        # with no mirror is a gap only against this list.
        here = sorted({lang for entry in mirrors.values() for lang in entry})
        where = _Origin(layer=layer, origin=origin, namespace=namespace or "")

        return {
            "agent": self._collect_agents(config_dir, where, mirrors, here),
            "command": self._collect_commands(config_dir, where, mirrors, here),
            "skill": self._collect_skills(config_dir, where, mirrors, here),
        }

    def _collect_agents(
        self, config_dir: Path, where: _Origin, mirrors: Mirrors, langs: list[str]
    ) -> int:
        directory = config_dir / "agents"
        count = 0
        for path in sorted(directory.rglob("*.md")):
            meta, body = parse_frontmatter(read_text(path))
            key = path.relative_to(directory).with_suffix("").as_posix()
            text, truncated = self._body(body)
            self.add(
                kind="agent",
                name=meta.get("name") or path.stem,
                key=key,
                translations=mirrors.get(("agent", key), {}),
                mirrorLangs=langs,
                # An agent is not typed as a slash command; it is selected by
                # its description. Showing a fake invocation would teach the
                # wrong thing, so the field stays empty and the viewer says so.
                invocation="",
                description=meta.get("description", ""),
                **where.fields(),
                path=collapse_home(path),
                body=text,
                truncated=truncated,
                extra=self._extra(meta),
            )
            count += 1
        return count

    def _collect_commands(
        self, config_dir: Path, where: _Origin, mirrors: Mirrors, langs: list[str]
    ) -> int:
        directory = config_dir / "commands"
        count = 0
        for path in sorted(directory.rglob("*.md")):
            meta, body = parse_frontmatter(read_text(path))
            # A subdirectory namespaces the command; the file name is the last
            # segment. A command has no `name:` field — the path IS the name.
            local = ":".join(path.relative_to(directory).with_suffix("").parts)
            text, truncated = self._body(body)
            self.add(
                kind="command",
                name=local,
                key=local,
                translations=mirrors.get(("command", local), {}),
                mirrorLangs=langs,
                invocation=f"/{where.namespace}:{local}" if where.namespace else f"/{local}",
                description=meta.get("description", ""),
                **where.fields(),
                path=collapse_home(path),
                body=text,
                truncated=truncated,
                extra=self._extra(meta),
            )
            count += 1
        return count

    def _collect_skills(
        self, config_dir: Path, where: _Origin, mirrors: Mirrors, langs: list[str]
    ) -> int:
        directory = config_dir / "skills"
        count = 0
        for path in sorted(directory.rglob("SKILL.md")):
            meta, body = parse_frontmatter(read_text(path))
            name = meta.get("name") or path.parent.name
            key = path.parent.relative_to(directory).as_posix() if directory.is_dir() else name
            text, truncated = self._body(body)
            # A skill can ship references, scripts and templates beside its
            # SKILL.md. They are not separate resources, but their number says
            # whether this is a prompt or a small program.
            bundled = sorted(
                collapse_home(p)
                for p in path.parent.rglob("*")
                if p.is_file() and p.name != "SKILL.md"
            )
            extra = self._extra(meta)
            if bundled:
                extra["bundled files"] = f"{len(bundled)}"
            self.add(
                kind="skill",
                name=name,
                key=key,
                translations=mirrors.get(("skill", key), {}),
                mirrorLangs=langs,
                invocation=f"/{where.namespace}:{name}" if where.namespace else f"/{name}",
                description=meta.get("description", ""),
                **where.fields(),
                path=collapse_home(path),
                body=text,
                truncated=truncated,
                extra=extra,
                bundled=bundled,
            )
            count += 1
        return count

    @staticmethod
    def _extra(meta: dict[str, str]) -> dict[str, str]:
        extra = {k: meta[k] for k in SURFACED_KEYS if meta.get(k)}
        for key, value in meta.items():
            if key in ("name", "description") or key in SURFACED_KEYS:
                continue
            if value:
                extra[key] = value
        return extra

    # -- hooks -----------------------------------------------------------

    def collect_hooks(
        self, source: Path, hooks: Any, layer: str, origin: str, base: Path, namespace: str = ""
    ) -> int:
        """Flatten a hooks block into one item per registered command.

        A hook is a pointer, and its quietest failure is that the target is not
        there: the registration still ships, the hook never fires, and nothing
        says so. So each item carries whether its script resolves.
        """
        if not isinstance(hooks, dict):
            return 0
        count = 0
        for event, entries in hooks.items():
            if not isinstance(entries, list):
                continue
            for entry in entries:
                if not isinstance(entry, dict):
                    continue
                matcher = entry.get("matcher") or "*"
                for hook in entry.get("hooks", []) or []:
                    if not isinstance(hook, dict):
                        continue
                    command = str(hook.get("command", "")).strip()
                    if not command:
                        continue
                    script, exists = self._resolve_hook_target(command, base)
                    name = Path(script).name if script else command.split()[0]
                    extra = {
                        "event": event,
                        "matcher": matcher,
                        "type": str(hook.get("type", "command")),
                    }
                    if script:
                        extra["script"] = collapse_home(Path(script))
                        extra["resolves"] = "yes" if exists else "NO — the file is not there"
                    if hook.get("timeout"):
                        extra["timeout"] = str(hook["timeout"])
                    self.add(
                        kind="hook",
                        name=name,
                        invocation=f"{event}:{matcher}",
                        # Home-collapsed so the viewer stays handable to someone
                        # else; the hook's own text is unchanged on disk.
                        description=command.replace(str(Path.home()), "~"),
                        layer=layer,
                        origin=origin,
                        namespace=namespace,
                        path=collapse_home(source),
                        body=read_text(Path(script).expanduser()) if script and exists else "",
                        truncated=False,
                        extra=extra,
                        event=event,
                        matcher=matcher,
                        command=command,
                        script=collapse_home(Path(script)) if script else "",
                        resolves=exists,
                    )
                    count += 1
        return count

    @staticmethod
    def _resolve_hook_target(command: str, base: Path) -> tuple[str, bool]:
        """Return (script path, exists). An inline shell hook resolves to ("", True).

        Only the first word is a candidate, and only when it looks like a file
        rather than a program on PATH — which is what separates `guard.sh` from
        `jq`, and keeps an inline one-liner from being reported as a missing
        script.
        """
        first = command.split()[0] if command.split() else ""
        first = first.strip("\"'")
        expanded = first.replace("${CLAUDE_PLUGIN_ROOT}", str(base)).replace(
            "$CLAUDE_PLUGIN_ROOT", str(base)
        )
        expanded = os.path.expandvars(expanded)
        looks_like_path = "/" in expanded or expanded.startswith("~")
        has_script_suffix = Path(expanded).suffix in (
            ".sh",
            ".bash",
            ".zsh",
            ".py",
            ".js",
            ".mjs",
            ".ts",
            ".rb",
            ".pl",
        )
        if not (looks_like_path or has_script_suffix):
            return "", True
        target = Path(expanded).expanduser()
        if not target.is_absolute():
            target = (base / target).resolve()
        return str(target), target.is_file()

    def collect_settings(self, path: Path, layer: str, origin: str, base: Path) -> None:
        if not path.is_file():
            return
        data = read_json(path)
        if data is None:
            self.notes.append(f"{collapse_home(path)} is not valid JSON — its hooks were skipped")
            return
        self.collect_hooks(path, data.get("hooks"), layer, origin, base)

    # -- memory ----------------------------------------------------------

    def collect_memory(self, path: Path, layer: str, origin: str) -> None:
        if not path.is_file():
            return
        text = read_text(path)
        body, truncated = self._body(text)
        first = next((line.strip() for line in text.splitlines() if line.strip()), "")
        self.add(
            kind="memory",
            name=path.name,
            invocation="",
            # A CLAUDE.md is resident in full, so its own first line is the most
            # honest one-line summary available.
            description=first.lstrip("# ").strip(),
            layer=layer,
            origin=origin,
            namespace="",
            path=collapse_home(path),
            body=body,
            truncated=truncated,
            extra={"resident": "whole file", "chars": f"{len(text):,}"},
            residentChars=len(text),
        )

    # -- mcp -------------------------------------------------------------

    def collect_mcp(self, path: Path, layer: str, origin: str) -> None:
        if not path.is_file():
            return
        data = read_json(path)
        if data is None:
            self.notes.append(f"{collapse_home(path)} is not valid JSON — its servers were skipped")
            return
        servers = data.get("mcpServers")
        if not isinstance(servers, dict):
            return
        for name, spec in servers.items():
            if not isinstance(spec, dict):
                continue
            if spec.get("url"):
                summary = str(spec["url"])
                transport = spec.get("type", "http")
            else:
                argv = [str(spec.get("command", ""))] + [str(a) for a in spec.get("args", []) or []]
                summary = " ".join(a for a in argv if a)
                transport = spec.get("type", "stdio")
            extra = {"transport": str(transport)}
            if spec.get("env"):
                # Names only. The values are exactly the kind of thing that must
                # not be baked into a file someone might hand to a colleague.
                extra["env"] = ", ".join(sorted(spec["env"]))
            self.add(
                kind="mcp",
                name=name,
                invocation=f"mcp__{name}__*",
                description=summary,
                layer=layer,
                origin=origin,
                namespace="",
                path=collapse_home(path),
                body="",
                truncated=False,
                extra=extra,
            )


# --------------------------------------------------------------------------
# plugins
# --------------------------------------------------------------------------


def installed_plugins(plugins_root: Path) -> list[dict[str, Any]]:
    """Resolve installed plugins to (name, marketplace, version, path).

    The registry is versioned, so read it defensively: an unknown shape should
    cost the plugin layer, not the whole map.
    """
    registry = read_json(plugins_root / "installed_plugins.json")
    if not isinstance(registry, dict):
        return []
    found: list[dict[str, Any]] = []
    plugins = registry.get("plugins")
    if not isinstance(plugins, dict):
        return []
    for key, installs in plugins.items():
        name, _, marketplace = key.partition("@")
        if not isinstance(installs, list):
            continue
        for install in installs:
            if not isinstance(install, dict):
                continue
            path = install.get("installPath")
            if not path:
                continue
            found.append(
                {
                    "name": name,
                    "marketplace": marketplace,
                    "version": str(install.get("version", "")),
                    "scope": str(install.get("scope", "")),
                    "path": Path(path),
                }
            )
    return found


def enabled_state(settings_paths: Iterable[Path]) -> dict[str, bool]:
    """Merge every settings file's enabledPlugins, later files winning.

    Pass them in precedence order, lowest first — a project setting overrides a
    user one, exactly as the session resolves it.
    """
    state: dict[str, bool] = {}
    for path in settings_paths:
        data = read_json(path) if path.is_file() else None
        if not isinstance(data, dict):
            continue
        entries = data.get("enabledPlugins")
        if isinstance(entries, dict):
            for key, value in entries.items():
                state[str(key).partition("@")[0]] = bool(value)
        elif isinstance(entries, list):
            for key in entries:
                state[str(key).partition("@")[0]] = True
    return state


# --------------------------------------------------------------------------
# graph
# --------------------------------------------------------------------------


def build_graph(
    project: Path,
    user_root: Path,
    plugins_root: Path,
    max_body: int,
    include_bodies: bool,
    include_project: bool = True,
) -> dict[str, Any]:
    """Map the layers a session resolves.

    With `include_project` false the project layer is skipped entirely: the map
    becomes the global one — the user config and the installed plugins, the part
    that is the same in every repository. Useful as its own file to compare a
    repo against, which is why `view` can emit both at once.
    """
    collector = Collector(max_body=max_body, include_bodies=include_bodies)
    roots: list[dict[str, Any]] = []

    # Layer 1 — the user config directory.
    if user_root.is_dir():
        counts = collector.collect_config_dir(user_root, "user", collapse_home(user_root))
        collector.collect_settings(user_root / "settings.json", "user", "settings.json", user_root)
        collector.collect_settings(
            user_root / "settings.local.json", "user", "settings.local.json", user_root
        )
        collector.collect_memory(user_root / "CLAUDE.md", "user", collapse_home(user_root))
        roots.append({"layer": "user", "path": collapse_home(user_root), "counts": counts})
    else:
        collector.notes.append(f"no user config directory at {collapse_home(user_root)}")

    # Layer 2 — this project.
    project_config = project / ".claude"
    if not include_project:
        collector.notes.append(
            "global view — the user config and installed plugins only. "
            "No project layer was scanned, so nothing here is repo-specific."
        )
    elif project_config.is_dir() or (project / "CLAUDE.md").is_file():
        counts = collector.collect_config_dir(project_config, "project", project.name)
        collector.collect_settings(
            project_config / "settings.json", "project", "settings.json", project_config
        )
        collector.collect_settings(
            project_config / "settings.local.json",
            "project",
            "settings.local.json",
            project_config,
        )
        collector.collect_memory(project / "CLAUDE.md", "project", project.name)
        collector.collect_mcp(project / ".mcp.json", "project", project.name)
        roots.append({"layer": "project", "path": collapse_home(project), "counts": counts})
    else:
        collector.notes.append(
            f"{collapse_home(project)} has no .claude/ and no CLAUDE.md — "
            "only the user and plugin layers are shown"
        )
        collector.collect_mcp(project / ".mcp.json", "project", project.name)

    # Layer 3 — installed plugins. A global view resolves enablement from the
    # user settings alone; a project's override is part of the layer it skipped.
    settings_chain = [user_root / "settings.json"]
    if include_project:
        settings_chain += [
            project_config / "settings.json",
            project_config / "settings.local.json",
        ]
    state = enabled_state(settings_chain)
    for plugin in installed_plugins(plugins_root):
        path = plugin["path"]
        label = plugin["name"]
        enabled = state.get(plugin["name"], True)
        if not path.is_dir():
            collector.notes.append(
                f"plugin {label} is registered at {collapse_home(path)}, which does not exist"
            )
            continue
        counts = collector.collect_config_dir(path, "plugin", label, namespace=plugin["name"])
        hooks_manifest = path / "hooks" / "hooks.json"
        hook_count = 0
        if hooks_manifest.is_file():
            data = read_json(hooks_manifest)
            if data is None:
                collector.notes.append(f"{collapse_home(hooks_manifest)} is not valid JSON")
            else:
                hook_count = collector.collect_hooks(
                    hooks_manifest, data.get("hooks"), "plugin", label, path, plugin["name"]
                )
        manifest = read_json(path / ".claude-plugin" / "plugin.json") or {}
        total = sum(counts.values()) + hook_count
        collector.add(
            kind="plugin",
            name=plugin["name"],
            invocation=f"/{plugin['name']}:…" if counts["command"] or counts["skill"] else "",
            description=str(manifest.get("description", "")),
            layer="plugin",
            origin=label,
            namespace=plugin["name"],
            path=collapse_home(path),
            body="",
            truncated=False,
            extra={
                "version": plugin["version"] or str(manifest.get("version", "")),
                "marketplace": plugin["marketplace"],
                "scope": plugin["scope"],
                "provides": f"{counts['command']} commands, {counts['agent']} agents, "
                f"{counts['skill']} skills, {hook_count} hooks",
                "enabled": "yes" if enabled else "no",
            },
            enabled=enabled,
        )
        roots.append(
            {
                "layer": "plugin",
                "path": collapse_home(path),
                "name": plugin["name"],
                "counts": {**counts, "hook": hook_count},
                "total": total,
                "enabled": enabled,
            }
        )

    items = collector.items
    # Hooks read best in the order a session encounters them, not the order the
    # settings files happened to list them in. Only the hook rows move; every
    # other kind keeps its collection order.
    rank = {event: index for index, event in enumerate(HOOK_EVENT_ORDER)}
    hooks = sorted(
        (item for item in items if item["kind"] == "hook"),
        key=lambda i: (rank.get(i.get("event", ""), len(rank)), i.get("event", ""), i["name"]),
    )
    ordered = iter(hooks)
    items = [next(ordered) if item["kind"] == "hook" else item for item in items]

    conflicts = detect_conflicts(items)
    return {
        "generatedAt": datetime.now(timezone.utc).astimezone().strftime("%Y-%m-%d %H:%M %Z"),
        # What this map is OF. A global build is not a map of a project that
        # happens to be empty, so it names itself rather than borrowing the
        # directory the command was run from.
        "scope": "project" if include_project else "global",
        "project": collapse_home(project if include_project else user_root),
        "projectName": project.name if include_project else "global",
        "userRoot": collapse_home(user_root),
        "roots": roots,
        "items": items,
        "conflicts": conflicts,
        # Mirror languages found anywhere. The source directory is what Claude
        # Code loads, so these cost nothing at runtime — the viewer offers them
        # as a reading language, never as extra resources.
        "languages": sorted(collector.languages),
        "notes": collector.notes,
        "stats": summarize(items),
    }


def detect_conflicts(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Report names that resolve to more than one definition.

    A plugin's commands and skills are namespaced, so they cannot collide with
    a user's own — only agents share one flat namespace across all three
    layers. A winner is named only where precedence is documented (a project
    definition overrides a user one); anything else is reported as ambiguous
    rather than guessed at.
    """
    groups: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for item in items:
        if item["kind"] in ("hook", "plugin", "memory", "mcp"):
            continue
        if item["kind"] in ("command", "skill") and item["namespace"]:
            continue  # namespaced by its plugin; cannot be shadowed
        groups.setdefault((item["kind"], item["name"]), []).append(item)

    conflicts = []
    for (kind, name), group in sorted(groups.items()):
        if len(group) < 2:
            continue
        layers = {item["layer"] for item in group}
        if layers == {"project", "user"} or layers == {"project"} or layers == {"user"}:
            winner = "project" if "project" in layers else "user"
            verdict = f"the {winner}-level definition wins"
        else:
            verdict = "which one wins is not documented — treat it as ambiguous"
        for item in group:
            item["conflicted"] = True
        conflicts.append(
            {
                "kind": kind,
                "name": name,
                "verdict": verdict,
                "where": [
                    {"layer": i["layer"], "origin": i["origin"], "path": i["path"]} for i in group
                ],
            }
        )
    return conflicts


def summarize(items: list[dict[str, Any]]) -> dict[str, Any]:
    by_kind: dict[str, int] = {}
    by_layer: dict[str, int] = {}
    resident = 0
    # The same resident total, split by where it comes from. One number for the
    # whole session says the bill is large; this says which scope to go edit.
    resident_by_layer: dict[str, int] = {}
    for item in items:
        by_kind[item["kind"]] = by_kind.get(item["kind"], 0) + 1
        by_layer[item["layer"]] = by_layer.get(item["layer"], 0) + 1
        # What a session pays before you type anything: the name and description
        # of every routable item, plus every CLAUDE.md in full.
        if item["kind"] in ("agent", "command", "skill"):
            cost = item["descriptionChars"]
        elif item["kind"] == "memory":
            cost = item.get("residentChars", 0)
        else:
            continue
        resident += cost
        resident_by_layer[item["layer"]] = resident_by_layer.get(item["layer"], 0) + cost
    return {
        "total": sum(v for k, v in by_kind.items()),
        "byKind": by_kind,
        "byLayer": by_layer,
        "residentChars": resident,
        "residentTokens": resident // CHARS_PER_TOKEN,
        "residentByLayer": resident_by_layer,
        "residentTokensByLayer": {
            layer: chars // CHARS_PER_TOKEN for layer, chars in resident_by_layer.items()
        },
        "unresolvedHooks": sum(
            1 for i in items if i["kind"] == "hook" and not i.get("resolves", True)
        ),
        "missingDescriptions": sum(
            1
            for i in items
            if i["kind"] in ("agent", "command", "skill") and not i.get("description")
        ),
    }


# --------------------------------------------------------------------------
# rendering
# --------------------------------------------------------------------------

# The viewer's markup lives in templates/page.html rather than in a literal
# here. Not only for its length: inside a Python string every backslash in the
# page's JavaScript had to be written twice — 54 of them across 23 lines — and a
# single missed pair fails silently at runtime rather than at parse time. As its
# own file it is ordinary HTML that an editor highlights and a test can load
# directly, without first building a page to extract it back out of.
TEMPLATE = Path(__file__).resolve().parent.parent / "templates" / "page.html"


def load_template() -> str:
    """Read the viewer template, or say plainly what is missing.

    The template ships alongside this script. A bare traceback here would name
    a path without explaining that the install itself is incomplete.
    """
    try:
        return TEMPLATE.read_text(encoding="utf-8")
    except OSError as exc:
        raise SystemExit(
            f"atlas: cannot read the viewer template at {collapse_home(TEMPLATE)} ({exc}).\n"
            f"       It ships next to scripts/atlas.py — reinstall the plugin if it is gone."
        ) from exc


def render_html(graph: dict[str, Any]) -> str:
    heading = f"Claude Code atlas — {graph['projectName']}"
    lead = (
        "The user config and installed plugins, from"
        if graph.get("scope") == "global"
        else "Everything reachable from"
    )
    # `<` cannot appear outside a JSON string, so escaping every one of them is
    # enough to keep an embedded `</script>` from ending the block early.
    data = json.dumps(graph, ensure_ascii=False).replace("<", "\\u003c")
    return (
        load_template()
        .replace("__TITLE__", html.escape(heading))
        .replace("__HEADING__", html.escape(heading))
        .replace("__LEAD__", lead)
        .replace("__PROJECT__", html.escape(graph["project"]))
        .replace("__GENERATED__", html.escape(graph["generatedAt"]))
        .replace("__DATA__", data)
    )


# --------------------------------------------------------------------------
# output
# --------------------------------------------------------------------------


def viewer_name(label: str) -> str:
    """The file name a viewer for `label` gets. One name per subject, so a
    re-run of the same subject overwrites rather than accumulating."""
    safe = re.sub(r"[^A-Za-z0-9._-]", "-", label) or "project"
    return f"claude-atlas-{safe}.html"


def default_out(label: str) -> Path:
    """Write outside the project by default.

    A viewer dropped into the repository is one `git add .` away from being
    committed, so the default lands in the temp directory and the path is
    printed. `--out` puts it wherever the user actually wants it.
    """
    return Path(tempfile.gettempdir()) / viewer_name(label)


# What a viewer is called, and a string only a viewer contains. Both must match
# before a file is deleted — a glob alone would be willing to remove someone
# else's file that happened to be named the same way.
VIEWER_GLOB = "claude-atlas-*.html"
VIEWER_MARKER = "Claude Code atlas"


def prune_old_viewers(spare: Iterable[Path]) -> list[Path]:
    """Delete viewers left in the temp directory by earlier runs.

    Re-running in the same project already overwrites: the name is derived from
    the subject, not the timestamp. What accumulates is one multi-megabyte file
    per project ever mapped, in a directory nobody opens. This sweeps those.

    Deliberately narrow. Only the temp directory — an `--out` the user picked is
    theirs to manage — only atlas's own file name, and only after reading the
    file's own title back out of it.
    """
    kept = {path.resolve() for path in spare}
    removed: list[Path] = []
    for path in sorted(Path(tempfile.gettempdir()).glob(VIEWER_GLOB)):
        if not path.is_file() or path.resolve() in kept:
            continue
        try:
            with path.open("r", encoding="utf-8", errors="replace") as handle:
                head = handle.read(2048)
        except OSError:
            continue
        if VIEWER_MARKER not in head:
            continue
        try:
            path.unlink()
        except OSError:
            continue
        removed.append(path)
    return removed


def resolve_project(text: str, session: Path) -> Path:
    """Turn a `view` argument into a directory.

    A path is used as given. A bare name is looked for next to the session
    project first — sibling checkouts are how repositories are usually laid out
    — and then under the home directory, so `atlas view acme-platform` works
    from inside another repo without typing the whole path.
    """
    candidate = Path(text).expanduser()
    tried = [candidate]
    if candidate.is_dir():
        return candidate.resolve()
    if not candidate.is_absolute() and os.sep not in text:
        for base in (session.parent, Path.home()):
            guess = base / text
            tried.append(guess)
            if guess.is_dir():
                return guess.resolve()
    where = "\n       ".join(collapse_home(p) for p in tried)
    raise SystemExit(f"atlas: no directory found for {text!r}. Tried:\n       {where}")


def guard_out(out: Path, roots: list[Path], project: Path) -> None:
    """Refuse to write inside a directory that was scanned."""
    resolved = out.resolve()
    for root in roots:
        try:
            resolved.relative_to(root.resolve())
        except ValueError:
            continue
        raise SystemExit(
            f"atlas: refusing to write into a scanned directory ({collapse_home(root)}).\n"
            f"       Pick an --out outside it, or omit --out for "
            f"{collapse_home(default_out(project.name))}."
        )


def open_in_browser(path: Path) -> None:
    system = platform.system()
    if system == "Darwin":
        argv = ["open", str(path)]
    elif system == "Windows":
        argv = ["cmd", "/c", "start", "", str(path)]
    else:
        argv = ["xdg-open", str(path)]
    try:
        subprocess.run(argv, check=False, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except OSError:
        print(f"atlas: could not open a browser; the file is at {path}", file=sys.stderr)


def print_report(graph: dict[str, Any], out: Path | None) -> None:
    s = graph["stats"]
    kinds = ", ".join(
        f"{s['byKind'][k]} {KIND_PLURAL.get(k, k)}" for k in KIND_ORDER if s["byKind"].get(k)
    )
    if graph.get("scope") == "global":
        print(f"Global: {graph['project']} (no project layer)")
    else:
        print(f"Project: {graph['project']}")
    print(f"Resources: {s['total']} ({kinds})")
    # Per scope, with its own slice of the always-on bill — a single total says
    # the cost is large, this says which directory to go open.
    per_layer = s.get("residentTokensByLayer", {})
    known = [layer for layer in LAYER_ORDER if s["byLayer"].get(layer)]
    rest = sorted(layer for layer in s["byLayer"] if layer not in LAYER_ORDER)
    scopes = " · ".join(
        f"{LAYER_LABEL.get(layer, layer)} {s['byLayer'][layer]}"
        + (f" (~{per_layer[layer]:,}t)" if per_layer.get(layer) else "")
        for layer in known + rest
    )
    print(f"By scope: {scopes}")
    print(f"Always-on context: ~{s['residentTokens']:,} tokens ({s['residentChars']:,} chars, estimate)")
    if graph["conflicts"]:
        print(f"Name conflicts: {len(graph['conflicts'])}")
        for conflict in graph["conflicts"]:
            where = ", ".join(w["origin"] for w in conflict["where"])
            print(f"  - {conflict['name']} ({conflict['kind']}) in {where} — {conflict['verdict']}")
    if s["unresolvedHooks"]:
        print(f"Hooks pointing at a missing script: {s['unresolvedHooks']}")
    if s["missingDescriptions"]:
        print(f"Items with no description: {s['missingDescriptions']}")
    for note in graph["notes"]:
        print(f"Note: {note}")
    if out is not None:
        print(f"Viewer: {out}")


# --------------------------------------------------------------------------
# budget
# --------------------------------------------------------------------------

# What a session pays for before you type: the name and description of every
# routable item, and every CLAUDE.md in full.
RESIDENT_KINDS = ("agent", "command", "skill")

BUDGET_AXES = {
    "kind": lambda i: KIND_PLURAL.get(i["kind"], i["kind"]),
    "scope": lambda i: LAYER_LABEL.get(i["layer"], i["layer"]),
    "origin": lambda i: i["origin"],
}


def item_chars(item: dict[str, Any]) -> int:
    """This item's share of the always-on bill, in characters.

    Characters rather than tokens because characters are the quantity that adds
    up exactly. Tokens are an estimate produced by a division, and flooring each
    item before summing gives a total that disagrees with flooring once at the
    end — a one-token discrepancy that reads as a bug in the arithmetic.
    """
    if item["kind"] in RESIDENT_KINDS:
        return int(item.get("descriptionChars", 0))
    if item["kind"] == "memory":
        return int(item.get("residentChars", 0))
    return 0


def item_tokens(item: dict[str, Any]) -> int:
    """The same share, estimated in tokens. For display and ranking only."""
    return item_chars(item) // CHARS_PER_TOKEN


def budget_report(graph: dict[str, Any], axis: str, top: int, over: int) -> dict[str, Any]:
    """Rank what the session is paying for, grouped and per item.

    The scope line a `view` prints answers "how much, and from where". This
    answers the question that follows it: which specific items, so there is
    something to go and shorten.
    """
    resident = [i for i in graph["items"] if item_chars(i)]
    key = BUDGET_AXES[axis]

    groups: dict[str, dict[str, int]] = {}
    for item in resident:
        bucket = groups.setdefault(key(item), {"chars": 0, "count": 0})
        bucket["chars"] += item_chars(item)
        bucket["count"] += 1

    ranked = sorted(resident, key=lambda i: (-item_chars(i), i["name"]))
    if over:
        ranked = [i for i in ranked if item_tokens(i) >= over]
    shown = ranked if over else ranked[:top]

    return {
        "axis": axis,
        "totalTokens": graph["stats"]["residentTokens"],
        "totalChars": graph["stats"]["residentChars"],
        "groups": [
            {"name": name, "tokens": bucket["chars"] // CHARS_PER_TOKEN, **bucket}
            for name, bucket in sorted(groups.items(), key=lambda kv: -kv[1]["chars"])
        ],
        "items": [
            {
                "name": i["name"],
                "kind": i["kind"],
                "origin": i["origin"],
                "layer": i["layer"],
                "tokens": item_tokens(i),
                "chars": item_chars(i),
                "path": i["path"],
            }
            for i in shown
        ],
        "itemsOverThreshold": len(ranked) if over else None,
        "residentItems": len(resident),
    }


def print_budget(report: dict[str, Any], graph: dict[str, Any], over: int) -> None:
    print(f"Project: {graph['project']}")
    print(
        f"Always-on context: ~{report['totalTokens']:,} tokens "
        f"({report['totalChars']:,} chars, estimate) across {report['residentItems']} items"
    )
    print()
    print(f"By {report['axis']}")
    for group in report["groups"]:
        share = group["chars"] * 100 // max(report["totalChars"], 1)
        print(
            f"  ~{group['tokens']:>7,}t  {share:>3}%  "
            f"{group['count']:>3} items  {group['name']}"
        )
    print()
    if over:
        print(f"Items at or over ~{over:,} tokens: {report['itemsOverThreshold']}")
    else:
        print(f"Heaviest {len(report['items'])} items")
    for item in report["items"]:
        print(
            f"  ~{item['tokens']:>7,}t  {item['kind']:<8} "
            f"{item['name']:<42.42} {item['origin']}"
        )
    if not report["items"]:
        print("  (none)")


# --------------------------------------------------------------------------
# diff
# --------------------------------------------------------------------------


def item_identity(item: dict[str, Any]) -> tuple[str, str, str, str]:
    """What makes an item the same item across two scans.

    The path is deliberately not part of it: moving `~/.claude/agents/x.md` into
    a subdirectory should read as the same agent, not as one deleted and one
    added. `key` is the addressable name where there is one and falls back to
    `name` for the kinds that have no key at all.
    """
    return (item["kind"], item["layer"], item["origin"], item.get("key") or item["name"])


def diff_report(before: dict[str, Any], after: dict[str, Any]) -> dict[str, Any]:
    """What changed between a saved scan and the current one.

    Written for the shape of work that motivates it: a campaign that rewrites
    many descriptions at once, where the question afterwards is not "is it
    smaller" but "which items moved, and did anything disappear by accident".
    """
    old = {item_identity(i): i for i in before.get("items", [])}
    new = {item_identity(i): i for i in after.get("items", [])}

    def entry(identity: tuple[str, str, str, str], item: dict[str, Any], delta: int) -> dict:
        return {
            "kind": identity[0],
            "name": item["name"],
            "origin": item["origin"],
            "layer": item["layer"],
            "chars": item_chars(item),
            "delta": delta,
        }

    added = [entry(k, new[k], item_chars(new[k])) for k in new.keys() - old.keys()]
    removed = [entry(k, old[k], -item_chars(old[k])) for k in old.keys() - new.keys()]
    changed = []
    for identity in old.keys() & new.keys():
        delta = item_chars(new[identity]) - item_chars(old[identity])
        if delta:
            changed.append(entry(identity, new[identity], delta))

    for group in (added, removed, changed):
        group.sort(key=lambda e: (-abs(e["delta"]), e["name"]))

    before_stats = before.get("stats", {})
    after_stats = after.get("stats", {})
    scopes = {}
    for layer in set(before_stats.get("residentByLayer", {})) | set(
        after_stats.get("residentByLayer", {})
    ):
        was = before_stats.get("residentByLayer", {}).get(layer, 0)
        now = after_stats.get("residentByLayer", {}).get(layer, 0)
        scopes[layer] = {"before": was, "after": now, "delta": now - was}

    return {
        "baselineGeneratedAt": before.get("generatedAt", "unknown"),
        "generatedAt": after["generatedAt"],
        "residentChars": {
            "before": before_stats.get("residentChars", 0),
            "after": after_stats.get("residentChars", 0),
            "delta": after_stats.get("residentChars", 0) - before_stats.get("residentChars", 0),
        },
        "byScope": scopes,
        "added": added,
        "removed": removed,
        "changed": changed,
    }


def print_diff(report: dict[str, Any], top: int) -> None:
    chars = report["residentChars"]
    print(f"Baseline: {report['baselineGeneratedAt']}")
    print(f"Now:      {report['generatedAt']}")
    print()
    print(
        f"Always-on context: {chars['before']:,} → {chars['after']:,} chars "
        f"({signed(chars['delta'])}, ~{signed(chars['delta'] // CHARS_PER_TOKEN)}t)"
    )
    for layer in LAYER_ORDER:
        scope = report["byScope"].get(layer)
        if not scope or not (scope["before"] or scope["after"]):
            continue
        print(
            f"  {LAYER_LABEL.get(layer, layer):<8} {scope['before']:>8,} → {scope['after']:>8,}"
            f"  {signed(scope['delta'])}"
        )

    for title, entries in (
        ("Removed", report["removed"]),
        ("Added", report["added"]),
        ("Changed", report["changed"]),
    ):
        if not entries:
            continue
        print()
        shown = entries[:top]
        more = len(entries) - len(shown)
        print(f"{title}: {len(entries)}")
        for e in shown:
            print(f"  {signed(e['delta']):>8}c  {e['kind']:<8} {e['name']:<40.40} {e['origin']}")
        if more:
            print(f"  … and {more} more")

    if not (report["added"] or report["removed"] or report["changed"]):
        print()
        print("Nothing changed.")


def signed(value: int) -> str:
    return f"{value:+,}"


# --------------------------------------------------------------------------
# cli
# --------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="atlas",
        description="Map every Claude Code resource a project can reach.",
    )
    parser.add_argument("--project", default=".", help="project directory (default: cwd)")
    parser.add_argument("--user-root", default=None, help="user config dir (default: ~/.claude)")
    parser.add_argument("--plugins-root", default=None, help="plugin registry dir")
    sub = parser.add_subparsers(dest="command")

    view = sub.add_parser("view", help="build the HTML viewer")
    view.add_argument(
        "targets",
        nargs="*",
        metavar="PROJECT",
        help="also map these projects; naming any of them additionally emits a "
        "global-only map, so you get one file per subject to compare",
    )
    view.add_argument(
        "--out",
        default=None,
        help="where to write the viewer — a file for one map, a directory for several",
    )
    view.add_argument("--open", action="store_true", help="open it in a browser afterwards")
    view.add_argument("--no-bodies", action="store_true", help="omit file bodies (smaller file)")
    view.add_argument(
        "--max-body", type=int, default=20000, help="per-item body character cap (default: 20000)"
    )
    view.add_argument(
        "--keep-old",
        action="store_true",
        help="keep viewers from earlier runs (default: delete atlas's own stale files in the temp dir)",
    )

    scan = sub.add_parser("scan", help="emit the graph as JSON")
    scan.add_argument("--out", default=None, help="write the JSON to a file instead of stdout")
    scan.add_argument("--no-bodies", action="store_true", help="omit file bodies")
    scan.add_argument("--max-body", type=int, default=20000, help="per-item body character cap")

    budget = sub.add_parser("budget", help="rank what the always-on context is spent on")
    budget.add_argument(
        "--by",
        choices=sorted(BUDGET_AXES),
        default="scope",
        help="group the totals by this axis (default: scope)",
    )
    budget.add_argument("--top", type=int, default=15, help="how many items to list (default: 15)")
    budget.add_argument(
        "--over",
        type=int,
        default=0,
        help="list every item at or over this many tokens instead of the top N",
    )
    budget.add_argument("--json", action="store_true", help="emit the report as JSON")

    # Deliberately its own subcommand rather than `scan --diff`: `scan` promises
    # to print the graph as JSON, and a second thing printed from the same place
    # would break whatever is reading it.
    diff = sub.add_parser("diff", help="compare the current state against a saved scan")
    diff.add_argument("baseline", metavar="SCAN.json", help="a file written by an earlier `scan`")
    diff.add_argument("--top", type=int, default=15, help="entries to list per section")
    diff.add_argument("--json", action="store_true", help="emit the comparison as JSON")

    args = parser.parse_args(argv)
    command = args.command or "view"

    project = Path(args.project).expanduser().resolve()
    user_root = user_config_dir(args.user_root)
    plugins_root = (
        Path(args.plugins_root).expanduser().resolve()
        if args.plugins_root
        else user_root / "plugins"
    )

    def graph_for(subject: Path, include_project: bool = True) -> dict[str, Any]:
        return build_graph(
            project=subject,
            user_root=user_root,
            plugins_root=plugins_root,
            max_body=getattr(args, "max_body", 20000),
            # Neither a budget nor a diff ever shows a body, and reading a
            # megabyte of Markdown to count description characters is waste.
            include_bodies=(
                command not in ("budget", "diff") and not getattr(args, "no_bodies", False)
            ),
            include_project=include_project,
        )

    def scanned_roots(graph: dict[str, Any], subject: Path) -> list[Path]:
        return [user_root, subject / ".claude"] + [
            expand_home(root["path"]) for root in graph["roots"] if root["layer"] == "plugin"
        ]

    if command == "diff":
        baseline = read_json(Path(args.baseline).expanduser())
        if not isinstance(baseline, dict) or "items" not in baseline:
            raise SystemExit(
                f"atlas: {args.baseline} is not a scan written by atlas.\n"
                f"       Produce one with: atlas scan --no-bodies --out {args.baseline}"
            )
        report = diff_report(baseline, graph_for(project))
        if args.json:
            print(json.dumps(report, ensure_ascii=False, indent=2))
        else:
            print_diff(report, args.top)
        return 0

    if command == "budget":
        graph = graph_for(project)
        report = budget_report(graph, args.by, args.top, args.over)
        if args.json:
            print(json.dumps(report, ensure_ascii=False, indent=2))
        else:
            print_budget(report, graph, args.over)
        return 0

    if command == "scan":
        graph = graph_for(project)
        payload = json.dumps(graph, ensure_ascii=False, indent=2)
        if args.out:
            out = Path(args.out).expanduser()
            guard_out(out, scanned_roots(graph, project), project)
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_text(payload + "\n", encoding="utf-8")
            print(f"atlas: wrote {out}")
        else:
            print(payload)
        return 0

    # What to map. Always the session project. Naming another one turns this
    # into a comparison, and a comparison needs the third term: the global layer
    # on its own, which is the part both of them share.
    targets = [resolve_project(text, project) for text in args.targets]
    subjects: list[tuple[Path, bool]] = [(project, True)]
    if targets:
        subjects.append((project, False))  # the global layer on its own
        seen = {project}
        for target in targets:
            if target in seen:
                continue
            seen.add(target)
            subjects.append((target, True))

    out_dir: Path | None = None
    if len(subjects) > 1:
        # Several files cannot share one --out, so it becomes the directory they
        # go in. Each still gets the name its subject earns.
        out_dir = Path(args.out).expanduser() if args.out else Path(tempfile.gettempdir())
        if out_dir.exists() and not out_dir.is_dir():
            raise SystemExit(
                f"atlas: {out_dir} is a file, but {len(subjects)} maps are being written.\n"
                f"       With more than one subject, --out is the directory to write them into."
            )

    written: list[Path] = []
    for index, (subject, include_project) in enumerate(subjects):
        graph = graph_for(subject, include_project)
        if out_dir is not None:
            out = out_dir / viewer_name(graph["projectName"])
        else:
            out = Path(args.out).expanduser() if args.out else default_out(graph["projectName"])
        guard_out(out, scanned_roots(graph, subject), subject)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(render_html(graph), encoding="utf-8")
        if index:
            print()
        print_report(graph, out)
        written.append(out)

    if not args.keep_old:
        for stale in prune_old_viewers(written):
            print(f"Removed stale viewer: {stale}")

    if args.open:
        for path in written:
            open_in_browser(path)
    return 0


if __name__ == "__main__":
    sys.exit(main())
