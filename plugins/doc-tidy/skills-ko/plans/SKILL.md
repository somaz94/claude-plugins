---
name: plans
description: '자동 정리가 곧 지울 Claude Code plan 파일, 이미 사라진 plan을 가리키는 durable 참조, 워크스페이스 루트에 남은 Markdown을 찾고, 남길 가치가 있는 부분을 영구적인 곳으로 옮기는 일을 돕는다. Claude Code는 cleanupPeriodDays가 지난 최상위 plan 파일을 시작 시 정리 과정에서 지우므로, plan을 가리키던 CLAUDE.md·에이전트·스킬·메모리 파일은 조용히 아무것도 가리키지 않게 된다. "plan 파일이 사라졌어", "곧 만료되는 plan이 뭐야", "삭제된 plan 참조 찾아줘", "plan에서 중요한 것 챙겨줘" 라고 할 때 사용. plan을 살려 두려고 편집하지 않으며, 커밋하지 않는다.'
argument-hint: '[report] [--workspace <root>]… [--recover-from <config repo>]'
allowed-tools: Read, Grep, Glob, Bash(python3 ${CLAUDE_PLUGIN_ROOT}/scripts/doctidy.py plans:*), Bash(python3 ${CLAUDE_PLUGIN_ROOT}/scripts/doctidy.py --check-rules:*)
---

> 이 문서는 [skills/plans/SKILL.md](../../skills/plans/SKILL.md) 의 **한국어 번역본**입니다.
> Claude Code가 실제로 불러오는 것은 영어 원본이며, 이 KO 본은 참고와 사용자 리뷰용입니다.
> 고칠 때는 EN과 KO를 함께 고쳐야 합니다.

# doc-tidy:plans — plan은 만료되지만, plan을 가리키는 참조는 남는다

Claude Code는 plan 파일을 `~/.claude/plans/`(또는 `$CLAUDE_CONFIG_DIR/plans/`)에 둔다. 시작 시 정리
과정에서 수정 시각이 `cleanupPeriodDays` 보다 오래된 최상위 파일을 지운다. 기본값은 30일이다.

plan은 원래 버려도 되는 문서지만, 그 밖의 것들은 그렇지 않다.
- "catalog in `~/.claude/plans/<name>.md`" 라고 적힌 `CLAUDE.md` 한 줄
- plan을 출처로 인용하는 에이전트
- 참고용 plan을 링크하는 plan 템플릿
- 메모리 노트

이것들은 파일이 사라진 뒤에도 여전히 참조처럼 읽힌다. 이 스킬은 사라지기 전과 후 모두에서 그것을
찾는다.

<br/>

## Run it

```bash
python3 ${CLAUDE_PLUGIN_ROOT}/scripts/doctidy.py plans --save <run>/plans.json
```

| 플래그 | 쓰는 때 |
|---|---|
| `--workspace DIR` (반복 가능) | `DIR` 바로 아래에 있으면서 어떤 저장소에도 속하지 않은 `.md` 도 나열하고, 각 문서가 어느 저장소에 관한 것인지 추정한다 |
| `--refs-root DIR` (반복 가능) | 저장소의 `CLAUDE.md`, `AGENTS.md`, `.claude/**` 에서도, 또는 `DIR` 아래 포크가 아닌 모든 저장소의 같은 파일에서도 plan 참조를 찾는다 |
| `--recover-from REPO[:SUBDIR]` | Claude 설정을 git으로 관리한다면 그 저장소를 넘긴다. 깨진 참조마다 plan을 지운 커밋이 붙어 복원할 수 있다 |
| `--project DIR` | 그 프로젝트의 `.claude/settings*.json` 도 읽는다. 더 짧은 `cleanupPeriodDays` 나 사용자 지정 `plansDirectory`를 반영하기 위해서다 |
| `--expiring-days N` | 며칠 이내를 만료 임박으로 볼지(기본 7) |

`<run>`은 세션 scratchpad가 있으면 그것, 없으면 `${CLAUDE_PLUGIN_DATA}/runs/plans-<YYYYmmdd-HHMMSS>`
다.

스크립트는 설정 파일과 메모리 노트만 읽고, 세션 기록(transcript)은 읽지 않는다.

<br/>

## What to report

**🔴 `dangling-plan-ref`.** 존재하지 않는 plan을 가리키는 참조를 plan 별로 묶는다. 참조하는 파일과
줄, 그리고 `recoverable`이 있으면 복원 명령을 나열한다.

