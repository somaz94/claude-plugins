# korean-prose

Reviews Korean writing for one thing: whether it reads like Korean somebody wrote, or like English wearing Korean words.

> 한국어 문서는 [README-ko.md](README-ko.md)를 참고하세요.

In Claude Code:

```
/plugin marketplace add somaz94/claude-plugins
/plugin install korean-prose@somaz94
```

Or from your shell, without an interactive session:

```bash
claude plugin marketplace add somaz94/claude-plugins
claude plugin install korean-prose@somaz94
```

<br/>

## Why this exists

A spell-checker reads one word. A grammar checker reads one sentence. Both pass the sentence `본 문서는 마이그레이션에 있어서 중요성을 가진다` without a complaint, because every word is spelled correctly and the grammar is intact. It is still not Korean anybody writes.

That gap is where translated documentation lives. A README written in English and rendered into Korean keeps the English skeleton — the possessive chains, the passive voice, the "it is important to" frame — and the result reads like a document you have to decode rather than read. Nothing flags it, because nothing is wrong at the level any tool checks.

The newer half of the problem is not translation at all. Text that was **generated** carries its own tells: the filler discourse marker, the mechanical `첫째 … 둘째 …`, the contrast frame used three times in one paragraph. Those are invisible to a translation-aware eye, because nothing was translated.

<br/>

## What it catches

| Pattern | Why it reads wrong | What it should be |
|---|---|---|
| `~에 있어서` | Japanese-syntax residue, near-dead in modern Korean | `~에서`, or drop it |
| `중요성을 가지다` | English "to have importance" | `중요하다` |
| `검토 진행`, `구축 진행` | Nominalize a word that is already a verb | `검토했다`, `구축했다` |
| `압력을 완화` | `pressure` translated to the word for *air* pressure | `부하를 완화` |
| `간접화` | `indirection` rendered as a word that does not exist | `분리` |
| `~하는 것이 가능하다` | Calque of "it is possible to" | `~할 수 있다` |
| `되어지다`, `보여지다` | Passive applied twice | `되다`, `보이다` |
| `GitHub Actions 의` | A particle detached from its word by a space | `GitHub Actions의` |

The middle rows are the ones worth the install. `압력`, `정렬`, `수렴`, `재활용`, `함정`, `완주` are all real Korean words that a dictionary will happily offer for the English term — and all six point somewhere else once they land in a technical sentence. The translation happened, and destroyed the meaning on the way. The plugin ships that substitution dictionary with the reason each one fails.

<br/>

## The pass a token scan cannot do

Every pattern above is visible inside one line. The findings that survive a clean sweep are only visible when a line is read **against its neighbours**, and they are the ones a human reviewer notices last:

- **형제 구조 이탈** — one bullet in a list is two sentences where the rest are one, or leads with a verb where the rest lead with the outcome. It is fine alone and wrong in place.
- **이웃 중복** — two items in the same block say the same thing. Not every overlap is a defect: a technology name repeated across an approach line and an outcome line is required, not redundant. The plugin sorts the three kinds and reports only the one that is a finding.
- **과장 드리프트** — `차단` where the evidence says `방지`, or a condition that never existed described as a defect that was fixed.
- **표기 일관성** — `Pod` in one paragraph and `파드` in the next, usually because a later edit followed the writer's habit rather than the file's.
- **용어 도입 관례** — a bare `fail-fast` in prose that writes `부분 장애(gray failure)` everywhere else. Spelling-consistency passes it trivially; the document's own convention does not.

Each is reported with its evidence — the sibling lines compared, or the two counts. A finding you cannot check is not a finding.

<br/>

## Two modes, and picking the wrong one is the usual disappointment

**감사** is a sweep: point it at a file, a directory, or nothing, and get every finding categorized. Read-only, always — a bulk apply you have not read line by line is exactly what that restriction exists to prevent.

**판정** is a verdict on one thing: you rewrote a sentence and want to know whether it is better. It reads the item *in place* with its siblings, keeps the improvements your version already made and says which ones it kept, and can apply the merged line — after showing 전/후 in full and being told yes.

Somebody holding one rewritten sentence does not want a forty-finding file audit. When the invocation is ambiguous the plugin asks in one line and defaults to 판정, because that is the cheaper wrong answer.

<br/>

## Scope

In scope: any `.md` whose body is predominantly Korean prose, and the `ko:` half of a YAML file whose strings are `{ ko, en }` pairs.

Out of scope: the `en:` half, `*-en.md`, English `.md`, code blocks, inline code, URLs, file paths, and comments in source files. Korean-only, in both directions — it will not rewrite your English to match a Korean change, and it will say so if a fix puts the pair out of parity.

In a bilingual file it reads the `en:` sibling **first**. These documents are usually authored EN → KO, so the Korean half is the degraded one: the object, the qualifier, or the unit fell out in translation while English still states it. `QA delivery-time` became `QA 전달 90% 단축` — the unit vanished, and what actually shrank was the time. Restoring from the English half makes the rewrite verifiable instead of creative.

<br/>

## What it never does

- Changes what the text claims. Numbers, technology labels, dates, and arrows survive every rewrite; a fact that looks wrong is flagged, never silently corrected.
- Edits in 감사 mode, however obvious the fix looks.
- Edits in 판정 mode without showing 전/후 in full and being told yes — and one approval covers one item, never the file.
- Touches the `en:` half or any English document.
- Adds, removes, reorders, merges, or splits a heading, section, list item, or table row.
- Flags a spaced em-dash. It is deliberate in technical prose, and a rule against it buries every real finding.

<br/>

## Releases

Each plugin in this marketplace is versioned and released on its own. Every change to `korean-prose` — with the commits scoped to this directory — is at [korean-prose releases](https://github.com/somaz94/claude-plugins/releases?q=korean-prose&expanded=true).

<br/>

## License

MIT — see [LICENSE](../../LICENSE).
