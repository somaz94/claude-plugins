# tally

Claude Code 하네스가 한 일을 셉니다. 무슨 말을 했는지는 남기지 않습니다.

> 이 문서는 [README.md](README.md)의 한국어판입니다. 내용이 어긋날 경우 영문판이 기준입니다.

Claude Code 세션 안에서:

```
/plugin marketplace add somaz94/claude-plugins
/plugin install tally@somaz94
```

대화형 세션에 들어가지 않고 셸에서 바로 설치하려면:

```bash
claude plugin marketplace add somaz94/claude-plugins
claude plugin install tally@somaz94
```

<br/>

## 왜 세는가

하네스는 무엇이 잘못될지에 대한 추측으로 만들어집니다. 여기에 hook 하나, 저기에 읽기 전용 agent 하나, 되돌릴 수 없는 단계 앞에 승인 게이트 하나를 둡니다. 그 추측이 맞았는지는 거의 확인하지 않습니다. guard는 실제 실수에 걸리나요, 아니면 명령마다 걸리나요? 프롬프트를 읽고 승인하나요, 반사적으로 승인하나요? 읽기 전용으로 선언한 agent는 정말 읽기만 하나요, 아니면 셸로 무언가를 바꾸나요?

답은 이미 디스크의 세션 기록에 있습니다. 다른 것도 다 들어 있습니다. 소스 코드, 호스트 이름, 가끔은 비밀 값까지 있습니다. 그래서 아무도 세션 기록을 공유하지 않고, 하네스 주인의 숫자는 그 컴퓨터를 거의 떠나지 않습니다.

`tally`는 숫자만 남기고 텍스트는 버립니다. 로컬에서 읽고, 개수와 날짜와 고정된 라벨만 담은 JSON 파일 하나를 씁니다. 모든 키는 [`schema/tally-v0.schema.json`](schema/tally-v0.schema.json)이 정하고, 모든 단계에 `additionalProperties: false`가 걸려 있어서 이름이나 경로가 들어갈 자리가 없습니다. 파일을 읽었다면 이 파일이 누군가에게 알려 줄 내용을 전부 읽은 것입니다.

<br/>

## 두 부분

| 구성 | 하는 일 |
|---|---|
| `scripts/log-wrap.sh` | 이미 쓰는 hook을 감쌉니다. 출력과 종료 코드는 그대로 넘기고, 조용하지 않은 판정마다 로컬 로그에 한 줄을 남깁니다 |
| `scripts/tally.py`, `/tally:report` | 세션 기록, 판정 로그, 설정 디렉터리를 읽고 개수만 담은 JSON을 씁니다 |

집계기는 래퍼 없이도 동작합니다. Claude Code의 세션 기록에 권한 판정과 일부 hook 결과가 이미 남기 때문입니다. 래퍼는 세션 기록에 없는 정보, 곧 어느 hook이 어떤 tier로 무엇을 판정했는지를 더합니다.

<br/>

## 로거 연결하기

`settings.json`의 hook 명령 앞에 `log-wrap.sh`를 붙입니다.

```json
{
  "type": "command",
  "command": "~/.claude/hooks/log-wrap.sh --tier ask ~/.claude/hooks/my-guard.sh"
}
```

먼저 스크립트를 바뀌지 않는 경로에 복사하세요. 설치된 플러그인은 버전 이름이 붙은 디렉터리에 있어서, 플러그인 캐시를 가리키는 hook은 다음 업데이트 때 아무 경고 없이 깨집니다.

tier는 `--tier <tier>`로 주거나, hook 스크립트 위쪽에 `# tally: tier=<tier>` 줄을 두거나, 비워 둡니다. 기록은 `~/.claude/tally/hook-decisions/decisions-YYYY-MM.jsonl`에 쌓이고, `TALLY_LOG_DIR`로 위치를 바꿀 수 있습니다. `tally.py`도 같은 변수를 읽으므로 두 부분이 함께 따라갑니다.

래퍼는 실패해도 드러나지 않도록 만들었습니다. hook의 stdout, stderr, 종료 코드는 바이트 하나 바뀌지 않고 넘어가고, 로그 오류는 삼키며, hook이 없으면 0으로 끝납니다. 판정을 바꿀 수 있는 래퍼라면 아무도 검토하지 않은 두 번째 hook이 됩니다.

