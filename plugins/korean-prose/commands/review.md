---
description: 'Korean-naturalness review via the awkward-korean-reviewer agent — two lanes (Korean Markdown · bilingual `{ ko, en }` YAML) and two modes (감사 file sweep, read-only · 판정 single-item verdict, Edit after 전/후 approval)'
argument-hint: "[file | dir | pasted rewrite | empty=changed KO surfaces]"
allowed-tools: Read, Grep, Glob, Bash, Edit
---

# /korean-prose:review

Thin wrapper around the `awkward-korean-reviewer` agent. Reports awkwardness with concrete KO-only rewrites that preserve every fact, number, English technology label, code block, link, and path.

User invocation argument: `$ARGUMENTS`

<br/>

## Lane resolution — the two are co-equal

Neither lane is the default and neither is supplementary. Pick from what the target is.

- **Lane A — Korean Markdown**: any `.md` whose body is predominantly Korean prose — a README, a docs page, a blog post draft, a design note.
- **Lane B — bilingual YAML**: the `ko:` half of a YAML file whose strings are `{ ko, en }` pairs.

Out of scope in both lanes: the `en:` half, `*-en.md`, English `.md`, code blocks, inline code, URLs, paths, and source-file comments.

<br/>

## Mode resolution — decide this before anything else

| `$ARGUMENTS` | Mode |
|---|---|
| empty, a path, a directory, "전체" / "전부" / "스캔" | **감사** |
| pasted Korean prose, a proposed rewrite, "이렇게?" / "이거 어때" / "어색해" | **판정** |
| prose **and** a path | **판정** on that item, then *offer* 감사 — never run it uninvited |
| a path + "내가 고친 것들 봐줘" | **감사**, scoped to `git diff` rather than the whole file |

Ambiguous → ask in one line, default to **판정**. An unwanted audit is a wall of text; a 판정 the user wanted broad costs one follow-up.

**empty resolves to BOTH lanes**: Korean `.md` **and** `ko:` lines in bilingual YAML that `git diff --name-only` (plus untracked) reports. Scoping empty to Markdown alone silently skips the surface many users edit most.

<br/>

## Step 1 — Delegate to `awkward-korean-reviewer`

State the lane and the mode in one line, then invoke. The agent runs two passes:

1. **Token pass** — 직역체 / 번역체 (`~에 있어서`, `~을 통해서` overuse, `의` chains, passive overuse, abstract-noun + `~을 가지다`), 일본어식 한자어 (`검토 진행`, `~화 시키다`), AI 티 patterns (`~하는 것이 가능`, `결론적으로`, mechanical parallelism, 이중 피동, `~하고,`), 조사 누락·오선택, 종결법 혼용, tense, 외래어 표기, 호응 오류, and the 영어 개념 1:1 치환 사전.
2. **대조 pass** — the findings a token scan cannot see, and the reason a clean token sweep is not a clean review: **형제 구조 이탈** (one bullet shaped unlike its siblings), **이웃 중복** (two items repeating one rationale), **과장 드리프트** (`최소화` / `차단` / `완전` where the evidence says `경감` / `방지`), **표기 일관성** file-wide (`Pod` vs `파드`), **용어 도입 관례** (a bare English concept in prose that glosses every other one).

Every 대조 finding must carry its evidence — the sibling lines compared, or the two grep counts. Without it the finding is unfalsifiable and the user cannot check it.

<br/>

## Step 2 — Apply, item by item

- **감사 모드 output is read-only.** The agent must not edit in this mode, however obvious a fix looks. It ends by offering to apply specific items.
- **판정 모드 may apply** — but only after showing 전/후 **in full** (no diff fragments, no `…` elision) and being told yes. One approval covers one item, never the rest of the file.
- Cross-file sync is part of the edit: a `ko:` string living in two files is edited in both in the same turn, or in neither.
- After any YAML edit, run a parse check and report it.
- Surface the commit as the user's next step. The agent never commits, pushes, or runs a build target.

<br/>

## Hard rules

- The `en:` half, `*-en.md`, and English `.md` are out of scope in every mode — never rewritten, not even to match a KO change. If a KO fix breaks KO↔EN parity, say so and stop.
- Pair / structural drift (heading-count, code-block, table drift between the two copies) is **not** this command's job → the `doc-mirror` plugin.
- Preserve every fact, number, English technology label, code block, link, and path in every rewrite; match the document's existing tone and register.
- Never invent a metric, a headcount, or any number. If a fact looks wrong, flag it — do not silently change it.
- Never add, remove, reorder, merge, or split a heading, section, list item, or table row. This is a text-only pass.

<br/>

## References

- KO pair: `commands-ko/review.md`
- Primary agent: `agents/awkward-korean-reviewer.md`
- Companion plugin: `doc-mirror` — translation-pair completeness and structural drift, the axis this command deliberately leaves alone
