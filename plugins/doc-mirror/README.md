# doc-mirror

Keeps translated documentation pairs from drifting apart.

> 한국어 문서는 [README-ko.md](README-ko.md)를 참고하세요.

A repository that ships `README.md` beside `README-ko.md` has made a promise it has no way to keep. Edit one half and not the other and every check still passes: the build is green, the linter is quiet, the diff looks deliberate, and the review sees one file. Nothing anywhere is watching the pair. Months later the mirror is a museum piece, and the reader who picked that language is following last quarter's instructions.

```bash
/plugin marketplace add somaz94/claude-plugins
/plugin install doc-mirror@somaz94
```

Then, in any repository:

```
/doc-mirror:check
```

<br/>

## What it produces

```
Root: /Users/you/code/acme-platform
Pairs: 156 documents with 155 mirrors (en) across 113 directories

🔴 Critical (2)
  docs/deploying.md
    no en mirror — 14 of 15 documents in this directory have one
  README-en.md
    relative link does not resolve: docs/runbook.md

🟡 Warning (3)
  docs/observability-en.md
    shape has diverged from its source — headings 12 vs 6, code blocks 6 vs 3
  docs/scaling-en.md
    looks like a mirror but has no source beside it — either the source was
    removed, or this suffix was never a language
```

Four findings, in the order they cost you something:

- **A missing mirror**, where most of the directory has one. This document was meant to be translated and was not.
- **A broken relative link** — including the case that matters most here, a link that resolves in one language and not the other.
- **Structural drift** — the pair no longer has the same number of headings, code blocks, tables, lists or links. A source that gained a section while its mirror did not looks exactly like this.
- **An orphan mirror** whose source has been deleted or renamed away.

<br/>

## The convention is discovered, not configured

There is nothing to set up and no config file. `README-ko.md`, `guide.ja.md`, `docs/setup-pt-br.md` all work untouched, because the pairing rule is read out of the repository rather than declared to it.

That cuts both ways, and the second direction is what keeps the report usable:

- A language counts as a language only when some pair in that same directory **already proves it**. Without that rule `06-followup-fluent-bit.md` pairs on `bit` and `scaling-and-ha.md` pairs on `ha` — both are three lowercase letters after a hyphen, and both are nonsense. Real repositories are full of file names shaped like translations.
- A directory that keeps no mirrors is **never told it is missing them**. A single-language repo gets "no pairs found" and nothing else.
- A gap is reported against **how thoroughly that directory actually mirrors**. 14 of 15 translated makes the 15th an oversight; 2 of 9 makes the other 7 a decision. The report says which case it is and shows the ratio, because the ratio is the evidence.
- `CHANGELOG.md`, `RELEASE.md`, `CONTRIBUTORS.md`, `LICENSE.md` and `CLAUDE.md` are never expected to have mirrors. A machine writes the first four and a model reads the last one; a tool that demands `RELEASE-ko.md` beside your `README-ko.md` teaches you to ignore it by the second run.

<br/>

## What it will not do

**It has no opinion on meaning.** Whether the Korean says what the English says is a human judgment involving terminology, tone and accuracy. A tool that guesses at it is worse than no tool, so this one measures only what survives translation unchanged: a heading is still a heading, a table still has the same number of rows, a fenced block still holds the same command. Word counts and character counts do not survive, which is exactly why they are not measured.

**It does not see directory-based mirrors.** `agents/x.md` beside `agents-ko/x.md` is a real convention — Claude Code configuration uses it — but it is a *configured* pairing rather than one visible in a file name, and [`census`](../census) already takes a `pairs` map for exactly that. This plugin pairs on file names only.

**It does not answer "which half is stale".** Two files with identical shape can both be a year out of date. Answering that needs commit history, and [`census`](../census) already does it — its `drift` command compares when each half was last touched. This one compares what they contain. Run both; they disagree in useful ways.

**It never writes.** No missing mirror is created, no orphan is deleted, no file is renamed. Creating a mirror means writing prose in a language, and that is a decision for a person.

<br/>

## The nudge, at the moment it is cheap

The report finds pairs that have **already** drifted. The bundled hook is there so that fewer of them do.

It runs after every `Edit`, `Write` and `MultiEdit`, and when you change one half of a pair without having touched the other in the same session, it says so:

```
doc-mirror: edited the source document README.md but its mirror (README-ko.md)
was not edited this session — the pair may now be out of step.
```

Claude Code's `PostToolUse` event is stateless, so "was the counterpart edited this session?" is answered by reading the session transcript and collecting every `file_path` that an edit tool has been called with. If the counterpart is in that set, the hook stays silent.

It is **warn-only** by construction. `PostToolUse` fires after the tool has run, so nothing is undone and nothing is re-prompted — the reminder simply arrives while the other half is still in mind. It fails open on every uncertainty: an unparseable payload, an unreadable transcript, a directory it cannot list, all produce silence. A nudge that misfires on every edit gets the whole plugin uninstalled.

<br/>

## The `<br/>` spacer, if that is your house style

Some projects put a `<br/>` between heading sections so the rendered page breathes:

```markdown
... end of the previous section ...

<br/>

## Next heading
```

This is a house style, not a rule of Markdown, so it is enforced **only where it is already being followed** — inferred from whether at least 80% of the section headings in your pairs already have one, and only once there are enough headings for the answer to mean anything. `--spacer on` and `--spacer off` override the inference.

<br/>

## Running the script directly

One bundled script — python3, **stdlib only**, no install step.

```bash
python3 scripts/docmirror.py                      # check the working directory
python3 scripts/docmirror.py ~/code/api           # or somewhere else
python3 scripts/docmirror.py --json               # findings as data
python3 scripts/docmirror.py --strict             # exit 1 on any critical finding
python3 scripts/docmirror.py --spacer on          # enforce the spacer regardless
```

`--strict` is the CI form. As a pre-publish gate it answers one question — did a translated document get left behind — and answers it in about a second on a repository of 150 pairs.

The split is deliberate, and it is the same one the rest of this marketplace uses: the script measures, the skill interprets. Counting headings is not model work. Saying that all six drift findings sit under `_deprecated/` and can be ignored is.

Every check CI runs is also a file you can run: `bash plugins/doc-mirror/tests/run.sh`.

<br/>

## What it never does

- Edits, creates, renames or deletes any file it found.
- Translates anything, or judges whether a translation is good.
- Makes a network request.
- Blocks an edit. The hook is warn-only and cannot be otherwise — `PostToolUse` runs after the fact.

<br/>

## Releases

Each plugin in this marketplace is versioned and released on its own. Every change to `doc-mirror` — with the commits scoped to this directory — is at [doc-mirror releases](https://github.com/somaz94/claude-plugins/releases?q=doc-mirror&expanded=true).

<br/>

## License

MIT — see [LICENSE](../../LICENSE).
