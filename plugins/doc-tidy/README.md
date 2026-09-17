# doc-tidy

Decides whether each document should exist and where it belongs — then applies only what you approve.

> 한국어 문서는 [README-ko.md](README-ko.md)를 참고하세요.

Documents pile up where the work happened. A handoff written for one conversation gets committed. A finished migration keeps its plan and phase reports beside the current guides. A prompt saved "for later" sits next to the runbook it was written for, and a line in `CLAUDE.md` still points at a plan file that Claude Code deleted last month. None of it breaks a build, so none of it is ever cleaned up — and every stale document is one more thing the next reader, or the next agent, has to rule out.

In Claude Code:

```
/plugin marketplace add somaz94/claude-plugins
/plugin install doc-tidy@somaz94
```

Or from your shell, without an interactive session:

```bash
claude plugin marketplace add somaz94/claude-plugins
claude plugin install doc-tidy@somaz94
```

Then, in any repository:

```
/doc-tidy:triage
```

<br/>

## Why this exists

Tools that check documentation look **inside** a document: is the link valid, is the translation in step, is the prose awkward. None of them asks whether the document should still exist, whether it is in the right place, or what breaks if it moves. That question is where assistant-heavy repositories accumulate the most debris, because an assistant writes a new file far more readily than it retires an old one.

Answering it safely needs two things a single pass cannot give. A **measurement** that does not guess: who links to this file, from where, in which language, and when the code it describes last changed. And a **judgment** that reads the file with the repository's own rules in hand — because a committed prompt can be a reusable template a README indexes, and a migration folder can be exactly the layout the repository asks for.

<br/>

## What it produces

The script measures first:

```
doc-tidy scan: /Users/you/code/acme-platform
  212 documents (agent-config 18, archived 9, byproduct 3, entry 64, guide 110, historical 8); 2 untracked
  archive: docs/_archive (mirror)
  1 critical, 9 warning, 4 suggestion
🔴 broken-link · docs/deploying.md — 1 broken relative link(s)
     L42: ../runbooks/rollback.md (moved-candidate: ops/runbooks/rollback.md)
🟡 byproduct · docs/billing-ci-handoff.md — looks like a session byproduct
🟡 migration-folder · docs/queue-migration — a migration folder whose status reads as finished
🟡 wrong-language-link · README-ko.md — 2 link(s) point at the other language's half of a pair
🟢 drift · . — 6 document(s) were last changed long before the code they describe
```

Then the `doc-tidy-triager` agent reads every candidate and returns a verdict with its evidence:

```
🟡 ARCHIVE (high) · docs/queue-migration (folder, 7 documents)
   completion: docs/queue-migration/status.md:3 "Status: done — cutover finished 2025-03-28"
   destination: docs/_archive/queue-migration/
🟢 KEEP (high) · docs/alert-followup-prompt.md   default DELETE
   inbound: runbooks/README.md:18 — indexed and reused for every alert of this kind
```

<br/>

## Every verdict carries its evidence

| Verdict | It needs |
|---|---|
| KEEP | something that references the document, a repository rule that places it, or proof it is current |
| LINK | the broken link, or the index that should gain a row |
| MOVE / ARCHIVE | a placement rule or a completion marker, and a destination |
| MERGE | the overlapping passages, and the sections to carry over |
| DELETE | **all three**: nothing references it, it served one moment, and its substance is preserved elsewhere or worthless |
| ASK | a question with real options — the answer to "where does finished history go here?" is saved, so it is asked once per repository |

A verdict without its evidence is turned into a question before you ever see it. A verdict that overrules the class default has to say why.

<br/>

## Applied in batches, lowest risk first

Nothing changes until you pick items and confirm the computed change. The order is fixed:

1. **Links** — wrong-language links, broken links with a unique candidate, index rows.
2. **Moves and archives** — whole language pairs and whole folders, never one half.
3. **Merges** — the carry-over is shown in full, and checked, before the source is retired.
4. **Tracked deletions** — each link the deletion would break is resolved first.
5. **Untracked files** — one at a time, into a trash directory, never `rm`.

Every move, merge and delete goes through `relink`, which computes each link edit in the style it was written — relative, root-absolute, or a backticked path in agent config — and refuses a destination that exists, escapes the repository, or is ignored by git. `apply` re-checks every edit against the current files and writes nothing if anything moved. After each batch a re-scan against the first scan must show **no new broken link and no split pair**, or the loop stops and hands you the undo commands.

<br/>

## Plans expire

Claude Code deletes top-level plan files older than `cleanupPeriodDays` (30 days by default) during startup housekeeping. That is fine for a plan — and not for the `CLAUDE.md` line, the agent, the plan template or the memory note that points at one.

```
/doc-tidy:plans
```

It lists plans about to go, every durable reference to a plan that is already gone, and loose Markdown in workspace roots you name. If you keep your Claude config in git, `--recover-from` finds the commit that deleted each missing plan, so a reference can be restored rather than guessed at. It copies durable parts out of a plan before it expires; it never edits a plan to keep it alive.

<br/>

## What it will not do

**It does not judge what a document says.** Content freshness, translation quality and prose are other tools' work — the verdict for a stale claim is a hand-off, not an edit.

**It does not check pair structure.** It moves translated pairs as one unit, but whether the two halves still have the same sections is [`doc-mirror`](../doc-mirror)'s job.

**It does not track configuration drift.** Two copies of an agent disagreeing is [`census`](../census).

**It does not keep a plan current.** That is [`session-continuity`](../session-continuity); this plugin decides what outlives a plan.

<br/>

## Running the script directly

One bundled script — python3, **stdlib only**, no install step. Every subcommand except `apply` is read-only.

```bash
python3 scripts/doctidy.py scan .                                   # one repository
python3 scripts/doctidy.py sweep ~/code --top 10                    # rank many repositories
python3 scripts/doctidy.py plans --workspace ~/code                 # the plans lane
python3 scripts/doctidy.py relink . --archive docs/queue-migration --save /tmp/plan.json
python3 scripts/doctidy.py apply --plan /tmp/plan.json              # the only command that writes
python3 scripts/doctidy.py --check-rules                            # validate rules/rules.tsv
```

The rules that name things — what a committed prompt looks like, which directory names mean "archive", which lines declare a migration finished — are data in `rules/rules.tsv`, not code. Every row carries an example that must match and counter-examples that must not, and `--check-rules` proves both. A `rules.local.tsv` beside it overrides rows by id for your own naming habits.

Every check CI runs is also a file you can run: `bash plugins/doc-tidy/tests/run.sh`.

<br/>

## What it never does

- Commits, pushes or tags.
- Deletes an untracked file with `rm`, or moves one half of a translated pair.
- Edits release automation, or writes an inventory into a repository document.
- Edits a plan's decision text, or touches a plan to extend its life.
- Makes a network request.

<br/>

## Releases

Each plugin in this marketplace is versioned and released on its own. Every change to `doc-tidy` — with the commits scoped to this directory — is at [doc-tidy releases](https://github.com/somaz94/claude-plugins/releases?q=doc-tidy&expanded=true).

<br/>

## License

MIT — see [LICENSE](../../LICENSE).
