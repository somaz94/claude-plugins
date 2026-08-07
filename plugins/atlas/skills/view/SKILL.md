---
name: view
description: Build a browsable HTML map of every Claude Code resource this project can reach — commands, agents, skills, hooks, MCP servers and memory files, from your user config, this repo, and every installed plugin, with the name conflicts and dead hooks marked. Use when asked "what commands do I have here", "show my setup", "what does this plugin actually add", "open my config in a browser", or after installing a plugin.
argument-hint: "[--open] [--out FILE] [--no-bodies]"
allowed-tools: Bash, Read
---

# atlas:view — what can this project actually reach?

Scans the four layers a session resolves at once, then writes a single self-contained HTML file you open in a browser.

1. the user config directory — `~/.claude`, or `$CLAUDE_CONFIG_DIR`
2. this project — `.claude/`, `settings.local.json`, `CLAUDE.md`, `.mcp.json`
3. every installed plugin — resolved through `~/.claude/plugins/installed_plugins.json`
4. the hooks registered by any settings file in the first two

This skill is **read-only** against everything it scans. The only file it writes is the viewer.

<br/>

## Run it

```bash
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/atlas.py" view --open
```

That writes to the temp directory and opens a browser. Forward whatever the user supplied in `$ARGUMENTS`, and reach for these when they apply:

| Flag | When |
|---|---|
| `--out FILE` | the user wants the file somewhere specific, or wants to send it to someone |
| `--no-bodies` | the config is large and they only want the index |
| `--project DIR` | mapping a repo other than the working directory |
| `scan` instead of `view` | they want the graph as JSON to pipe somewhere |

Omit `--open` when the user only asked for the numbers, or when a browser would not help — a remote shell, a container, CI.

The script prints a summary to stdout as well as writing the file. **Do not re-type the viewer's contents into your reply**; the file is the deliverable.

<br/>

## What to report

Lead with the path to the file and the one-line count the script printed, then add whatever of these actually fired. Keep it short.

**Name conflicts.** The finding with a real consequence. Two definitions of one agent name means the same name behaves differently depending on where the session started, and nothing else reports which copy won. The script names the winner where precedence is documented — a project definition overrides a user one — and says *ambiguous* where it is not. Never guess past that.

**Hooks that point at a script which is not there.** The registration still loads, the hook never fires, and nothing announces it. Quote the path.

**Items with no description.** The description is the only signal for when to reach for an item. Without one it is installed, listed, and unreachable unless named outright.

**Always-on context.** The `name` and `description` of every command, agent and skill is resident in every session's system prompt, and every `CLAUDE.md` is resident in full — bodies load only on invocation. That total is what the session pays before the user types anything. Under ~5k tokens is unremarkable; past ~25k a meaningful slice of every session is spent before work starts. It is an estimate at 4 characters per token, so quote it as one.

**What each installed plugin contributes.** After a `/plugin install`, this is usually the actual question: which commands, agents, skills and hooks did that plugin just add, and are they enabled here.

<br/>

## Hard rules

- Read-only against every scanned tree. The only write is the viewer.
- Never edit, move, or delete a discovered item, and never offer to as part of this skill.
- The script refuses an `--out` inside a scanned directory. If that refusal fires, pick a path outside it rather than working around it.
- If the user wants the viewer kept in their repo, say that it names real paths from this machine and belongs in `.gitignore`.
- Do not judge whether an item is shareable, and do not diff mirrored copies. A map says what is there and where it came from.
