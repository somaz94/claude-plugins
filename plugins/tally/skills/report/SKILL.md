---
name: report
description: 'Count what this Claude Code harness did, from the local session transcripts and the optional hook decision log: sessions, permission prompts approved and rejected, sub-agent runs and the state-changing calls they made, hook decisions by tier, and the size of the config. Writes a counts-only JSON file and shows it. Use when asked "how often do my hooks fire", "how many prompts did I approve", "did my read-only agents change anything", "tally my harness", or before sharing harness metrics with anyone.'
argument-hint: '[--since YYYY-MM-DD] [--until YYYY-MM-DD] [--config DIR ...]'
allowed-tools: Bash, Read
---

# report

Run the bundled aggregator over this machine's transcripts and show the result. Read-only: it reads `~/.claude/projects/`, writes one JSON file, and sends nothing anywhere.

<br/>

## Run it

```bash
out="$(mktemp -t tally).json"
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/tally.py" --out "$out" $ARGUMENTS
```

Defaults: transcripts from `~/.claude/projects`, hook log from `~/.claude/tally/hook-decisions`, config shape from `~/.claude`. Pass `--config <repo>/.claude` once per extra config directory to include project-level agents and hooks. A large transcript history takes a minute or two; run it at low priority (`nice -n 10`, or `taskpolicy -b` on macOS).

<br/>

## Reporting

Lead with the window (`window.first` to `window.last`, `window.weeks`) and the file path. Then, from `totals`:

- **Prompts**: `permissions.user_allowed_once` and `permissions.user_rejected` are the times a person was asked. Everything under `auto_*` was decided by config, mode or classifier without asking.
- **Sub-agents**: `subagents.runs`, and `runs_readonly_declared_with_mutation` out of `runs_readonly_declared`. That pair is the one to look at twice: it counts runs of agents whose `tools:` grants no file-write tool that still changed the workspace through the shell.
- **Hooks**: `hooks_log` when the decision log exists (it only fills once hooks run through `log-wrap.sh`), else `hooks_transcript`.
- **Harness**: `harness.agents.write_capable` and `inherit_all_tools` out of `agents.total`.

Show the weekly rows as a small table only when the user asks for a trend.

State two limits whenever the numbers are used for a decision. The state-change classifier is a heuristic: `mutating_bash_calls` errs high, and `scratch_bash_calls` covers only changes it could place in a temp directory. And the transcript format is Claude Code's internal storage, so a rising `input.unrecognized_lines` means the counts may be stale.

<br/>

## What you do NOT do

- Paste raw transcript text, commands or paths into the conversation to explain a number. The point of the file is that it holds none.
- Upload, send or attach the output anywhere. If the user wants to share it, tell them to read the file first: every key is fixed by `schema/tally-v0.schema.json`, so what they read is all there is.
- Edit settings to install the logger. Show the wiring from the README and let the user add it.
