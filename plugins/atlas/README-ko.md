# atlas

프로젝트가 닿을 수 있는 Claude Code 리소스 전체를 브라우저에서 훑어보는 지도입니다.

> 이 문서는 [README.md](README.md)의 한국어판입니다. 내용이 어긋날 경우 영문판이 기준입니다.

세션은 네 개 레이어를 한꺼번에 해석합니다 — 사용자 설정, 이 저장소의 `.claude/`, 설치된 모든 플러그인, 그리고 그 사이 설정 파일들이 등록한 훅. 그런데 네 개를 한자리에 놓고 보여주는 건 어디에도 없습니다. `/help`는 커맨드를 나열할 뿐 출처를 말해 주지 않고, `claude plugin details`는 플러그인 하나만 다루며, `/plugin install` 직후 *방금 그게 여기에 뭘 추가했는지*에는 아예 답이 없습니다.

```bash
/plugin marketplace add somaz94/claude-plugins
/plugin install atlas@somaz94
```

설치한 뒤 아무 세션에서나:

```
/atlas:view
```

<br/>

## 무엇이 나오나

자체 완결된 HTML 파일 하나가 브라우저에서 열립니다. 서버도, CDN도, 빌드 단계도 없습니다 — CSS와 JS가 전부 인라인이라 오프라인에서 열리고, 동료에게는 첨부파일 하나로 그냥 건네면 됩니다.

```
Project: ~/code/acme-platform
Resources: 41 (12 commands, 18 agents, 5 skills, 4 hooks, 1 MCP servers, 1 memory files)
By layer: 9 plugin, 11 project, 21 user
Always-on context: ~9,120 tokens (36,481 chars, estimate)
Name conflicts: 1
  - shell-reviewer (agent) in ~/.claude, acme-platform — the project-level definition wins
Hooks pointing at a missing script: 1
Items with no description: 1
Viewer: /tmp/claude-atlas-acme-platform.html
```

페이지는 리소스를 종류별로 묶어 보여주고, 각 행을 펼치면 그 파일의 원본이 그대로 나옵니다 — 요약이 아니라 Claude Code가 실제로 읽는 Markdown입니다.

- **검색** — 이름, 설명, 경로, 본문을 한 번에 훑습니다.
- **필터** — 종류별(커맨드·에이전트·스킬·훅·MCP·메모리·플러그인), 레이어별(user·project·plugin).
- **needs attention** — 토글 하나로 가려진 이름, 죽은 훅, 설명 없는 항목만 남깁니다.
- 모든 행이 자기 레이어와 공급한 플러그인, 그리고 출처 경로를 함께 답니다.

라이트/다크 모두 지원하며 브라우저 설정을 따릅니다.

<br/>

## 네 개 레이어

| 레이어 | 읽는 대상 |
|---|---|
| user | `~/.claude/` (또는 `$CLAUDE_CONFIG_DIR`) — `agents/`, `commands/`, `skills/`, `CLAUDE.md`, 그리고 `settings.json` / `settings.local.json`의 훅 |
| project | 이 저장소의 `.claude/` — 같은 구조에 저장소 루트의 `CLAUDE.md`와 `.mcp.json`이 더해집니다 |
| plugin | `~/.claude/plugins/installed_plugins.json`에 등록된 모든 플러그인을 실제 설치 경로에서 읽고, `hooks/hooks.json`까지 포함합니다 |
| — | 여기서 어떤 플러그인이 켜져 있는지는 user와 project 설정을 합쳐서 판단하며, 충돌하면 project가 이깁니다 |

하위 디렉터리에 있는 커맨드는 실제로 입력하는 이름 그대로 표시합니다 — `commands/git/ship.md`는 `/git:ship`입니다. 플러그인의 커맨드와 스킬은 플러그인 네임스페이스를 달고 나옵니다(`/atlas:view`). 이 구분은 겉치레가 아닙니다. 플러그인 커맨드가 내 커맨드와 절대 충돌할 수 없는 이유, 그리고 에이전트는 충돌할 수 있는 이유가 정확히 여기에 있습니다.

<br/>

## 짚어 주는 세 가지

페이지의 대부분은 목록입니다. 아래 셋은 지적입니다.

**한 이름이 두 개 이상의 정의로 풀리는 경우.** 에이전트는 세 레이어를 통틀어 평평한 네임스페이스 하나를 씁니다. `shell-reviewer`를 사용자 레벨에 두고 저장소에도 두면 그 저장소 안에서는 project 쪽이 이깁니다 — 세션을 어디서 시작했느냐에 따라 같은 이름이 다르게 동작하는데, 어느 쪽이 답했는지는 아무도 알려주지 않습니다. 플러그인이 제공하는 커맨드와 스킬은 네임스페이스가 붙어 충돌할 수 없으므로 여기서 다루지 않습니다.

우선순위가 문서화된 경우에는 이긴 쪽을 지목합니다. 그렇지 않은 경우 — 플러그인 에이전트가 내 에이전트와 이름이 겹칠 때 — 는 추측하는 대신 *ambiguous*라고 적습니다. 틀린 답을 확신에 차서 내놓는 쪽이 더 나쁘니까요.

