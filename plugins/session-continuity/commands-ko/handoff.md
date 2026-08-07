---
description: 'session-handoff-prompter 를 통해 다음 Claude Code 세션을 위한 자체완결형 handoff prompt 를 생성'
argument-hint: "[plan-name | now/mid-session | free-form context]"
allowed-tools: Read, Grep, Glob, Bash
---

> 본 문서는 [commands/handoff.md](../commands/handoff.md) 의 **한국어 번역본** 입니다.
> Claude Code 가 실제 로드하는 것은 영어 원본이며, 본 KO 본은 참조 / 사용자 리뷰 용도입니다.
> 수정 시 EN + KO 둘 다 동시 수정해야 합니다.

# /handoff

`session-handoff-prompter` agent 의 thin wrapper. 사용자가 새 chat 에 붙여넣으면 context reset 이후에도 복잡한 multi-step 작업을 깔끔히 이어갈 수 있는 단일 Markdown 코드 블록을 생성한다.

사용자 호출 인자: `$ARGUMENTS`

<br/>

## 인자 해석

- 비어 있음 → 어떤 plan / context 를 기준으로 handoff 할지 사용자에게 묻는다 (대화형)
- `<plan-name>` (예: `inventory-gap-coverage-cobalt-stargazer`) → `~/.claude/plans/<plan-name>.md` 를 주 출처로 사용 (**`end-of-day` 모드**)
- `now` / `mid-session` → **`mid-session` 모드**: 작업 도중 context 가 차오르는 상황에서, 이 세션은 열어둔 채 새 세션으로 넘긴다. agent 는 진행 중 상태를 `git` (브랜치, 미커밋 diff, stash, 편집 중인 `file:line`) + 대화에서 종합한다 — plan 파일은 필수가 **아니다**. 더 가벼운 ~30-60줄 블록을 만든다.
- 자유 형식 텍스트 → agent 가 handoff 에 녹여야 할 추가 context 로 취급한다 (현재 blocker, 사용자가 못박고 싶은 결정 등)

<br/>

## Step 1 — `session-handoff-prompter` 에 위임

다음을 주어 agent 를 호출한다:

- 해석된 plan 파일 경로 (있다면) — agent 가 직접 읽는다.
- `$ARGUMENTS` 의 자유 형식 context.
- 사용자의 최근 2-3 턴 대화 context (즉시 다음 액션은 agent 가 추론하게 둔다).

agent 는 다음을 담은 단일 markdown 코드 블록을 만든다:

1. plan 파일 참조 + "이것부터 읽어라" 지시
2. 이전 세션이 무엇을 냈는지 1-2 문장 요약
3. 즉시 다음 액션 (절대 경로, 우선순위, 이미 확정된 결정)
4. 새 세션이 달리 알 수 없는 하드 룰 + 컨벤션
5. 새 세션이 꺼내 써야 할 sub-agent
6. 남겨둔 `<TBD>` placeholder
7. 첫 번째로 실행할 커맨드

<br/>

## Step 2 — 제시 (실행 없음)

agent 의 출력이 곧 handoff prompt 다. 사용자가 한 번에 복사할 수 있도록 **그대로 보여준다**.

새 세션에 자동으로 붙여넣지 않는다 — 사용자가 직접 새 chat 으로 복사한다.

<br/>

## 하드 룰

- 읽기 전용 — `session-handoff-prompter` 는 plan 파일을 수정하지 않고 (`plan-progress-updater` 의 몫), 새 plan 파일을 만들지 않으며 (plan-mode 의 몫), 어떤 작업도 실행하지 않고, `git commit` / `git push` 등 상태를 바꾸는 조작을 하지 않는다.
- agent 는 plan 의 Progress 표 + What changed 섹션에서 "즉시 다음 액션" 을 추론한다 — 그 작업을 직접 중복해서 하지 않는다.
- plan 파일이 없거나 읽을 수 없으면, agent 는 handoff 를 지어내지 말고 "plan file not found at <path>" 라고 분명히 밝히며 중단해야 한다.
- 출력은 **새 세션에 붙여넣기** 위한 것이다 — 자체완결적으로 유지한다 (대화 내 참조, 예컨대 "앞서 얘기한 대로" 같은 표현 금지).

<br/>

## 참조

- KO 페어: `~/.claude/commands-ko/handoff.md`
- 주 agent: `~/.claude/agents/session-handoff-prompter.md`
- 동반: `~/.claude/agents/plan-progress-updater.md` (in-place plan 갱신용이며, handoff 생성용이 아님)
