---
name: scan
description: '공개 전 sanitize 해야 하는 값 — private IP, 클라우드 키, 토큰, SSH 개인키, 그리고 직접 정의한 마커 — 을 저장소에서 스캔한다. push 하거나 공개하기 전, "공개해도 안전한가?" / "secret 스캔해줘" / "push 전에 확인해줘" 라고 할 때, 또는 sanitize 후 수정이 반영됐는지 확인할 때 사용.'
argument-hint: "[path | --all [ROOT]]"
allowed-tools: Bash, Read
---

> 본 문서는 [skills/scan/SKILL.md](../../skills/scan/SKILL.md) 의 **한국어 번역본** 입니다.
> Claude Code 가 실제 로드하는 것은 영어 원본이며, 본 KO 본은 참조 / 사용자 리뷰 용도입니다.
> 수정 시 EN + KO 둘 다 동시 수정해야 합니다.

# scan

번들된 스캐너를 저장소에 돌려 **safe-to-publish** / **hold** 판정을 반환한다. 읽기 전용 — 보고만 하며 절대 수정 · 커밋 · push 하지 않는다.

<br/>

## 실행

```bash
"${CLAUDE_PLUGIN_ROOT}/scripts/find-sensitive.sh" [DIR]            # 저장소 또는 디렉터리 하나
"${CLAUDE_PLUGIN_ROOT}/scripts/find-sensitive.sh" -q [DIR]         # 건수만
"${CLAUDE_PLUGIN_ROOT}/scripts/find-sensitive.sh" --all [ROOT]     # ROOT 하위 전체 디렉터리
"${CLAUDE_PLUGIN_ROOT}/scripts/find-sensitive.sh" -p FILE [DIR]    # FILE 에서 카테고리 추가
```

인자가 없으면 현재 작업 디렉터리를 스캔한다. `--all` 은 basename 에 `-private` 가 들어간 repo 를 건너뛰고, `gh` 를 쓸 수 있으면 GitHub origin 이 private 인 repo 도 건너뛴다 — 오프라인이면 `--no-remote-check` 를 붙인다.

종료 코드 0 은 아무것도 걸리지 않았다는 뜻이고, 1 은 최소 한 카테고리가 걸렸다는 뜻이다.

<br/>

## 보고 방식

판정을 먼저 말하고, 근거를 뒤에 붙인다.

**Clean** — 한 줄로 말하고 끝낸다. 깨끗한 결과를 길게 늘리지 않는다.

**Findings** — 카테고리별로 묶고 파일과 라인을 인용한다. 각 건마다 다음 중 어느 쪽인지 밝힌다:

- **절대 나가면 안 되는 실제 값.** 살아있는 토큰, 키, 내부 호스트. sanitize 대체값을 함께 제시한다 — 주소는 RFC 5737 문서용 대역 (`192.0.2.0/24`, `198.51.100.0/24`, `203.0.113.0/24`), 자격증명은 누가 봐도 가짜인 placeholder.
- **진짜처럼 보일 뿐인 예시.** 테스트 픽스처와 문서에는 `password = hunter2` 형태가 정당하게 들어간다. 수정을 요구하지 말고 그렇다고 분명히 말한다.

대체값은 원본과 같은 클래스로 유지한다 — 공인 주소를 사설 주소로 바꾸면 주소의 라우팅 가능 여부로 분기하는 테스트가 조용히 깨진다.

한 카테고리가 여러 파일에서 한꺼번에 걸리면 보통 누출 여러 건이 아니라 컨벤션 하나다. 건수를 붙여 단일 finding 으로 보고한다.

<br/>

## 이 skill 이 아닌 것

`gitleaks` 나 `trufflehog` 의 대체재가 아니라 **마지막 관문** 이다. 정규식 셋이 작고 읽을 수 있게 설계되어 있고, 그래서 저 도구들이 못 도는 커밋 시점에 돌 수 있다. 깨끗한 결과를 저장소에 secret 이 없다는 증거로 제시하지 않는다 — 판정과 실제로 검사한 범위를 항상 함께 보고한다.

스캐너는 범용 카테고리만 기본 탑재한다. 회사명, 내부 도메인, 실제 사용자명은 사람마다 다르므로 repo 루트의 `.sensitive-patterns` 파일이나 `~/.claude/sensitive-patterns` 에 둔다. 그런 파일이 없는 repo 에서 clean 이 나오면, 범용 셋만 돌았다는 사실을 명시한다.

<br/>

## 하지 않는 것

- sanitize 하려고 파일을 수정하는 것. 무엇을 바꿔야 하는지만 보고하고, 적용은 사용자나 후속 작업이 한다.
- `git add`, `git commit`, `git push`, 그 밖의 발행 행위.
- 스캐너가 조용했다는 이유로 저장소를 안전하다고 단정하는 것. 판정과 그 범위는 항상 같이 간다.
