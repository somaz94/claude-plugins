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
  - This script SCANS and RENDERS. It never edits a scanned tree, and the only
    file it writes is the viewer it was asked to produce.
  - stdlib only. A tool you reach for to understand your setup must not need a
    setup of its own.
  - The viewer is one self-contained file. No server, no CDN, no build step:
    every byte of CSS and JS is inline, so it opens offline and can be handed
    to someone else as a single attachment.

Subcommands:
  view   build the HTML viewer and optionally open it in a browser
  scan   emit the same graph as JSON, for piping somewhere else
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
from typing import Any, Iterable

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


class Collector:
    """Builds the flat item list. One instance per run."""

    def __init__(self, max_body: int, include_bodies: bool) -> None:
        self.items: list[dict[str, Any]] = []
        self.notes: list[str] = []
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

    def collect_config_dir(
        self, config_dir: Path, layer: str, origin: str, namespace: str | None = None
    ) -> dict[str, int]:
        """Collect agents, commands and skills laid out the standard way.

        `namespace` is the plugin name when the directory belongs to a plugin:
        its commands and skills are addressed as /<plugin>:<name>, which is what
        makes them unable to collide with a user's own.
        """
        counts = {"agent": 0, "command": 0, "skill": 0}
        if not config_dir.is_dir():
            return counts

        for path in sorted((config_dir / "agents").rglob("*.md")):
            meta, body = parse_frontmatter(read_text(path))
            name = meta.get("name") or path.stem
            text, truncated = self._body(body)
            self.add(
                kind="agent",
                name=name,
                # An agent is not typed as a slash command; it is selected by
                # its description. Showing a fake invocation would teach the
                # wrong thing, so the field stays empty and the viewer says so.
                invocation="",
                description=meta.get("description", ""),
                layer=layer,
                origin=origin,
                namespace=namespace or "",
                path=collapse_home(path),
                body=text,
                truncated=truncated,
                extra=self._extra(meta),
            )
            counts["agent"] += 1

        commands_dir = config_dir / "commands"
        for path in sorted(commands_dir.rglob("*.md")):
            meta, body = parse_frontmatter(read_text(path))
            # A subdirectory namespaces the command; the file name is the last
            # segment. A command has no `name:` field — the path IS the name.
            parts = list(path.relative_to(commands_dir).with_suffix("").parts)
            local = ":".join(parts)
            invocation = f"/{namespace}:{local}" if namespace else f"/{local}"
            text, truncated = self._body(body)
            self.add(
                kind="command",
                name=local,
                invocation=invocation,
                description=meta.get("description", ""),
                layer=layer,
                origin=origin,
                namespace=namespace or "",
                path=collapse_home(path),
                body=text,
                truncated=truncated,
                extra=self._extra(meta),
            )
            counts["command"] += 1

        for path in sorted((config_dir / "skills").rglob("SKILL.md")):
            meta, body = parse_frontmatter(read_text(path))
            name = meta.get("name") or path.parent.name
            invocation = f"/{namespace}:{name}" if namespace else f"/{name}"
            text, truncated = self._body(body)
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
                invocation=invocation,
                description=meta.get("description", ""),
                layer=layer,
                origin=origin,
                namespace=namespace or "",
                path=collapse_home(path),
                body=text,
                truncated=truncated,
                extra=extra,
                bundled=bundled,
            )
            counts["skill"] += 1

        return counts

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
) -> dict[str, Any]:
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
    if project_config.is_dir() or (project / "CLAUDE.md").is_file():
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

    # Layer 3 — installed plugins.
    state = enabled_state(
        [
            user_root / "settings.json",
            project_config / "settings.json",
            project_config / "settings.local.json",
        ]
    )
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
        "project": collapse_home(project),
        "projectName": project.name,
        "userRoot": collapse_home(user_root),
        "roots": roots,
        "items": items,
        "conflicts": conflicts,
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
    for item in items:
        by_kind[item["kind"]] = by_kind.get(item["kind"], 0) + 1
        by_layer[item["layer"]] = by_layer.get(item["layer"], 0) + 1
        # What a session pays before you type anything: the name and description
        # of every routable item, plus every CLAUDE.md in full.
        if item["kind"] in ("agent", "command", "skill"):
            resident += item["descriptionChars"]
        elif item["kind"] == "memory":
            resident += item.get("residentChars", 0)
    return {
        "total": sum(v for k, v in by_kind.items()),
        "byKind": by_kind,
        "byLayer": by_layer,
        "residentChars": resident,
        "residentTokens": resident // CHARS_PER_TOKEN,
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

PAGE = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>__TITLE__</title>
<style>
:root {
  color-scheme: light dark;
  --bg: #fbfbfa; --panel: #ffffff; --ink: #1d1d1b; --muted: #6b6b66;
  --line: #e3e3df; --accent: #b4551d; --warn: #a8341f; --ok: #2f6b45;
  --chip: #f0f0ec; --code: #f5f5f2;
}
@media (prefers-color-scheme: dark) {
  :root {
    --bg: #161614; --panel: #1e1e1b; --ink: #eceae5; --muted: #9c9a93;
    --line: #33322e; --accent: #e08b4f; --warn: #e07a63; --ok: #7ec295;
    --chip: #2a2a26; --code: #232320;
  }
}
* { box-sizing: border-box; }
body {
  margin: 0; background: var(--bg); color: var(--ink);
  font: 15px/1.55 ui-sans-serif, -apple-system, "Segoe UI", Roboto, "Helvetica Neue", Arial, sans-serif;
}
code, pre, .mono { font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace; }
.wrap { max-width: 1100px; margin: 0 auto; padding: 28px 20px 80px; }
header h1 { font-size: 22px; margin: 0 0 4px; letter-spacing: -0.01em; }
header .sub { color: var(--muted); font-size: 13px; margin-bottom: 18px; }
header .sub code { background: var(--code); padding: 1px 5px; border-radius: 4px; }
.stats { display: flex; flex-wrap: wrap; gap: 8px; margin-bottom: 18px; }
.stat {
  background: var(--panel); border: 1px solid var(--line); border-radius: 8px;
  padding: 8px 12px; min-width: 96px;
}
.stat b { display: block; font-size: 19px; font-weight: 600; }
.stat span { color: var(--muted); font-size: 11px; text-transform: uppercase; letter-spacing: .05em; }
.toolbar { position: sticky; top: 0; background: var(--bg); padding: 10px 0 12px; z-index: 5; }
#q {
  width: 100%; padding: 10px 12px; font-size: 15px; color: var(--ink);
  background: var(--panel); border: 1px solid var(--line); border-radius: 8px;
}
#q:focus { outline: 2px solid var(--accent); outline-offset: -1px; }
.filters { display: flex; flex-wrap: wrap; gap: 6px; margin-top: 10px; align-items: center; }
.filters .sep { width: 1px; height: 20px; background: var(--line); margin: 0 4px; }
button.chip {
  font: inherit; font-size: 12.5px; cursor: pointer; padding: 4px 10px;
  border-radius: 999px; border: 1px solid var(--line);
  background: var(--chip); color: var(--muted);
}
button.chip[aria-pressed="true"] { background: var(--accent); border-color: var(--accent); color: #fff; }
.count { color: var(--muted); font-size: 12.5px; margin-left: auto; }
h2.kind {
  font-size: 13px; text-transform: uppercase; letter-spacing: .07em; color: var(--muted);
  margin: 26px 0 8px; padding-bottom: 6px; border-bottom: 1px solid var(--line);
}
details.card {
  background: var(--panel); border: 1px solid var(--line); border-radius: 8px;
  margin-bottom: 7px; overflow: hidden;
}
details.card[open] { border-color: var(--accent); }
summary { cursor: pointer; padding: 11px 14px; list-style: none; }
summary::-webkit-details-marker { display: none; }
.row { display: flex; align-items: baseline; gap: 8px; flex-wrap: wrap; }
.name { font-weight: 600; font-size: 14.5px; }
.inv { font-size: 12.5px; color: var(--accent); }
.tag {
  font-size: 10.5px; text-transform: uppercase; letter-spacing: .05em;
  padding: 2px 7px; border-radius: 999px; background: var(--chip); color: var(--muted);
}
.tag.warn { color: var(--warn); border: 1px solid var(--warn); background: none; }
.desc { color: var(--muted); font-size: 13.5px; margin-top: 4px; }
.desc.none { font-style: italic; color: var(--warn); }
.meta { padding: 0 14px 12px; border-top: 1px solid var(--line); }
.kv { display: flex; flex-wrap: wrap; gap: 5px; margin: 10px 0; }
.kv span { font-size: 11.5px; background: var(--chip); border-radius: 4px; padding: 2px 7px; }
.kv span b { font-weight: 600; }
.path { font-size: 12px; color: var(--muted); margin-top: 8px; word-break: break-all; }
pre.body {
  background: var(--code); border: 1px solid var(--line); border-radius: 6px;
  padding: 12px; overflow-x: auto; font-size: 12.5px; line-height: 1.5;
  max-height: 460px; overflow-y: auto; white-space: pre-wrap; word-wrap: break-word;
}
.empty { color: var(--muted); padding: 40px 0; text-align: center; }
.callout {
  border: 1px solid var(--warn); border-radius: 8px; padding: 12px 14px;
  margin-bottom: 14px; font-size: 13.5px;
}
.callout h3 { margin: 0 0 6px; font-size: 13px; text-transform: uppercase; letter-spacing: .06em; color: var(--warn); }
.callout ul { margin: 6px 0 0; padding-left: 18px; }
.callout li { margin-bottom: 4px; }
footer { margin-top: 44px; padding-top: 14px; border-top: 1px solid var(--line); color: var(--muted); font-size: 12.5px; }
footer code { background: var(--code); padding: 1px 5px; border-radius: 4px; }
</style>
</head>
<body>
<div class="wrap">
<header>
  <h1>__HEADING__</h1>
  <div class="sub">Everything reachable from <code>__PROJECT__</code> · generated __GENERATED__</div>
</header>
<div id="summary"></div>
<div class="toolbar">
  <input id="q" type="search" placeholder="Search name, description, path, or body…" autocomplete="off">
  <div class="filters" id="filters"></div>
</div>
<main id="list"></main>
<footer>
  Regenerate with <code>/atlas:view</code>. This file is self-contained — no network requests, no server.
  Bodies are shown as the Markdown source on disk, which is what Claude Code reads.
</footer>
</div>
<script id="atlas-data" type="application/json">__DATA__</script>
<script>
const DATA = JSON.parse(document.getElementById('atlas-data').textContent);
const KIND_LABEL = {
  command: 'Commands', agent: 'Agents', skill: 'Skills',
  hook: 'Hooks', mcp: 'MCP servers', memory: 'Memory', plugin: 'Plugins'
};
const KIND_ORDER = ['command', 'agent', 'skill', 'hook', 'mcp', 'memory', 'plugin'];
const state = { q: '', kinds: new Set(), layers: new Set(), issuesOnly: false };

function esc(s) {
  return String(s == null ? '' : s).replace(/[&<>"']/g, c =>
    ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
}

function renderSummary() {
  const s = DATA.stats;
  const cards = [
    ['Resources', s.total],
    ['Commands', s.byKind.command || 0],
    ['Agents', s.byKind.agent || 0],
    ['Skills', s.byKind.skill || 0],
    ['Hooks', s.byKind.hook || 0],
    ['~Tokens always on', s.residentTokens.toLocaleString()],
  ];
  let html = '<div class="stats">' + cards.map(([k, v]) =>
    `<div class="stat"><b>${esc(v)}</b><span>${esc(k)}</span></div>`).join('') + '</div>';

  const alerts = [];
  if (DATA.conflicts.length) {
    alerts.push('<h3>' + DATA.conflicts.length + ' name' + (DATA.conflicts.length > 1 ? 's resolve' : ' resolves') +
      ' to more than one definition</h3><ul>' + DATA.conflicts.map(c =>
      `<li><code>${esc(c.name)}</code> (${esc(c.kind)}) — defined in ` +
      c.where.map(w => esc(w.origin)).join(', ') + '. ' + esc(c.verdict) + '</li>').join('') + '</ul>');
  }
  if (s.unresolvedHooks) {
    alerts.push('<h3>' + s.unresolvedHooks + ' hook' + (s.unresolvedHooks > 1 ? 's point' : ' points') +
      ' at a script that is not there</h3><p>The registration ships, the hook never fires, and nothing says so.</p>');
  }
  if (s.missingDescriptions) {
    alerts.push('<h3>' + s.missingDescriptions + ' item' + (s.missingDescriptions > 1 ? 's declare' : ' declares') +
      ' no description</h3><p>The description is the only signal for when to reach for an item. Without one it is unreachable unless named outright.</p>');
  }
  if (DATA.notes.length) {
    alerts.push('<h3>Notes</h3><ul>' + DATA.notes.map(n => `<li>${esc(n)}</li>`).join('') + '</ul>');
  }
  if (alerts.length) html += alerts.map(a => `<div class="callout">${a}</div>`).join('');
  document.getElementById('summary').innerHTML = html;
}

function renderFilters() {
  const kinds = KIND_ORDER.filter(k => DATA.items.some(i => i.kind === k));
  const layers = ['user', 'project', 'plugin'].filter(l => DATA.items.some(i => i.layer === l));
  const el = document.getElementById('filters');
  el.innerHTML =
    kinds.map(k => `<button class="chip" data-kind="${k}" aria-pressed="false">${esc(KIND_LABEL[k] || k)}</button>`).join('') +
    '<span class="sep"></span>' +
    layers.map(l => `<button class="chip" data-layer="${l}" aria-pressed="false">${esc(l)}</button>`).join('') +
    '<span class="sep"></span>' +
    '<button class="chip" data-issues="1" aria-pressed="false">needs attention</button>' +
    '<span class="count" id="count"></span>';

  el.addEventListener('click', e => {
    const b = e.target.closest('button.chip');
    if (!b) return;
    const on = b.getAttribute('aria-pressed') === 'true';
    b.setAttribute('aria-pressed', String(!on));
    if (b.dataset.kind) on ? state.kinds.delete(b.dataset.kind) : state.kinds.add(b.dataset.kind);
    else if (b.dataset.layer) on ? state.layers.delete(b.dataset.layer) : state.layers.add(b.dataset.layer);
    else state.issuesOnly = !on;
    render();
  });
}

function matches(item) {
  if (state.kinds.size && !state.kinds.has(item.kind)) return false;
  if (state.layers.size && !state.layers.has(item.layer)) return false;
  if (state.issuesOnly) {
    const bad = item.conflicted || item.resolves === false ||
      (!item.description && ['agent', 'command', 'skill'].includes(item.kind));
    if (!bad) return false;
  }
  if (!state.q) return true;
  const hay = [item.name, item.invocation, item.description, item.path, item.origin, item.body]
    .join('\\n').toLowerCase();
  return state.q.split(/\\s+/).every(t => hay.includes(t));
}

function card(item) {
  const tags = [`<span class="tag">${esc(item.layer)}</span>`];
  if (item.namespace && item.kind !== 'plugin') tags.push(`<span class="tag">${esc(item.namespace)}</span>`);
  if (item.conflicted) tags.push('<span class="tag warn">shadowed</span>');
  if (item.resolves === false) tags.push('<span class="tag warn">missing script</span>');
  if (item.enabled === false) tags.push('<span class="tag warn">disabled</span>');

  const kv = Object.entries(item.extra || {})
    .map(([k, v]) => `<span><b>${esc(k)}</b> ${esc(v)}</span>`).join('');
  const desc = item.description
    ? `<div class="desc">${esc(item.description)}</div>`
    : (['agent', 'command', 'skill'].includes(item.kind)
        ? '<div class="desc none">no description — this item cannot be routed to</div>' : '');

  let body = '';
  if (item.body) {
    body = `<pre class="body">${esc(item.body)}${item.truncated ? '\\n\\n… truncated' : ''}</pre>`;
  } else if (item.kind === 'agent' || item.kind === 'command' || item.kind === 'skill') {
    body = '<div class="path">Body not included in this build.</div>';
  }

  return `<details class="card">
    <summary>
      <div class="row">
        <span class="name">${esc(item.name)}</span>
        ${item.invocation ? `<span class="inv mono">${esc(item.invocation)}</span>` : ''}
        ${tags.join('')}
      </div>
      ${desc}
    </summary>
    <div class="meta">
      ${kv ? `<div class="kv">${kv}</div>` : ''}
      ${body}
      <div class="path mono">${esc(item.path)}</div>
    </div>
  </details>`;
}

function render() {
  const shown = DATA.items.filter(matches);
  const list = document.getElementById('list');
  document.getElementById('count').textContent = shown.length + ' of ' + DATA.items.length;
  if (!shown.length) {
    list.innerHTML = '<div class="empty">Nothing matches.</div>';
    return;
  }
  let html = '';
  for (const kind of KIND_ORDER) {
    const group = shown.filter(i => i.kind === kind);
    if (!group.length) continue;
    html += `<h2 class="kind">${esc(KIND_LABEL[kind] || kind)} (${group.length})</h2>`;
    html += group.map(card).join('');
  }
  list.innerHTML = html;
}

document.getElementById('q').addEventListener('input', e => {
  state.q = e.target.value.trim().toLowerCase();
  render();
});
document.addEventListener('keydown', e => {
  if (e.key === '/' && document.activeElement.id !== 'q') {
    e.preventDefault();
    document.getElementById('q').focus();
  }
});
renderSummary();
renderFilters();
render();
</script>
</body>
</html>
"""


def render_html(graph: dict[str, Any]) -> str:
    heading = f"Claude Code atlas — {graph['projectName']}"
    # `<` cannot appear outside a JSON string, so escaping every one of them is
    # enough to keep an embedded `</script>` from ending the block early.
    data = json.dumps(graph, ensure_ascii=False).replace("<", "\\u003c")
    return (
        PAGE.replace("__TITLE__", html.escape(heading))
        .replace("__HEADING__", html.escape(heading))
        .replace("__PROJECT__", html.escape(graph["project"]))
        .replace("__GENERATED__", html.escape(graph["generatedAt"]))
        .replace("__DATA__", data)
    )


# --------------------------------------------------------------------------
# output
# --------------------------------------------------------------------------


def default_out(project: Path) -> Path:
    """Write outside the project by default.

    A viewer dropped into the repository is one `git add .` away from being
    committed, so the default lands in the temp directory and the path is
    printed. `--out` puts it wherever the user actually wants it.
    """
    safe = re.sub(r"[^A-Za-z0-9._-]", "-", project.name) or "project"
    return Path(tempfile.gettempdir()) / f"claude-atlas-{safe}.html"


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
            f"{collapse_home(default_out(project))}."
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
    print(f"Project: {graph['project']}")
    print(f"Resources: {s['total']} ({kinds})")
    layers = ", ".join(f"{v} {k}" for k, v in sorted(graph["stats"]["byLayer"].items()))
    print(f"By layer: {layers}")
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
    view.add_argument("--out", default=None, help="where to write the viewer")
    view.add_argument("--open", action="store_true", help="open it in a browser afterwards")
    view.add_argument("--no-bodies", action="store_true", help="omit file bodies (smaller file)")
    view.add_argument(
        "--max-body", type=int, default=20000, help="per-item body character cap (default: 20000)"
    )

    scan = sub.add_parser("scan", help="emit the graph as JSON")
    scan.add_argument("--out", default=None, help="write the JSON to a file instead of stdout")
    scan.add_argument("--no-bodies", action="store_true", help="omit file bodies")
    scan.add_argument("--max-body", type=int, default=20000, help="per-item body character cap")

    args = parser.parse_args(argv)
    command = args.command or "view"

    project = Path(args.project).expanduser().resolve()
    user_root = user_config_dir(args.user_root)
    plugins_root = (
        Path(args.plugins_root).expanduser().resolve()
        if args.plugins_root
        else user_root / "plugins"
    )

    graph = build_graph(
        project=project,
        user_root=user_root,
        plugins_root=plugins_root,
        max_body=getattr(args, "max_body", 20000),
        include_bodies=not getattr(args, "no_bodies", False),
    )

    scanned = [user_root, project / ".claude"] + [
        expand_home(root["path"]) for root in graph["roots"] if root["layer"] == "plugin"
    ]

    if command == "scan":
        payload = json.dumps(graph, ensure_ascii=False, indent=2)
        if args.out:
            out = Path(args.out).expanduser()
            guard_out(out, scanned, project)
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_text(payload + "\n", encoding="utf-8")
            print(f"atlas: wrote {out}")
        else:
            print(payload)
        return 0

    out = Path(args.out).expanduser() if args.out else default_out(project)
    guard_out(out, scanned, project)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(render_html(graph), encoding="utf-8")
    print_report(graph, out)
    if args.open:
        open_in_browser(out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
