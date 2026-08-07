---
description: '의미 있는 작업 한 덩어리가 끝난 뒤 plan-progress-updater 로 ~/.claude/plans/<name>.md 를 제자리 갱신'
argument-hint: "[plan-name | free-form what-changed summary]"
allowed-tools: Read, Grep, Glob, Bash
---

> 본 문서는 [commands/plan-update.md](../commands/plan-update.md) 의 **한국어 번역본** 입니다.
> Claude Code 가 실제 로드하는 것은 영어 원본이며, 본 KO 본은 참조 / 사용자 리뷰 용도입니다.
> 수정 시 EN + KO 둘 다 동시 수정해야 합니다.

# /plan-update

`plan-progress-updater` agent 의 thin wrapper. 작업이 진행되는 동안 `~/.claude/plans/*.md` plan 파일을 최신 상태로 유지한다 — Progress 표, "What changed" 섹션, lessons-learned 꼬리를 갱신해서 **다음 세션** 이 (context reset 이후) context 를 다시 도출하지 않고 깔끔히 이어갈 수 있게 한다.

사용자 호출 인자: `$ARGUMENTS`

<br/>

## 인자 해석

- 비어 있음 → 최근 대화에서 열려 있는 plan 참조를 살핀다. 정확히 하나가 걸려 있으면 그것을 쓰고, 아니면 어떤 plan 인지 사용자에게 묻는다
- `<plan-name>` → `~/.claude/plans/<plan-name>.md` 를 바로 사용 (`.md` 접미사는 생략하며 agent 가 해석한다)
- 자유 형식 텍스트 → agent 가 갱신에 녹여야 할 "what changed" 요약으로 취급한다

<br/>

## 언제 쓰나

- 진행 중인 plan 의 phase / 하위 작업이 끝났을 때.
- blocker 가 해소됐을 때.
- plan 도중 범위가 바뀌었을 때 (새 제약, 결정 번복, 예상 못 한 엣지 케이스).
- 사용자가 "plan 업데이트해줘" / "plan 에 반영해줘" 라고 할 때.

쓰지 않는 경우:

- 작업이 plan 파일과 엮여 있지 않을 때 — memory 기록을 하거나 그냥 계속한다.
- plan 이 끝났을 때 — 새 세션이 이어받아야 한다면 대신 `~/.claude/agents/session-handoff-prompter.md` 를 (`/handoff` 로) 쓴다.
- plan 을 새로 만들 때 — 그것은 이 커맨드가 아니라 plan-mode 의 몫이다.

<br/>

## Step 1 — `plan-progress-updater` 에 위임

다음을 주어 agent 를 호출한다:

- plan 파일 경로 (절대 경로).
- `$ARGUMENTS` 또는 최근 대화에서 온 "what changed" 요약.
- 선택: 뒤집어야 할 Progress 표 행 지시자 (`A1`, `B2`, `C3` 등).

agent 는 plan 을 제자리에서 수정한다 — 보통 다음과 같다:

- Progress 행 상태를 뒤집고 (⬜ pending → 🟢 in-progress → ✅ completed) 날짜/메모를 붙인다.
- "What changed" 섹션에 오늘 날짜 + 사용자 요약을 덧붙인다.
- 자명하지 않은 결정이 내려졌으면 "Lessons / Conventions to preserve" 에 덧붙인다.
- 다음 단계가 바뀌었으면 "next session prompt" 섹션을 갱신한다.

<br/>

## Step 2 — diff 제시 (커밋 없음)

plan 파일에 대한 diff 를 한 화면 요약으로 보여준다. plan 변경을 자동 커밋하지 않는다 — plan 이 추적하는 대상과 함께 언제 커밋할지는 사용자가 정한다.

사용자가 plan 변경을 즉시 커밋하고 싶어 하면 `/commit` 에 위임한다 (별도 사이클).

<br/>

## 하드 룰

- `plan-progress-updater` 는 `~/.claude/plans/*.md` 파일을 **제자리에서** 수정한다. 새 plan 파일을 만들지 않고 (plan-mode 의 몫), repo 코드를 건드리지 않으며, `git commit` / `git push` 를 실행하지 않는다.
- plan 파일 쓰기는 `plan-progress-updater` 자체 규율에 따라 사용자 승인이 필요하다 — 이 커맨드는 그것을 우회하지 않는다.
- plan 수정은 외과적으로 유지한다: progress 행 + what-changed 줄 + lessons 줄. 사용자가 명시적으로 재구성을 요청하지 않는 한 plan 전체를 다시 쓰지 않는다.
- plan 의 기존 언어와 톤을 보존한다. plan 은 다음 세션이 읽는 것이지 새 독자를 위해 다시 쓰이는 것이 아니다.

<br/>

## 참조

- KO 페어: `~/.claude/commands-ko/plan-update.md`
- 주 agent: `~/.claude/agents/plan-progress-updater.md`
- 동반: `~/.claude/agents/session-handoff-prompter.md` (다음 세션 handoff 용이며 제자리 갱신용이 아님) — `/handoff` 로 호출
