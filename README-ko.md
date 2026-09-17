# claude-plugins

[Claude Code](https://code.claude.com/docs) 플러그인 마켓플레이스입니다.

> 이 문서는 [README.md](README.md)의 한국어판입니다. 내용이 어긋날 경우 영문판이 기준입니다.

Claude Code 세션 안에서:

```
/plugin marketplace add somaz94/claude-plugins
/plugin install census@somaz94
```

대화형 세션에 들어가지 않고 셸에서 바로 설치하려면:

```bash
claude plugin marketplace add somaz94/claude-plugins
claude plugin install census@somaz94
```

<br/>

## 플러그인

| 플러그인 | 하는 일 |
|---|---|
| [`census`](plugins/census) | 흩어진 `.claude/` 설정을 읽기 전용으로 감사 — 가진 것을 목록화하고, 어긋난 곳을 찾고, 팀에 공유할 만큼 이식 가능한 항목을 가려냅니다 |
| [`atlas`](plugins/atlas) | 프로젝트가 실제로 닿을 수 있는 모든 것을 HTML 지도로 시각화 — 사용자 설정과 이 저장소, 설치된 모든 플러그인에 걸친 커맨드·에이전트·스킬·훅·MCP·메모리를 한 화면에서 훑습니다 |
| [`doc-mirror`](plugins/doc-mirror) | 번역된 문서 쌍이 어긋나지 않게 지킵니다 — 미러가 아예 안 만들어진 README, 원본이 먼저 나아가 버린 미러, 한쪽만 섹션을 잃은 쌍을 찾아냅니다 |
| [`shell-portability`](plugins/shell-portability) | 셸 스크립트의 bash/zsh 이식성 검토 — `shellcheck`이 다루지 않는 영역입니다. `shellcheck`은 shebang이 선언한 셸을 검사할 뿐, 그 스크립트가 실제로 실행될 셸은 보지 않으니까요 |
| [`session-continuity`](plugins/session-continuity) | 컨텍스트가 리셋돼도 장기 작업 이어 가기 — 진행하면서 plan 파일을 갱신하고, 새 세션의 출발점이 될 핸드오프 프롬프트를 만듭니다 |
| [`release-guards`](plugins/release-guards) | 되돌릴 수 없는 릴리스 동작 앞에 확인 절차를 세웁니다 — 태그 생성·삭제와 릴리스 발행, 그리고 그 둘을 만들어 내는 자동화 파일 편집이 대상입니다 |
| [`sensitive-guard`](plugins/sensitive-guard) | 비밀값이 커밋되는 순간 차단 — 커밋이 추가하는 줄만 훑는 마지막 관문, 그리고 공개 전에 요청해서 돌리는 전체 스캔을 제공합니다 |
| [`korean-prose`](plugins/korean-prose) | 맞춤법 검사기가 볼 수 없는 어색함을 검토 — 한국어 문장 안에 남은 영어 골격, 생성된 텍스트의 흔적, 그리고 한 줄을 이웃 줄과 대조해야만 드러나는 문제를 잡습니다 |
| [`doc-tidy`](plugins/doc-tidy) | 문서마다 남겨야 하는지, 어디에 둬야 하는지 판정 — 끝난 기록은 보관하고, 커밋된 세션 부산물은 지우고, 이동이 깨뜨리는 링크는 모두 다시 씁니다. 승인한 묶음만 적용합니다 |

플러그인별 문서 — 영어와 한국어 두 벌:

- [`census`](plugins/census) — [English](plugins/census/README.md) · [한국어](plugins/census/README-ko.md)
- [`atlas`](plugins/atlas) — [English](plugins/atlas/README.md) · [한국어](plugins/atlas/README-ko.md)
- [`doc-mirror`](plugins/doc-mirror) — [English](plugins/doc-mirror/README.md) · [한국어](plugins/doc-mirror/README-ko.md)
- [`shell-portability`](plugins/shell-portability) — [English](plugins/shell-portability/README.md) · [한국어](plugins/shell-portability/README-ko.md)
- [`session-continuity`](plugins/session-continuity) — [English](plugins/session-continuity/README.md) · [한국어](plugins/session-continuity/README-ko.md)
- [`release-guards`](plugins/release-guards) — [English](plugins/release-guards/README.md) · [한국어](plugins/release-guards/README-ko.md)
- [`sensitive-guard`](plugins/sensitive-guard) — [English](plugins/sensitive-guard/README.md) · [한국어](plugins/sensitive-guard/README-ko.md)
- [`korean-prose`](plugins/korean-prose) — [English](plugins/korean-prose/README.md) · [한국어](plugins/korean-prose/README-ko.md)
- [`doc-tidy`](plugins/doc-tidy) — [English](plugins/doc-tidy/README.md) · [한국어](plugins/doc-tidy/README-ko.md)

<br/>

## 왜 만들었나

여기 있는 플러그인은 하나같이 어시스턴트가 빠르게 작업하는 동안 **조용히** 어긋나는 문제를 다룹니다. 작업을 멈춰 세우는 실패가 아닙니다 — 그런 건 알아서 드러납니다. 모든 검사를 통과하고도 틀려 있는 쪽입니다.

- 설정이 갈라집니다. 같은 에이전트가 서로 다른 내용으로 두 벌 존재하는데, 어느 쪽이 이겼는지는 어디서도 알려주지 않습니다. — [`census`](plugins/census)
- 플러그인을 설치해도 그게 뭘 추가했는지 볼 방법이 없습니다. 세션은 네 개 레이어를 한꺼번에 해석하는데 그걸 한자리에 보여주는 건 없고, 그래서 스크립트가 지워진 훅이 등록된 채로 남아 설정된 것처럼 보이면서 아무 일도 하지 않습니다. — [`atlas`](plugins/atlas)
- README 한쪽만 고치고 번역본은 그대로 둡니다. 빌드는 초록불이고 린터는 조용하고 리뷰어 눈에는 파일 하나만 들어오는데, 다른 언어 쪽은 조용히 지난 분기 지침이 되어 갑니다. — [`doc-mirror`](plugins/doc-mirror)
- 스크립트가 다른 셸에서 실행됩니다. `shellcheck`은 통과시켰습니다. shebang이 선언한 셸을 검사했지 실제로 실행된 셸을 검사하지 않았으니까요. — [`shell-portability`](plugins/shell-portability)
- 컨텍스트 창이 압축됩니다. 앞선 대화가 요약되면서 반쯤 하다 만 편집과 어떤 결정을 내린 이유가 흐려집니다. — [`session-continuity`](plugins/session-continuity)
- 빌드가 초록불이면 태그를 다는 게 다음 순서처럼 보여서, 태그가 그냥 푸시됩니다. — [`release-guards`](plugins/release-guards)
- 아무도 다시 읽지 않은 diff에 비밀값이 묻어갑니다. — [`sensitive-guard`](plugins/sensitive-guard)
- 번역된 문서가 영어 골격을 그대로 안고 갑니다. 철자도 맞고 문법도 온전해서 아무것도 이걸 잡아내지 못하는데, 읽는 문서가 아니라 해독하는 문서가 됩니다. — [`korean-prose`](plugins/korean-prose)
- 문서는 작업이 일어난 자리에 쌓입니다. 핸드오프가 커밋되고, 끝난 마이그레이션의 plan이 현재 문서 옆에 남고, 지난달 만료된 plan 파일을 가리키는 문장이 여전히 참조처럼 읽힙니다. — [`doc-tidy`](plugins/doc-tidy)

하나같이 실수를 값싸게 잡을 수 있는 지점에 놓인 게이트나 보고서이지, 일이 벌어진 뒤에 오는 요약이 아닙니다. 더 나은 도구가 이미 있는 영역에서는 — 비밀값 탐지에는 `gitleaks`, 셸 린트에는 `shellcheck` — 그 도구들을 대체하지 않습니다. 그 도구들이 닿지 않는 곳, 즉 세션 안에서 커밋 명령이 끝나기 전에 돌 뿐입니다.

여기 있는 어떤 것도 묻지 않고 사용자의 작업을 고치지 않습니다. `census`는 계약상 읽기 전용이고, 가드는 막는 대신 물으며, 모든 훅은 자체 오류가 나도 막지 않고 통과시킵니다. 오작동할 때 작업 흐름을 막는 가드는 결국 사용자가 꺼 버리고, 꺼진 가드는 아무것도 지키지 못하기 때문입니다.

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
    _shared.py                    각 플러그인에 동일하게 벤더링된 공용 헬퍼
  <data>/                         번들 스크립트가 자기 __file__ 기준으로 찾는 데이터
```

`_shared.py`는 **import가 아니라 벤더링**입니다. 플러그인은 각자 따로 설치되므로 런타임에는 자기 디렉터리 밖의 것이 디스크에 없고, import 해 올 공용 패키지도 존재하지 않습니다. 그래서 모든 사본은 바이트 단위로 동일해야 하며, 어긋나면 CI가 실패합니다.

이 검사는 형식적인 절차가 아닙니다. 검사가 없던 동안 이 헬퍼들은 각각 손으로 두 번씩 쓰여 있었고, 사본끼리 이미 어긋나 있었습니다. 한쪽 frontmatter 파서는 `''` 이스케이프를 풀었지만 다른 쪽은 그대로 텍스트로 보여 줬고, 한쪽 문서 구조 계산기는 펜스 코드블록 안의 `#`을 제목으로 셌지만 다른 쪽은 세지 않았습니다. 설정 드리프트를 잡으라고 만든 도구 두 개가 정작 서로 드리프트해 있던 셈입니다.

수정은 정본인 `plugins/census/scripts/_shared.py`에 하고, `bash tests/sync-shared.sh`로 나머지에 전파하세요.

여기 모든 플러그인은 **두 곳**에 버전이 적히고 그 둘은 일치해야 합니다 — 자신의 `plugin.json`과 `marketplace.json`의 항목입니다. 사용자가 실제로 받는 버전은 마켓플레이스 항목 쪽이므로, 둘이 어긋나면 CI가 빌드를 실패시킵니다.

플러그인은 버전과 릴리스를 **각자 독립적으로** 관리합니다. 태그는 `<plugin>-v<X.Y.Z>` 형태입니다 — `census-v0.3.1`, `shell-portability-v0.1.0`. 태그 하나를 푸시하면 그 플러그인만 릴리스되고, 릴리스 노트는 그 플러그인 디렉터리 아래 커밋 중 직전 태그 이후 것만 모아 만듭니다. 저장소 단위 태그를 쓰면 하나가 나갈 때마다 모든 플러그인의 버전이 딸려 올라갑니다. 지금은 쓰지 않는 저장소 단위 형식(`v0.3.0` 이하)의 태그는 이력에 그대로 남아 있습니다.

<br/>

## 개발

```bash
claude --plugin-dir ./plugins/census    # 설치하지 않고 플러그인 로드
claude plugin validate .                # 마켓플레이스 검증
claude plugin validate ./plugins/census # 플러그인 하나 검증
```

세션 안에서 `/reload-plugins`를 실행하면 재시작 없이 편집분이 반영됩니다.

```bash
bash tests/run.sh              # 푸시 전에, CI가 돌리는 모든 검사
bash tests/release-status.sh   # 태그 없이 배포 중인 플러그인 확인
```

`release-status`가 있는 이유는 태그를 잊어도 아무 소리가 나지 않기 때문입니다. 사용자가 받는 버전은 `marketplace.json`에 적힌 값이므로, main에 올라간 bump는 이미 배포된 상태이고 태그는 이력만 담습니다. 그래서 그 플러그인을 다음에 릴리스할 때가 되어서야 문제가 드러납니다. git-cliff가 그 플러그인의 직전 태그부터 범위를 잡으니, 노트가 두 구간을 한꺼번에 담게 됩니다. 이 검사는 빌드를 실패시키지 않습니다 — bump 커밋이 태그보다 먼저 main에 도달하는 건 정상적인 순서이기 때문입니다.

<br/>

## 라이선스

MIT — [LICENSE](LICENSE)를 참고하세요.
