---
description: '다음 Claude Code 세션을 위한 자체완결형 handoff prompt 를 session-handoff-prompter 스펙에 따라 인라인으로 생성'
argument-hint: "[plan-name | now/mid-session | free-form context]"
allowed-tools: Read, Grep, Glob, Bash
---

# /handoff

> 본 문서는 [commands/handoff.md](../commands/handoff.md) 의 **한국어 번역본** 입니다.
> Claude Code 가 실제 로드하는 것은 영어 원본이며, 본 KO 본은 참조 / 사용자 리뷰 용도입니다.
> 수정 시 EN + KO 둘 다 동시 수정해야 합니다.

<br/>

사용자가 새 chat 에 붙여넣으면 context reset 이후에도 복잡한 multi-step 작업을 깔끔히 이어갈 수 있는 단일 Markdown 코드 블록을 만든다.

**이 세션에서 인라인으로 작성한다 — sub-agent 에 위임하지 않는다.** handoff 의 주 입력은 **이번 대화** 인데 sub-agent 는 그것을 볼 수 없다. 위임하면 내용이 얇아지고, 블록이 **두 번** 렌더링된다 (agent 의 리포트, 그리고 호출자의 재출력). 이 플러그인에 함께 실리는 `session-handoff-prompter` agent (`agents/session-handoff-prompter.md`) 는 따라야 할 스펙으로 남으며, 이 command 밖의 proactive handoff 에는 여전히 직접 호출할 수 있다.

사용자 호출 인자: `$ARGUMENTS`

<br/>

## 인자 해석

- 비어 있음 → 어떤 plan / context 를 기준으로 handoff 할지 사용자에게 묻는다 (대화형)
- `<plan-name>` (예: `inventory-gap-coverage-cobalt-stargazer`) → `~/.claude/plans/<plan-name>.md` 를 주 출처로 사용 (**`end-of-day` 모드**)
- `now` / `mid-session` → **`mid-session` 모드**: 작업 도중 context 가 차오르는 상황에서, 이 세션은 열어둔 채 새 세션으로 넘긴다. 진행 중 상태를 `git` (브랜치, 미커밋 diff, stash, 편집 중인 `file:line`) + 이번 대화에서 종합한다 — plan 파일은 필수가 **아니다**. 더 가벼운 ~30-60줄 블록을 만든다.
- 자유 형식 텍스트 → handoff 에 녹여야 할 추가 context 로 취급한다 (현재 blocker, 사용자가 못박고 싶은 결정 등)

<br/>

## Step 1 — 블록 작성

`session-handoff-prompter` agent 정의 (`agents/session-handoff-prompter.md`) 를 authoring 스펙으로 따른다 — 모드 선택, 두 가지 skeleton, 내용 체크리스트, 출력 스타일, 읽기 전용 Bash 정책은 모두 그 문서의 몫이다. 여기서 되풀이하지 않는다.

입력:

- 해석된 plan 파일 경로 (있다면) — 직접 `Read` 로 전부 읽는다.
- `$ARGUMENTS` 의 자유 형식 context.
- **이번 세션의 대화** — 무엇을 냈고, 무엇이 정해졌고, 무엇이 편집 도중 끊겼는지. 스펙이 공급할 수 없는 입력이자, 이 command 가 위임 대신 인라인으로 도는 이유다.

블록에 담아야 할 것:

1. plan 파일 참조 + "이것부터 읽어라" 지시
2. 이번 세션이 무엇을 냈는지 1-2 문장 요약
3. 즉시 다음 액션 (절대 경로, 우선순위, 이미 확정된 결정)
4. 새 세션이 달리 알 수 없는 하드 룰 + 컨벤션
5. 새 세션이 꺼내 써야 할 sub-agent
6. 남겨둔 `<TBD>` placeholder
7. 첫 번째로 실행할 커맨드

<br/>

## Step 2 — 출력 (실행 없음)

1줄 소개를 찍고, 이어서 **정확히 하나의** 코드 블록을 낸다. 뒤에 덧붙이는 설명을 달지 않고, 어떤 형태로든 블록을 두 번째로 렌더링하지 않는다 — 재진술도, 내용 요약도, "다시 한 번" 도 하지 않는다.

새 세션에 자동으로 붙여넣지 않는다 — 사용자가 직접 새 chat 으로 복사한다.

<br/>

## 하드 룰

- 읽기 전용 — plan 파일을 수정하지 않고 (`plan-progress-updater` 의 몫), 새 plan 파일을 만들지 않으며 (plan-mode 의 몫), 대기 중인 작업을 실행하지 않고, `git commit` / `git push` 등 상태를 바꾸는 조작을 하지 않는다.
- plan 이 있으면 Progress 표 + What changed 섹션에서, 없으면 live `git` 상태 + 이번 대화에서 "즉시 다음 액션" 을 추론한다.
- plan 파일을 지정했는데 없거나 읽을 수 없으면, handoff 를 지어내지 말고 "plan file not found at <path>" 라고 분명히 밝히며 중단한다.
- 출력은 **새 세션에 붙여넣기** 위한 것이다 — 자체완결적으로 유지한다 (대화 내 참조, 예컨대 "앞서 얘기한 대로" 같은 표현 금지).

<br/>

## 참조

- EN 페어: `commands/handoff.md`
- authoring 스펙: `agents/session-handoff-prompter.md` — 본 command 가 따르는 규칙. 이 command 밖의 proactive handoff 에는 sub-agent 로 직접 호출할 수 있다.
- 동반: `agents/plan-progress-updater.md` (제자리 plan 갱신용이며 handoff 생성용이 아님) — `/plan-update` 로 호출
