---
name: check
description: 'Check a repository''s translated documentation pairs for drift — the README whose mirror was never written, the mirror whose source moved on without it, the half that lost a section, and links that resolve in one language but not the other. Use when asked "are my translations in sync", "check the docs pairs", "did I update both READMEs", or before publishing a repo that ships more than one language.'
argument-hint: "[path] [--strict] [--spacer on|off]"
allowed-tools: Bash, Read
---

# doc-mirror:check — did both halves change?

A repository that ships `README.md` beside `README-ko.md` has no mechanism that notices when only one of them moves. This finds the ones that have already drifted.

The pairing convention is **discovered, not configured**: `README-ko.md`, `guide.ja.md`, `docs/setup-pt-br.md` all work untouched, and a directory that keeps no mirrors is never told it is missing them.

<br/>

## Run it

```bash
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/docmirror.py" .
```

| Flag | When |
|---|---|
| `<path>` | a repo other than the working directory |
| `--json` | you need the findings as data rather than as a report |
| `--strict` | exit non-zero on any critical finding — for a CI step or a pre-publish gate |
| `--spacer on\|off` | override whether a `<br/>` is expected above each section heading; the default infers it from what the repo already does |

Read-only. It never writes, never translates, and never creates a missing mirror.

<br/>

## What to report

Lead with the counts line the script printed, then the findings that fired. Keep it short — the report is already grouped.

**🔴 A missing mirror where most of the directory has one.** The strongest signal in the tool: this document was meant to be translated and was not. Say which language, and quote the ratio the script gives — the ratio is the evidence, and without it the finding is just an assertion.

**🔴 A broken relative link.** Cheap to fix, and a link that resolves in the English README but not the Korean one is exactly the asymmetry this plugin exists for.

**🟡 Structural drift.** The pair has diverged in headings, code blocks, tables, lists or links. Quote the two numbers (`headings 12 vs 6`). A pair whose source gained a section and whose mirror did not is the ordinary cause; a wholesale rewrite of one half is the other.

**🟡 An orphan mirror.** A `-ko.md` whose source is gone. Usually a rename that took only one half with it.

**🟢 Outline drift, missing spacers, home-path links.** Cosmetic. Mention the count, not each one.

<br/>

## Judgment the script cannot make

Say these plainly when they apply, because the counts alone will mislead:

- **Retired directories drift on purpose.** A `_deprecated/` or `archive/` tree full of drift findings is not a backlog. Check whether the paths cluster there before presenting a total.
- **Drift is not staleness.** Two files with identical shape can both be a year out of date, and two files with different shape can both be current. This measures structure only. For "which half was edited more recently", that is `census drift`, which reads commit history.
- **Never translate to close a finding.** Creating a missing mirror means writing content in a language, with terminology and tone decisions in it. Offer to draft it only if the user asks; never do it as cleanup.

<br/>

## Hard rules

- Read-only against everything it scans. The script writes nothing at all.
- Do not create a missing mirror, rename a file, or delete an orphan on your own initiative.
- Do not judge whether a translation is accurate or natural. This plugin has no opinion on meaning, and neither should the report.
- If the repo keeps a single language, say so and stop. "No pairs found" is a complete answer, not a failure.
