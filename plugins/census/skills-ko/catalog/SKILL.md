---
name: catalog
description: '사용자 레벨 ~/.claude 와 모든 프로젝트 레벨 .claude/ 디렉터리에 걸쳐 Claude Code agent / command / skill / hook 전수 조사를 하고, 무엇을 갖고 있는지와 그것이 상시 점유 context 로 얼마를 쓰는지 보고한다. "내 config 목록화해줘" / "agent 뭐뭐 있지" / ".claude 인벤토리" / "내 context 왜 이렇게 꽉 찼지" 라고 할 때 사용.'
argument-hint: "[--out FILE] [--top N] [--config PATH]"
allowed-tools: Bash, Read
---

> 본 문서는 [skills/catalog/SKILL.md](../../skills/catalog/SKILL.md) 의 **한국어 번역본** 입니다.
> Claude Code 가 실제 로드하는 것은 영어 원본이며, 본 KO 본은 참조 / 사용자 리뷰 용도입니다.
> 수정 시 EN + KO 둘 다 동시 수정해야 합니다.

# census:catalog — 나는 실제로 무엇을 갖고 있나?

설정된 모든 Claude config 루트를 스캔하고, 각 agent / command / skill / hook 을 하나의 asset 그래프로 정규화한 뒤, 거기서 카탈로그를 렌더링한다.

이 skill 은 **읽기 전용** 이다. 스캔 대상 트리에 절대 쓰지 않고, 발견한 항목을 수정하지 않으며, 아무것도 제거하지 않는다.

<br/>

## 실행

```bash
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/census.py" catalog --top 10
```

카탈로그를 stdout 대신 파일로 쓰려면 `--out CENSUS.md` 를, 특정 config 를 지정하려면 `--config PATH` 를 넘긴다. 사용자가 `$ARGUMENTS` 로 준 것은 그대로 전달한다.

스캔과 표 렌더링은 스크립트가 한다. 당신 일은 스크립트가 반환한 다음부터다 — **카탈로그를 답변에 그대로 옮겨 적지 않는다.** 그것을 가리키고, 해석한다.

<br/>

## 설정

config 는 다음 순서로 해석되며, 처음 걸린 것이 완전히 이긴다 (병합 없음):

1. `./.census.json` 또는 `./census.json`
2. `~/.claude/.census.json` 또는 `~/.claude/census.json`
3. 내장 기본값 — `userRoots: ["~/.claude"]`, `projectRoots: ["."]`

실행 결과 루트가 하나뿐인데 사용자가 분명히 더 갖고 있어 보이면 (다른 repo 를 언급하거나, 개수가 적어 보이면), `~/.claude/census.json` 을 만들라고 알려준다:

```json
{
  "userRoots": ["~/.claude"],
  "projectRoots": ["~/code/*", "~/work/*"],
  "exclude": ["archived-*"],
  "excludeOssForks": true
}
```

`excludeOssForks` 는 `upstream` git remote 가 있는 repo 를 건너뛴다 — fork 한 repo 는 당신 것이 아니라 그 maintainer 의 `.claude/` 를 담고 있어 카탈로그를 오염시킨다.

<br/>

## 무엇을 보고하나

스크립트가 출력한 숫자를 먼저 말하고, 아래 네 가지 해석을 덧붙인다. 짧게 유지한다 — 세부는 카탈로그 자체가 담고 있다.

**1. Context budget — 헤드라인.** 모든 agent · command · skill 의 `name` + `description` 은 *매* 세션 시스템 프롬프트에 상주하고, 본문은 호출할 때만 로드된다. 이 생태계에서 다른 어떤 것도 드러내지 않는 유일한 숫자다 (`claude plugin details` 는 플러그인 하나를 다루지 전체 config 를 다루지 않는다).

스크립트는 이것을 두 가지로 보고하고, 그 차이가 중요하다:

- **세션당** — 글로벌 루트에, 세션이 시작된 repo 하나를 더한 값. 세션이 실제로 치르는 비용이고, 기준으로 삼아야 할 숫자다. 리포트는 가장 무거운 repo 도 지목하는데, 그것이 최악의 경우다.
- **전체 루트 합산** — 어디서든 발견된 모든 것. 한 세션에서 이것을 치르는 사람은 없다. "config 를 얼마나 쌓아왔나" 에 답하는 값이지 "이게 나한테 얼마를 물리고 있나" 가 아니다. 절대 세션 비용으로 인용하지 않는다.

기준은 **세션당** 수치 대비로 잡는다: ~5k 토큰 미만은 특별할 것 없고, ~10k 면 다듬기 한 번 할 만하며, ~25k 를 넘으면 사용자가 무언가를 입력하기도 전에 매 세션의 상당 부분이 소모된다. 상위 항목을 지목하고 다듬으면 무엇을 벌 수 있는지 말한다 — 권하되, 절대 직접 고치지 않는다.

**2. 중복 이름.** 미러된 repo 들에 걸친 바이트 단위 동일 사본은 이미 두 출처를 함께 표기한 한 행으로 접혀 있다 — 그것은 미러가 제대로 동작하는 것이지 finding 이 아니다. 드러낼 가치가 있는 것은 세션을 어디서 시작했느냐에 따라 *다른* 내용으로 해석되는 이름이며, 진 쪽은 조용히 가려진다. 그렇다고 말하고 그 진단을 소유한 `/census:drift` 로 넘긴다 — 여기서 비교를 시도하지 않는다.

**3. 건너뛴 repo.** 스크립트가 무엇을 왜 제외했는지 나열한다. 실수로 빠진 것이 없는지 살핀다. 특히 사용자가 세고 싶어 했는데 패턴에 걸려 제외된 repo 를 본다.

**4. Scope 분포.** 글로벌 (`~/.claude/`) 대 repo 범위를 보면 config 가 중앙화돼 있는지 흩어져 있는지 알 수 있다. repo 범위 항목이 많고 같은 항목이 여러 repo 에 반복된다면 승격 후보다 — 드러내되, 승격이 실제로 안전한지는 `/census:portability` 가 판단하게 둔다.

<br/>

## 하드 룰

- 읽기 전용. 허용된 유일한 쓰기는 사용자가 요청한 명시적 `--out` 대상뿐이다.
- 발견한 항목을 수정 · 이동 · 삭제하지 않고, 이 skill 의 일부로 그렇게 하겠다고 제안하지도 않는다.
- 스캔 대상 트리에 절대 쓰지 않는다. 사용자가 `--out` 을 요청하면 config 루트가 아니라 현재 디렉터리를 기본으로 한다.
- 카탈로그 전체를 다시 붙여넣지 않는다. 출력을 참조하고, 해석한다.
- 여기서 공유 가능성을 판정하지 않는다 — 그것은 `/census:portability` 다.
- 여기서 EN/KO 나 미러 페어를 diff 하지 않는다 — 그것은 `/census:drift` 다.
