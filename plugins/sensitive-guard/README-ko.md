# sensitive-guard

비밀값을 커밋되는 순간에 막습니다.

> 이 문서는 [README.md](README.md)의 한국어판입니다. 내용이 어긋날 경우 영문판이 기준입니다.

```bash
/plugin marketplace add somaz94/claude-plugins
/plugin install sensitive-guard@somaz94
```

<br/>

## 무엇이고, 무엇이 아닌가

`gitleaks`와 `trufflehog`는 이것보다 나은 스캐너이고, 그것들을 쓰셔야 합니다. 다만 그것들은 CI에서 돕니다 — 커밋이 이미 생긴 뒤, 푸시된 뒤, 때로는 저장소가 공개된 뒤에요.

이 플러그인은 **`git commit`이 반환되기 전에**, 코드를 쓰고 있는 세션 안에서 돕니다. 그게 전부입니다. 탐지 깊이가 아니라 게이트가 놓인 위치가 차별점입니다. 정규식 집합은 일부러 작고 읽을 수 있게 두었습니다. 읽을 수 없는 게이트는 막히는 순간에 신뢰할 수 없기 때문입니다.

여기서 깨끗하게 나왔다고 저장소에 비밀값이 없다는 증명으로 읽으면 안 됩니다. 이건 마지막 한 구간이지 길 전체가 아닙니다.

<br/>

## 저장소 단위로 켭니다

경로로 판단하지 않습니다. 저장소는 루트에 `.sensitive-patterns` 파일을 두어 켭니다.

```
# .sensitive-patterns
internal_marker|acme-corp|acme-internal
personal_email|me@example\.com
real_username_path|/home/rjones\b
```

한 줄에 `name|regex` 하나씩이고, 빈 줄과 `#` 주석은 무시합니다. 빈 파일도 유효합니다 — 보편 카테고리를 켜고 아무것도 추가하지 않습니다.

의도적인 설계입니다. 누군가에게 "공개 미러" 디렉터리인 곳이 다른 사람에겐 평범한 체크아웃입니다. 그래서 스스로 짐작해서 발동하는 가드는 결국 제거당합니다. 게다가 본인만의 마커는 어차피 어딘가에 있어야 하므로, 켜는 행위와 설정하는 행위를 한 파일로 합쳤습니다.

수동 스캔에는 `~/.claude/sensitive-patterns`가 전역 대체 경로로 적용됩니다.

<br/>

## 두 축

| 구성 | 언제 | 무엇을 하는가 |
|---|---|---|
| `pre-commit-sensitive-scan` 훅 | 켜진 저장소에서 `git commit` | **커밋이 추가하는 줄만** 스캔하고 걸리면 차단 |
| `/sensitive-guard:scan` | 요청 시 | 저장소 전체 또는 루트 아래 모든 저장소를 스캔해 판정 보고 |

훅이 트리 전체가 아니라 **diff만** 검사하는 게 실사용의 관건입니다. 그러지 않으면 기존 지적이 있는 저장소는 모든 커밋이 영원히 막히고, 늘 발동하는 게이트는 반사적으로 무시당합니다.

**열린 상태로 실패합니다.** 읽을 수 없는 입력, 없는 스캐너, 해석 불가한 저장소 — 전부 커밋을 허용합니다. 오작동할 때 작업을 막는 가드는 꺼지게 되고, 꺼진 가드는 아무것도 지키지 못합니다.

<br/>

## 기본으로 잡는 것

사설 IP 대역(RFC 5737 문서 대역은 허용), AWS 키, GitLab PAT·러너·OAuth 토큰, GitHub 토큰, Slack 토큰, OIDC client secret, 일반적인 `password=` / `api_key=` 대입, SSH 개인키, 그리고 실제 사용자명이 담긴 홈 경로.

맨 `~/.claude`는 **일부러 잡지 않습니다.** Claude Code가 문서화한 경로이고 어느 머신에서나 동일해서, 그걸 잡으면 그 경로를 정상적으로 문서화한 모든 저장소에서 발동하게 됩니다.

<br/>

## 절대 하지 않는 것

- 파일을 고쳐 값을 치환하는 일. 보고만 하고 판단은 사용자 몫입니다.
- 커밋, 푸시, 배포.
- 네트워크 접근. 단 `--all` 중 비공개 저장소를 건너뛰기 위한 선택적 `gh` 조회는 예외입니다.

<br/>

## 릴리스

이 마켓플레이스의 플러그인은 각자 독립적으로 버전을 매기고 릴리스합니다. `sensitive-guard`의 모든 변경 이력은 이 디렉터리 커밋만 담아 [sensitive-guard 릴리스](https://github.com/somaz94/claude-plugins/releases?q=sensitive-guard&expanded=true)에 있습니다.

<br/>

## 라이선스

MIT — [LICENSE](../../LICENSE)를 참고하세요.
