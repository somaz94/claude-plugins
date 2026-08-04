# shell-portability

셸 스크립트를 딱 한 가지 기준으로 검토합니다. **bash와 zsh 양쪽에서 올바로 도는가.**

> 이 문서는 [README.md](README.md)의 한국어판입니다. 내용이 어긋날 경우 영문판이 기준입니다.

```bash
/plugin marketplace add somaz94/claude-plugins
/plugin install shell-portability@somaz94
```

<br/>

## 왜 필요한가

`shellcheck`은 훌륭한 도구이고 계속 쓰셔야 합니다. 다만 `shellcheck`은 스크립트를 **shebang이 선언한 셸** 기준으로 검사합니다. 정작 보지 않는 건 그 스크립트가 **실제로 실행될 셸**입니다.

버그는 그 틈에서 삽니다. macOS에서 대화형 셸은 zsh인데 거의 모든 스크립트는 `#!/usr/bin/env bash`를 달고 있고, `zsh ./script.sh`는 **shebang을 통째로 무시합니다**. 그러면 스크립트는 전혀 다른 셸과 마주하게 됩니다. 배열은 1부터 시작하고, 따옴표 없는 변수는 단어 분리되지 않으며, 매칭되지 않는 glob은 문자열이 아니라 치명적 오류인 셸입니다. 이 차이들은 터지기 전까지 조용합니다.

<br/>

## 무엇을 잡는가

| 차이 | bash에서 | zsh에서 |
|---|---|---|
| `${BASH_SOURCE[0]}` | 스크립트 경로 | 미설정 — `set -u`에서 치명적 |
| `arr=(a b c); ${arr[0]}` | `a` | 빈 값 — zsh 배열은 1부터 |
| 매칭 없는 `for x in v*/` | 문자열 `v*/` | 치명적 `no matches found` |
| `for x in $LIST` | 공백으로 분리 | 분리 없이 통째로 하나 |
| `echo -e "a\nb"` | `\n`을 해석 | `-e`를 그대로 출력 |
| `declare -g` | 동작 | 없는 문법 — `typeset -g` |

같은 패스에서 일반적인 품질 문제도 함께 짚습니다 — `set -euo pipefail` 누락, 테스트 문맥의 따옴표 없는 확장, 정리 trap 없는 `mktemp`, `$VAR`가 비어 있을 수 있는데 그대로 쓴 `rm -rf "$VAR"`, 그리고 `declare -A`와 `${var,,}`를 위험하게 만드는 macOS bash 3.2 대 bash 5 차이입니다.

<br/>

## 반복해서 권하게 되는 수정

지적 대부분은 스크립트 맨 위에 두 줄짜리 가드 하나만 넣으면 정리됩니다.

```bash
#!/usr/bin/env bash
if [ -n "${ZSH_VERSION:-}" ]; then
  exec bash "$0" "$@"
fi
set -euo pipefail
```

이게 있으면 아래쪽의 bash 전용 문법은 구조적으로 안전해지고, 검토에서도 더 이상 지적하지 않습니다. 가드를 넣을 수 없는 스크립트에는 이식 가능한 대체 문법을 제시합니다 — `${BASH_SOURCE[0]:-$0}`, `IFS=' ' read -r -a`, `echo -e` 대신 `printf`.

<br/>

## 사용법

에이전트는 요청하면 실행되고, 셸 스크립트를 커밋하기 전에 먼저 나서기도 합니다.

```
셸 스크립트 이식성 검토해줘
```

지적은 파일별로 묶여 🔴 / 🟡 / 🟢으로 나뉩니다. 블록을 통째로 다시 쓰는 대신 `file:line`을 인용해 최소 패치만 보여줍니다.

```
Shell portability review
========================
2 files scanned, 1 with issues.

── scripts/foo.sh ──
🔴 Critical
  foo.sh:7  — `${BASH_SOURCE[0]}` fails under zsh `set -u`. Add the re-exec
              guard at line 4:
              if [ -n "${ZSH_VERSION:-}" ]; then exec bash "$0" "$@"; fi
🟡 Warning
  foo.sh:23 — `echo -e` not portable; use `printf '%s\n' "$x"`.

Recommended verification:
  bash -n scripts/foo.sh && zsh --no-rcs -n scripts/foo.sh
```

<br/>

## 범위

대상: `*.sh` / `*.bash` / `*.zsh`, shebang이 셸을 가리키는 모든 파일, CI YAML 안의 인라인 셸(`run:` / `script:` 블록), git 및 husky 훅 스크립트.

대상 아님: PowerShell, fish, `.bat` / `.cmd`, 그리고 POSIX sh로 다시 쓰는 일. 불변 조건은 bash + zsh이지 bash + sh + dash + ash가 아닙니다.

<br/>

## 절대 하지 않는 것

- 검토 대상 스크립트를 실행하는 일. 명백히 읽기 전용인 호출(`--help`, `--dry-run`)만 예외입니다.
- 스크립트를 말없이 편집하는 일. 간단하지 않은 수정은 승인받을 수 있도록 보고만 합니다.
- `bash` shebang을 `sh`나 `zsh`로 바꾸는 일.
- `shellcheck`이 하는 일을 되풀이하는 일. 함께 쓸 도구로 언급하되, `shellcheck`이 다루지 않는 이식성 축에 집중합니다.

<br/>

## 릴리스

이 마켓플레이스의 플러그인은 각자 독립적으로 버전을 매기고 릴리스합니다. `shell-portability`의 모든 변경 이력은 이 디렉터리 커밋만 담아 [shell-portability 릴리스](https://github.com/somaz94/claude-plugins/releases?q=shell-portability&expanded=true)에 있습니다.

<br/>

## 라이선스

MIT — [LICENSE](../../LICENSE)를 참고하세요.