**존재하지 않는 스크립트를 가리키는 훅.** 훅 등록은 포인터입니다. 스크립트를 지우거나 이름을 바꿔도 `settings.json`의 등록은 멀쩡해 보이는 채로 남습니다 — 로드되고, 한 번도 발동하지 않고, 아무도 말해 주지 않습니다. 각 훅 행은 런타임과 같은 방식으로 `${CLAUDE_PLUGIN_ROOT}`와 `$HOME`을 펼쳐 대상을 확인하고, 파일이 실제로 있는지 표시합니다.

인라인 훅은 사라진 스크립트가 아닙니다. `jq -r .tool_name`에는 찾을 파일 자체가 없으므로 결함이 아니라 있는 그대로 보고합니다.

**설명이 없는 항목.** 설명은 Claude가 언제 그것을 꺼내 쓸지 판단하는 유일한 신호입니다. 설명이 없으면 설치돼 있고, 목록에도 뜨지만, 이름을 직접 대지 않는 한 닿을 수 없습니다.

<br/>

## 항상 올라가 있는 컨텍스트

모든 커맨드·에이전트·스킬의 `name`과 `description`은 *매* 세션의 시스템 프롬프트에 상주합니다. 본문은 실제로 호출될 때만 로드됩니다. 범위 안의 `CLAUDE.md`는 통째로 상주합니다. 그 합이 아무것도 입력하지 않은 시점에 이미 지불하는 비용이고, 프로젝트 단위로 보고합니다 — 실제로 지불하는 형태가 그것뿐이니까요.

토큰 4자 기준의 추정치이며, 추정치라고 명시합니다. 5k 토큰 아래면 특별히 언급할 것이 없습니다. 25k를 넘어가면 작업을 시작하기도 전에 매 세션의 상당 부분이 소모되고 있다는 뜻이고, 대개는 일 년 넘게 자라난 `CLAUDE.md`가 그 대부분을 차지합니다.

> 지금 서 있는 프로젝트 하나가 아니라 *모든* root를 통틀어 같은 수치를 보고 싶다면, 그리고 drift 검출과 이식성 등급까지 원한다면 [`census`](../census)를 보세요. `atlas`는 "이 프로젝트가 무엇에 닿을 수 있고 그 화면이 어떻게 생겼나"에 답하고, `census`는 "내가 여기저기 쌓아 둔 게 무엇이고 어디서 서로 어긋나 있나"에 답합니다.

<br/>

## 스크립트 직접 실행하기

스킬은 번들 스크립트 하나를 감쌉니다. python3 **표준 라이브러리만** 쓰며 설치 단계가 없습니다.

```bash
python3 scripts/atlas.py view --open              # 만들고 브라우저로 열기
python3 scripts/atlas.py view --out setup.html    # 원하는 위치에 쓰기
python3 scripts/atlas.py view --no-bodies         # 목록만, 파일 크기가 훨씬 작아집니다
python3 scripts/atlas.py --project ~/code/api view
python3 scripts/atlas.py scan                     # 같은 그래프를 JSON으로
```

| 플래그 | 기본값 | 의미 |
|---|---|---|
| `--project DIR` | 현재 디렉터리 | 지도로 만들 저장소 |
| `--user-root DIR` | `$CLAUDE_CONFIG_DIR` 또는 `~/.claude` | 사용자 설정 디렉터리 |
| `--plugins-root DIR` | `<user root>/plugins` | 플러그인 레지스트리 위치 |
| `--out FILE` | `<tmp>/claude-atlas-<project>.html` | 출력 경로 |
| `--open` | 꺼짐 | 생성 후 브라우저로 열기 |
| `--no-bodies` | 꺼짐 | 파일 본문 제외 |
| `--max-body N` | 20000 | 항목당 본문 글자 수 상한 |

역할을 나눈 건 의도적입니다. 스크립트는 훑고 그리며, 스킬은 해석합니다. 백 개 남짓한 파일을 읽는 일은 모델이 할 일이 아닙니다 — 느리고 비싸고 매번 결과가 달라집니다. 그 결과가 무엇을 뜻하는지 말하는 쪽이 모델의 일입니다.

<br/>

## 파일이 놓이는 곳

기본 출력 위치는 저장소가 아니라 임시 디렉터리입니다. 작업 트리에 떨어진 뷰어는 `git add .` 한 번이면 커밋되고, 그 안에는 생성한 기계의 실제 경로가 들어 있습니다.

다른 곳에 두려면 `--out`을 쓰면 됩니다. 저장소 안에 일부러 두는 경우라면 `.gitignore`에 넣으세요.

훑은 디렉터리 안에 쓰는 것은 아예 거부합니다. 설정을 보고하는 도구가 그 보고 대상의 일부가 되어서는 안 되니까요.

<br/>

## 하지 않는 일

- 찾아낸 것을 편집·이동·삭제하지 않습니다.
- 훑은 디렉터리 안에 쓰지 않습니다.
- 네트워크 요청을 하지 않습니다. 뷰어에는 외부 참조가 한 줄도 없습니다.
- MCP 비밀값을 페이지에 담지 않습니다. `env` 블록은 **변수 이름만** 보고하고 값은 절대 싣지 않습니다.

<br/>

## 릴리스

이 마켓플레이스의 플러그인은 각자 버전을 매기고 따로 릴리스합니다. `atlas`의 변경 이력은 — 커밋이 이 디렉터리로 한정된 채 — [atlas releases](https://github.com/somaz94/claude-plugins/releases?q=atlas&expanded=true)에 있습니다.

<br/>

## 라이선스

MIT — [LICENSE](../../LICENSE)를 참고하세요.