**🔴 / 🟡 `plan-expiring`.** 삭제까지 `--expiring-days` 이내로 남은 plan. durable 파일이 가리키고
있으면 critical 이다.

**🟡 `plan-referenced`.** durable 파일이 가리키는 살아 있는 plan. 오늘은 괜찮지만 다음 깨진 참조가
된다.

**🟡 `loose-doc`.** 워크스페이스 루트에 있고 어떤 저장소에도 속하지 않은 문서.

스크립트가 출력한 보존 기간 줄로 시작한다: 일수와 그 값의 출처. 출처가 뜻밖이면(프로젝트 설정이
기간을 줄였다면) 그 자체가 알릴 만한 사실이다.

<br/>

## Judgment

레인 `plans`와 저장한 `plans.json`으로 `doc-tidy-triager` 에이전트를 부른다. 보여주기 전에
`doc-tidy-verdicts` 블록을 검증한다. 그다음 finding 마다 다음과 같이 처리한다.

- **만료 임박 plan.**
  - **EXTRACT** 는 durable 부분(결정, 교훈)을 오래 남을 곳으로 복사한다.
    - 여러 프로젝트에 걸친 규칙: 사용자의 `CLAUDE.md`
    - 한 프로젝트의 규칙과 이력: 저장소 `CLAUDE.md` 나 `docs/`
    - 선호: 메모리
    쓰기 전에 발췌마다 목적지와 함께 보여준다.
  - **EXPIRE** 는 남길 것이 없어 plan이 사라져도 된다는 뜻이다.
  - **plan의 결정 텍스트는 편집하지 않고, 수명을 늘리려고 `touch` 하지도 않는다.** 실제 작업으로
    plan을 갱신하면 시계는 자연스럽게 다시 돈다. 정리를 피하려고 건드리면 문제만 가려진다.
- **깨진 참조.** 삭제된 plan 마다 전략을 하나 고른다.
  - **REPLACE** 는 에이전트가 찾은 durable 대상을 대신 가리킨다.
  - **REMOVE** 는 문장에서 참조를 뺀다.
  - **RESTORE** 는 `git -C <repo> show <recoverable.show>`를 돌린다. plan 이름은 무작위 단어이므로,
    쓰기 전에 앞 20줄을 보여주고 주제가 맞는지 확인한다. 아직 중요한 부분은 참조 위치에
    **인라인**하거나 지정한 durable 경로에 둔다. plans 디렉터리 최상위로는 **절대** 복원하지 않는다.
    거기 두면 다시 만료된다.
  - 예시일 뿐인 참조는 `<plan-name>.md` 자리표시자로 바꾼다.
- **메모리 파일.** 메모리 노트 안의 참조는 **파일별로** 동의할 때만 고친다. 메모리 인덱스도 함께
  맞춘다.
- **흩어진 문서.**
  - 관련된 저장소로 MOVE: 미추적 상태로 도착하고, 원본은 `${CLAUDE_PLUGIN_DATA}/trash/`로 간다.
  - 앞 20줄을 보여준 뒤 삭제.
  - 제자리에 둔다.

git 밖의 파일(설정 디렉터리, 워크스페이스 루트)은 편집 전에 `<run>/backup/`에 복사한다. `report`는
finding과 판정까지만 한다.

<br/>

## Hard rules

- **승인은 이 대화 안의 명시적인 답이다.**
- **묻지 않고 도는 것은 `plans`와 `--check-rules` 뿐이다.** 모든 편집·이동·삭제는 먼저 보여준다.
- **흩어진 문서를 `rm` 하지 않는다.** git 이력이 없다. trash 디렉터리로 옮긴다.
- **커밋·push·태그하지 않는다.**
- 여기 설명한 보존 동작은 작성 시점의 Claude Code 기준이다. 스크립트도 같은 주의 문구를 출력한다.
  Claude Code를 업그레이드한 뒤 다시 확인한다.

<br/>

## References

- 스크립트: `scripts/doctidy.py`(`plans` 서브커맨드). 에이전트: `agents/doc-tidy-triager.md`.
- 저장소 레인: `/doc-tidy:triage`.
- 현재 plan을 최신으로 유지하는 것은 `session-continuity`의 일이다. plan이 끝난 뒤 무엇을 남길지
  정하는 것은 이 스킬의 일이다.
