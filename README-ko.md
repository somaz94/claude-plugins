# claude-plugins

[Claude Code](https://code.claude.com/docs) 플러그인 마켓플레이스입니다.

> 이 문서는 [README.md](README.md)의 한국어판입니다. 내용이 어긋날 경우 영문판이 기준입니다.

```bash
/plugin marketplace add somaz94/claude-plugins
/plugin install census@somaz94
```

<br/>

## 플러그인

| 플러그인 | 하는 일 |
|---|---|
| [`census`](plugins/census) | 흩어진 `.claude/` 설정을 읽기 전용으로 감사 — 가진 것을 목록화하고, 어긋난 곳을 찾고, 팀에 공유할 만큼 이식 가능한 항목을 가려냅니다 |
| [`shell-portability`](plugins/shell-portability) | 셸 스크립트의 bash/zsh 이식성 검토 — `shellcheck`이 다루지 않는 축입니다. `shellcheck`은 shebang이 선언한 셸을 검사하지 실제로 실행될 셸을 검사하지 않습니다 |
| [`session-continuity`](plugins/session-continuity) | 컨텍스트 리셋을 가로질러 장기 작업을 이어 나름 — 진행하면서 plan 파일을 갱신하고, 새 세션이 시작할 핸드오프 프롬프트를 만듭니다 |
| [`release-guards`](plugins/release-guards) | 되돌릴 수 없는 릴리스 동작 앞에 확인 절차 — 태그 생성·삭제, 릴리스 발행, 그리고 그것들을 만들어 내는 자동화 파일 편집 |
| [`sensitive-guard`](plugins/sensitive-guard) | 비밀값을 커밋되는 순간에 차단 — 커밋이 추가하는 줄에 대한 마지막 관문과, 공개 전 요청 시 실행하는 전체 스캔 |

각 플러그인 문서 (두 언어):

- [`census`](plugins/census) — [English](plugins/census/README.md) · [한국어](plugins/census/README-ko.md)
- [`shell-portability`](plugins/shell-portability) — [English](plugins/shell-portability/README.md) · [한국어](plugins/shell-portability/README-ko.md)
- [`session-continuity`](plugins/session-continuity) — [English](plugins/session-continuity/README.md) · [한국어](plugins/session-continuity/README-ko.md)
- [`release-guards`](plugins/release-guards) — [English](plugins/release-guards/README.md) · [한국어](plugins/release-guards/README-ko.md)
- [`sensitive-guard`](plugins/sensitive-guard) — [English](plugins/sensitive-guard/README.md) · [한국어](plugins/sensitive-guard/README-ko.md)

<br/>

## 왜 만들었나

여기 있는 플러그인은 모두, 어시스턴트가 빠르게 작업하는 동안 **조용히** 어긋나는 것을 다룹니다. 작업을 멈추게 하는 실패가 아닙니다 — 그런 건 스스로 드러납니다. 모든 검사를 통과하면서도 틀린 쪽입니다.

- 설정이 갈라집니다. 같은 에이전트가 서로 다른 내용으로 두 벌 존재하는데, 어느 쪽이 이겼는지 아무도 알려주지 않습니다. — [`census`](plugins/census)
- 스크립트가 다른 셸에서 돕니다. `shellcheck`은 통과시켰습니다. shebang이 선언한 셸을 검사했지 실제로 실행된 셸을 검사하지 않았으니까요. — [`shell-portability`](plugins/shell-portability)
- 컨텍스트 창이 압축됩니다. 앞선 대화가 요약되면서 반쯤 하다 만 편집과 어떤 결정을 내린 이유가 흐려집니다. — [`session-continuity`](plugins/session-continuity)
- 빌드가 초록불이라 다음 순서처럼 보여서 태그가 밀립니다. — [`release-guards`](plugins/release-guards)
- 아무도 다시 읽지 않은 diff에 비밀값이 묻어갑니다. — [`sensitive-guard`](plugins/sensitive-guard)

각각은 실수를 값싸게 잡을 수 있는 지점에 놓인 게이트나 보고서이지, 일이 벌어진 뒤에 오는 요약이 아닙니다. 더 나은 도구가 이미 있는 영역 — 비밀값 탐지의 `gitleaks`, 셸 린트의 `shellcheck` — 에서는 그것들을 대체하지 않습니다. 그것들이 돌지 않는 곳에서 돌 뿐입니다. 세션 안, 커밋이 반환되기 전에요.

여기 있는 무엇도 묻지 않고 작업을 고치지 않습니다. `census`는 계약상 읽기 전용이고, 가드들은 막는 대신 묻고, 모든 훅은 열린 상태로 실패합니다. 오작동할 때 작업 흐름을 막는 가드는 꺼지게 되고, 꺼진 가드는 아무것도 지키지 못하기 때문입니다.

<br/>

## 저장소 구조

```
.claude-plugin/marketplace.json   /plugin marketplace add 가 읽는 카탈로그
plugins/<name>/                   플러그인당 디렉터리 하나
  .claude-plugin/plugin.json      플러그인 매니페스트 (이름, 버전)
  skills/<skill>/SKILL.md         스킬, /<plugin>:<skill> 로 호출
  agents/<agent>.md               서브에이전트, description 으로 라우팅
  commands/<command>.md           슬래시 커맨드, /<plugin>:<command> 로 호출
  hooks/hooks.json                훅 등록, ${CLAUDE_PLUGIN_ROOT} 기준 경로
  scripts/                        번들 실행 파일, ${CLAUDE_PLUGIN_ROOT} 로 참조
```

여기 모든 플러그인은 **두 곳**에 버전이 적히고 그 둘은 일치해야 합니다 — 자신의 `plugin.json`과 `marketplace.json`의 항목입니다. 사용자가 실제로 받는 버전은 마켓플레이스 항목 쪽이므로, 둘이 어긋나면 CI가 빌드를 실패시킵니다.

플러그인은 **독립적으로** 버전을 매기고 릴리스도 그렇습니다. 태그는 `<plugin>-v<X.Y.Z>` 형태입니다 — `census-v0.3.1`, `shell-portability-v0.1.0`. 하나를 밀면 그 플러그인만 릴리스되고, 노트는 자기 디렉터리 커밋을 자기 이전 태그 이후로 모아 만듭니다. 저장소 단위 태그를 쓰면 하나가 나갈 때마다 모든 플러그인의 버전이 딸려 올라갑니다. 은퇴한 저장소 단위 형식(`v0.3.0` 이하)의 태그는 이력에 그대로 남아 있습니다.

<br/>

## 개발

```bash
claude --plugin-dir ./plugins/census    # 설치하지 않고 플러그인 로드
claude plugin validate .                # 마켓플레이스 검증
claude plugin validate ./plugins/census # 플러그인 하나 검증
```

세션 안에서 `/reload-plugins` 를 실행하면 재시작 없이 편집분이 반영됩니다.

<br/>

## 라이선스

MIT — [LICENSE](LICENSE)를 참고하세요.
