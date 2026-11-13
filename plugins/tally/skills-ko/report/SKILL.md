---
name: report
description: '로컬 세션 기록과 선택 사항인 hook 판정 로그로 이 Claude Code 하네스가 한 일을 셉니다. 세션 수, 승인하거나 거부한 권한 프롬프트, sub-agent 실행과 그 실행이 상태를 바꾼 호출, tier별 hook 판정, 설정의 크기를 셉니다. 개수만 담은 JSON 파일을 쓰고 보여 줍니다. "hook이 얼마나 자주 걸려?", "프롬프트를 몇 번 승인했어?", "읽기 전용 agent가 뭘 바꿨어?", "하네스 집계해 줘"라는 요청이나, 하네스 지표를 누군가와 공유하기 전에 씁니다.'
argument-hint: '[--since YYYY-MM-DD] [--until YYYY-MM-DD] [--config DIR ...]'
allowed-tools: Bash, Read
---

# report

번들된 집계기로 이 컴퓨터의 세션 기록을 집계하고 결과를 보여 줍니다. 읽기 전용입니다. `~/.claude/projects/`를 읽고 JSON 파일 하나를 쓰며, 어디에도 보내지 않습니다.

<br/>

## 실행

```bash
out="$(mktemp -t tally).json"
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/tally.py" --out "$out" $ARGUMENTS
```

기본값: 세션 기록은 `~/.claude/projects`, hook 로그는 `~/.claude/tally/hook-decisions`, 설정 구조는 `~/.claude`에서 읽습니다. 프로젝트 단위 agent와 hook도 넣으려면 설정 디렉터리마다 `--config <repo>/.claude`를 한 번씩 붙입니다. 세션 기록이 많으면 1~2분 걸립니다. 낮은 우선순위로 실행하세요(`nice -n 10`, macOS에서는 `taskpolicy -b`).

<br/>

## 보고 방식

먼저 기간(`window.first`부터 `window.last`, `window.weeks`)과 파일 경로를 알립니다. 그다음 `totals`에서 다음을 보고합니다.

- **프롬프트**: 사람에게 실제로 물은 횟수는 `permissions.user_allowed_once`와 `permissions.user_rejected`입니다. `auto_*` 항목은 설정, 모드, 분류기가 묻지 않고 결정한 횟수입니다.
- **sub-agent**: `subagents.runs`, 그리고 `runs_readonly_declared` 중 `runs_readonly_declared_with_mutation`의 비율입니다. 이 쌍은 꼭 다시 확인하세요. `tools:`에 파일 쓰기 도구가 없는 agent가 셸을 통해 작업 공간을 바꾼 실행 수입니다.
- **hook**: 판정 로그가 있으면 `hooks_log`를 씁니다. 이 로그는 hook을 `log-wrap.sh`로 감싼 뒤부터 쌓입니다. 없으면 `hooks_transcript`를 씁니다.
- **하네스**: `agents.total` 중 `harness.agents.write_capable`과 `inherit_all_tools`입니다.

주별 행은 사용자가 추이를 물을 때만 작은 표로 보여 줍니다.

숫자를 판단 근거로 쓸 때는 두 가지 한계를 함께 밝힙니다. 첫째, 상태 변경 분류기는 휴리스틱입니다. `mutating_bash_calls`는 많게 세는 쪽으로 틀리고, `scratch_bash_calls`는 임시 디렉터리 안에서 일어났다고 판별한 변경만 셉니다. 둘째, 세션 기록 형식은 Claude Code의 내부 저장 형식입니다. `input.unrecognized_lines`가 늘어나면 집계가 낡았을 수 있습니다.

<br/>

## 하지 않는 일

- 숫자를 설명하려고 세션 기록 원문, 명령어, 경로를 대화에 붙여 넣는 일. 이 파일은 그런 내용을 담지 않으려고 만든 것입니다.
- 결과를 어딘가에 올리거나 보내거나 첨부하는 일. 사용자가 공유하려 하면 먼저 파일을 읽어 보라고 안내합니다. 모든 키는 `schema/tally-v0.schema.json`이 정하므로 읽은 내용이 담긴 전부입니다.
- 로거를 설치하려고 설정을 고치는 일. README의 연결 방법을 보여 주고 사용자가 직접 넣게 합니다.
