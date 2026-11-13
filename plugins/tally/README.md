# tally

Counts what your Claude Code harness did, without keeping what it said.

> 한국어 문서는 [README-ko.md](README-ko.md)를 참고하세요.

In Claude Code:

```
/plugin marketplace add somaz94/claude-plugins
/plugin install tally@somaz94
```

Or from your shell, without an interactive session:

```bash
claude plugin marketplace add somaz94/claude-plugins
claude plugin install tally@somaz94
```

<br/>

## Why count at all

A harness is built from guesses about what will go wrong: a hook here, a read-only agent there, an approval gate in front of the irreversible step. Whether those guesses held is rarely checked. Does the guard fire on real mistakes or on every other command? Are prompts read, or approved by reflex? Does an agent declared read-only stay read-only, or does it reach the shell and change things anyway?

The answers are already on disk, in the session transcripts. So is everything else: source code, hostnames, the occasional secret. That is why nobody shares them, and why a harness owner's numbers rarely leave their own machine.

`tally` keeps the numbers and drops the text. It reads locally and writes one JSON file holding counts, dates and fixed labels only. Every key is fixed by [`schema/tally-v0.schema.json`](schema/tally-v0.schema.json), with `additionalProperties: false` at every level, so a name or a path has nowhere to go. Read the file, and you have read everything it would tell anyone.

<br/>

## The two halves

| Piece | What it does |
|---|---|
| `scripts/log-wrap.sh` | Wraps a hook you already have. Passes its output and exit code through untouched, and appends one line per non-silent decision to a local log |
| `scripts/tally.py`, `/tally:report` | Reads the transcripts, the decision log and your config directories, and writes the counts-only JSON |

The aggregator works without the wrapper. Claude Code's own transcripts already record permission decisions and some hook outcomes; the wrapper adds what they do not: which hook decided, at which tier, and what it decided.

<br/>

## Wiring the logger

Put `log-wrap.sh` in front of the hook command in your `settings.json`:

```json
{
  "type": "command",
  "command": "~/.claude/hooks/log-wrap.sh --tier ask ~/.claude/hooks/my-guard.sh"
}
```

Copy the script to a stable path first. An installed plugin lives under a directory named after its version, so a hook pointing into the plugin cache breaks silently on the next update.

The tier is `--tier <tier>`, or a `# tally: tier=<tier>` line near the top of the hook script, or empty. Records land in `~/.claude/tally/hook-decisions/decisions-YYYY-MM.jsonl`; set `TALLY_LOG_DIR` to move them; `tally.py` reads the same variable, so both halves follow it.

The wrapper is built to be invisible when it fails. The hook's stdout, stderr and exit code pass through byte for byte; a logging error is swallowed; a missing hook exits 0. A wrapper that could change a verdict would be a second hook nobody reviewed.

What a record holds: time, the hook's file name, tier, event, tool, decision, exit code, session and tool-use ids, the edited file's path, the hook's own reason message, and for a shell command only its first word and a 12-character hash. The command text is never written. The log stays on your machine and `tally.py` reads only its counts.

<br/>

## What gets counted

Per ISO week (UTC) and in total:

- **Sessions and main-session calls**: tool calls, file edits, shell calls that changed state, questions asked, interrupts, compactions, sub-agent launches.
- **Permissions**: allowed or denied automatically by configuration (the transcript's `config` source), allowed by a person once, allowed by a rule a person saved, denied by a hook, rejected by a person.
- **Sub-agents**: runs, runs of your own agents, runs that changed state, and the same for agents whose `tools:` grants no file-write tool. That last pair answers the question a read-only declaration cannot: did the agent stay inside it?
- **Hooks**: decisions by kind and by tier from the log, and hook outcomes recorded in the transcripts.
- **Harness**: agents (write-capable, inheriting every tool), commands, skills, hooks per event.

<br/>

## Limits

- **State change is a heuristic.** A shell call counts when it runs a known writer: `rm`, `mv`, `cp`, `sed -i`, a `git` / `gh` / `glab` / `kubectl` / `helm` / `terraform` subcommand that writes, a write through `gh api`. A change confined to a temp directory is counted separately as scratch. When the classifier cannot place a change, it counts it as a workspace change, so that number errs high. Output redirection (`>`) is not counted at all. The classifier is versioned in the output (`classifier`); two files with different versions do not compare.
- **The transcript format is internal.** It is Claude Code's storage, not a public API. A line the reader cannot parse is counted in `input.unrecognized_lines` rather than guessed at. If that number climbs, the counts are going stale.
- **Agent names stay local.** Whether a run is your own agent comes from the run's sidecar metadata; a run whose sidecar is missing or predates that field counts as built-in. The read-only split also needs the run's agent type to match a definition in a `--config` directory, so agents from plugins or from directories you did not pass are left out of it.
- **Schema v0 may change** before anyone collects data under it. The output names its schema, so a reader can tell.

<br/>

## Running the script directly

```bash
python3 scripts/tally.py --out tally.json
python3 scripts/tally.py --since 2026-10-01 --config ~/.claude --config ./.claude
```

stdlib only, Python 3.10 or later. Run it at low priority on a large history.

<br/>

## What it never does

- Writes command text, prompts, file contents or names of any kind into its output.
- Sends anything anywhere. Sharing the file is your decision, made after you read it.
- Edits your settings. Wiring the logger is a change you make yourself.

<br/>

## Releases

Each plugin in this marketplace is versioned and released on its own. Every change to `tally` — with the commits scoped to this directory — is at [tally releases](https://github.com/somaz94/claude-plugins/releases?q=tally&expanded=true).

<br/>

## License

MIT — see [LICENSE](../../LICENSE).
