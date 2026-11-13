# commit-gates

승인이 있어야 할 자리에 승인을 두고 커밋합니다.

> 이 문서는 [README.md](README.md)의 한국어판입니다. 내용이 어긋날 경우 영문판이 기준입니다.

Claude Code 세션 안에서:

```
/plugin marketplace add somaz94/claude-plugins
/plugin install commit-gates@somaz94
```

대화형 세션에 들어가지 않고 셸에서 바로 설치하려면:

```bash
claude plugin marketplace add somaz94/claude-plugins
claude plugin install commit-gates@somaz94
```

<br/>

## 왜 필요한가

커밋은 push나 릴리스에 비하면 무해해 보입니다. 그래서 권한이 가장 먼저 새는 곳도 커밋입니다. 이 플러그인이 답하는 실패는 셸로 `git`을 다루던 sub-agent에서 시작됐습니다. 사용자와 대화하던 세션이 사용자의 "예"를 그 agent들에게 넘겼고, agent들은 그 전달된 승인을 근거로 stage하고, 커밋하고, push했습니다. 사용자도 아마 승인할 생각이었으므로 해로운 일은 없었습니다. 잘못된 것은 권한이 놓인 자리였습니다. 명령을 실행한 agent는 사용자에게서 직접 "예"를 들은 적이 없고, 그 "예"가 어디까지를 허락한 것인지도 확인할 수 없었습니다.

sub-agent는 사용자에게 아무것도 물을 수 없습니다. 그래서 sub-agent가 받는 승인은 언제나 다른 agent가 전달한 것이고, 전달된 승인은 사용자의 승인이 아닙니다. 그러므로 해법은 sub-agent의 프롬프트를 더 엄격하게 만드는 것이 아닙니다. 실행하는 자리를 옮기는 것입니다. 커밋을 계획하는 agent는 실행하지 않고, 사용자와 대화하는 세션이 사용자에게 물은 뒤에만 실행합니다.

<br/>

## 두 부분

| 구성 | 역할 | 할 수 있는 일 |
|---|---|---|
| `git-commit-runner` agent | 계획 | diff, 이력, 작성자를 읽고 메시지, 파일 목록, 게이트 두 개를 텍스트로 돌려줍니다. 파일 편집 도구가 없고, `git add`나 `git commit`을 실행하지 않습니다. |
| `/commit-gates:commit` | 묻는 세션 | 게이트마다 보여 주고 따로 묻고, 승인된 명령을 직접 실행합니다. 승인된 명령을 agent에게 되넘기지 않습니다. |

stage와 커밋은 서로 다른 게이트입니다. 승인 하나가 둘을 함께 덮지 않습니다. "그래, 커밋해"와 "그래, 이 파일들이 맞아"는 다른 질문이고, 끼어든 `.env`는 두 번째 질문에서 잡힙니다.

<br/>

## 계획 agent가 확인하는 것

- **Conventional Commits 타입 하나.** 가장 큰 비중의 변경을 기준으로 고르고, 섞인 diff에는 억지 라벨 대신 분할을 제안합니다.
- **저장소 관례가 먼저.** `CLAUDE.md`, `AGENTS.md`, `CONTRIBUTING*`, 기존 이력이 메시지의 언어, 본문 허용 여부, `Signed-off-by`나 `Co-Authored-By` 같은 trailer를 정합니다. 아무 근거가 없으면 기본값은 한 줄 제목입니다.
- **명시적인 파일 목록.** "전부"라고 말하지 않으면 `git add -A`를 쓰지 않습니다. 비밀 값이나 빌드 산출물처럼 보이는 파일은 빼고 표시합니다.
- **릴리스 자동화 파일은 빼 둠.** `release.yml`, `cliff.toml`, `RELEASE.md` 같은 파일은 표시만 하고 몰래 stage하지 않습니다.
- **커밋 작성자.** 작성자가 비어 있거나 봇처럼 보이면 사이클을 멈춥니다. 이 플러그인의 어느 부분도 `git config`를 실행하거나 `--author`로 덮어쓰지 않습니다. 계획할 수 있는 `--author`는 push하지 않은 커밋의 amend 하나뿐이며, 기존 작성자를 그대로 복사합니다.

<br/>

## 인자

```
/commit-gates:commit                 진단, 초안, 그다음 게이트 두 개
/commit-gates:commit recommend-only  메시지와 diff stat만
/commit-gates:commit split           커밋 2~4개 제안, 커밋마다 게이트가 있는 사이클
/commit-gates:commit auth            scope 힌트
```

저장소에 "직접 커밋하지 말라"는 규칙이 있으면 command가 스스로 `recommend-only`로 바뀝니다. [`sensitive-guard`](../sensitive-guard) 플러그인이 설치돼 있으면 stage 전에 그 스캔을 권합니다.

<br/>

## 절대 하지 않는 것

- 게이트마다 묻지 않고 `git add`나 `git commit`을 실행하는 일.
- 프롬프트에 사용자가 승인했다고 적혀 있어도, 계획 agent가 상태를 바꾸는 명령을 실행하게 두는 일.
- push, 태그, pull request 열기, 릴리스 게시. 다음 단계로 알려 주고 사용자에게 맡깁니다.
- git 설정을 바꾸거나 커밋 작성자를 덮어쓰는 일.
- 요청하지 않았는데 `--no-verify`나 `--no-gpg-sign`을 쓰는 일. hook이 실패하면 사이클은 새 계획으로 돌아갑니다.

<br/>

## 릴리스

이 마켓플레이스의 플러그인은 각자 독립적으로 버전을 매기고 릴리스합니다. `commit-gates`의 모든 변경 이력은 이 디렉터리의 커밋만 모은 [commit-gates 릴리스](https://github.com/somaz94/claude-plugins/releases?q=commit-gates&expanded=true)에서 볼 수 있습니다.

<br/>

## 라이선스

MIT — [LICENSE](../../LICENSE)를 참고하세요.