기록 한 줄에 담기는 것: 시각, hook 파일 이름, tier, 이벤트, 도구, 판정, 종료 코드, 세션과 tool-use ID, 편집한 파일 경로, hook이 낸 사유 메시지, 그리고 셸 명령이면 첫 단어와 12자 hash만 담깁니다. 명령 원문은 쓰지 않습니다. 로그는 내 컴퓨터에만 있고, `tally.py`는 그 로그에서 개수만 읽습니다.

<br/>

## 무엇을 세나

ISO 주(UTC) 단위와 전체 합계로 셉니다.

- **세션과 메인 세션 호출**: 도구 호출, 파일 편집, 상태를 바꾼 셸 호출, 질문 수, 중단, 압축, sub-agent 실행 요청.
- **권한**: 설정에 따른 자동 허용 또는 거부(기록의 `config` 출처), 사람이 한 번 허용, 사람이 저장한 규칙으로 허용, hook이 거부, 사람이 거부.
- **sub-agent**: 실행 수, 직접 만든 agent의 실행 수, 상태를 바꾼 실행 수, 그리고 `tools:`에 파일 쓰기 도구가 없는 agent에 대한 같은 값. 마지막 쌍은 읽기 전용 선언만으로는 답할 수 없는 질문, 곧 그 agent가 선언 안에 머물렀는지에 답합니다.
- **hook**: 로그에 남은 종류별·tier별 판정, 세션 기록에 남은 hook 결과.
- **하네스**: agent 수(쓰기 가능, 모든 도구 상속), command 수, skill 수, 이벤트별 hook 수.

<br/>

## 한계

- **상태 변경 판정은 휴리스틱입니다.** 셸 호출은 알려진 쓰기 명령을 실행할 때 셉니다. `rm`, `mv`, `cp`, `sed -i`, 쓰기를 하는 `git` / `gh` / `glab` / `kubectl` / `helm` / `terraform` 하위 명령, `gh api`를 통한 쓰기가 여기에 해당합니다. 임시 디렉터리 안에서만 일어난 변경은 scratch로 따로 셉니다. 분류기가 위치를 판별하지 못한 변경은 작업 공간 변경으로 세므로 이 숫자는 많게 나오는 쪽으로 틀립니다. 출력 리다이렉션(`>`)은 아예 세지 않습니다. 분류기 버전은 출력의 `classifier`에 남고, 버전이 다른 두 파일은 비교하지 않습니다.
- **세션 기록 형식은 내부 형식입니다.** 공개 API가 아니라 Claude Code의 저장 형식입니다. 읽지 못한 줄은 추측하지 않고 `input.unrecognized_lines`로 셉니다. 이 숫자가 늘어나면 집계가 낡아 가는 중입니다.
- **agent 이름은 로컬에만 있습니다.** 실행이 사용자가 만든 agent인지는 실행마다 남는 메타데이터 파일로 판단합니다. 그 파일이 없거나 해당 필드가 생기기 전의 것이면 내장 agent로 셉니다. 읽기 전용 구분은 여기에 더해 실행의 agent 종류가 `--config` 디렉터리 안의 정의와 맞아야 하므로, 플러그인 agent나 넘기지 않은 디렉터리의 agent는 그 구분에서 빠집니다.
- **스키마 v0은 바뀔 수 있습니다.** 이 스키마로 데이터를 모으기 전에 바뀔 수 있습니다. 출력에 스키마 이름이 들어 있어서 읽는 쪽이 구분할 수 있습니다.

<br/>

## 스크립트 직접 실행하기

```bash
python3 scripts/tally.py --out tally.json
python3 scripts/tally.py --since 2026-10-01 --config ~/.claude --config ./.claude
```

표준 라이브러리만 쓰고, Python 3.10 이상이 필요합니다. 기록이 많으면 낮은 우선순위로 실행하세요.

<br/>

## 절대 하지 않는 것

- 명령 원문, 프롬프트, 파일 내용, 그리고 어떤 종류의 이름도 출력에 쓰지 않습니다.
- 어디에도 보내지 않습니다. 파일을 공유할지는 읽어 본 뒤 직접 정합니다.
- 설정을 고치지 않습니다. 로거 연결은 직접 하는 변경입니다.

<br/>

## 릴리스

이 마켓플레이스의 플러그인은 각자 독립적으로 버전을 매기고 릴리스합니다. `tally`의 모든 변경 이력은 이 디렉터리의 커밋만 모은 [tally 릴리스](https://github.com/somaz94/claude-plugins/releases?q=tally&expanded=true)에서 볼 수 있습니다.

<br/>

## 라이선스

MIT — [LICENSE](../../LICENSE)를 참고하세요.
