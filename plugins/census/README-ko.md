# census

흩어진 Claude Code 설정을 읽기 전용으로 감사하는 도구입니다.

> 이 문서는 [README.md](README.md)의 한국어판입니다. 내용이 어긋날 경우 영문판이 기준입니다.

사용자 수준의 `~/.claude/`에 더해 저장소마다 `.claude/` 디렉터리가 생기고 나면, 답하기 어려워지는 질문이 세 가지 생깁니다. 그런데 이 질문에 답해 주는 기본 기능은 없습니다. `census`가 답하되, 아무것도 바꾸지 않습니다.

```bash
/plugin marketplace add somaz94/claude-plugins
/plugin install census@somaz94
```

<br/>

## 스킬

| 스킬 | 답하는 질문 |
|---|---|
| `/census:catalog` | 나는 무엇을 갖고 있고, 매 세션 컨텍스트로 얼마를 치르고 있는가? |
| `/census:drift` | 일치해야 할 두 가지가 어디서 어긋났는가? |
| `/census:portability` | 이 중 내가 아닌 사람의 환경에서도 동작할 것은 무엇인가? |

<br/>

## 사용법

Claude Code 세션에서 스킬 이름을 입력하면 됩니다. 스킬은 설정해 둔 루트 전체를 대상으로 번들 스크립트를 돌리고 그 결과를 읽어서 설명해 줍니다. 스캔과 렌더링은 스크립트가, 해석은 스킬이 맡습니다.

아래 예시는 모두 다음의 작은 설정 하나에서 나온 것입니다. 숫자의 의미를 알 수 있도록 구조를 먼저 보여 드립니다.

```
~/.claude/                        ~/code/acme-platform/.claude/
  agents/shell-reviewer.md          agents/shell-reviewer.md    ← 전역과 이름은 같고
  agents/db-migrator.md             agents/storage-reviewer.md    내용은 다름
  commands/ship.md
  skills/changelog/SKILL.md       ~/code/billing-api/.claude/
  hooks/guard.sh                    commands/deploy.md          ← description 없음
  settings.json
```

<br/>

### `/census:catalog` — 무엇을 갖고 있고 얼마를 치르는가

여기서 시작하세요. 설정이 전혀 필요 없는 유일한 스킬이자, 다른 어디서도 답을 구할 수 없는 유일한 스킬입니다.

```
- Assets: 8 (4 agents, 2 commands, 1 hooks, 1 skills)
- Per-session context: ~65 tokens from the global root, rising to ~105 in the heaviest repo (`acme-platform`)
- Across all roots: ~106 tokens (427 chars) — accumulated total, not a session cost

## Global
### Agents (2)
| Name | Origin | Description |
|---|---|---|
| `db-migrator` | user | Plans and reviews schema migrations before they are applied. |
| `shell-reviewer` | user | Reviews shell scripts for portability between bash and zsh. |
…
## Context budget — top 7 by description size
| Item | Kind | Chars | ~Tokens |
|---|---|---|---|
| `storage-reviewer` | agent | 67 | 16 |
| `shell-reviewer` | agent | 64 | 16 |
```

전체 루트 합계가 아니라 **세션당** 수치를 보세요. 실제로 손댈 지점은 맨 아래 컨텍스트 예산 표입니다. `description` 길이 순으로 정렬되는데, 그게 매 세션 실제로 치르는 비용이기 때문입니다.

<br/>

### `/census:drift` — 일치해야 할 것이 어디서 어긋났는가

```
- Checked: 7 assets across 7 files, plus 1 hook registrations
- 🔴 2  ·  🟡 0  ·  🟢 0

## 🔴 Behavior differs or routing is broken (2)

**[shadowed]** `shell-reviewer` (agent) exists at both user and project level with different content

Origins: acme-platform, user. A project-level definition overrides the user-level one inside
that repo, so the same name behaves differently depending on where the session is started —
and nothing reports which copy won.

- `~/code/acme-platform/.claude/agents/shell-reviewer.md`
- `~/.claude/agents/shell-reviewer.md`

**[no-description]** `deploy` (command) declares no description
```

🔴은 결과를 말로 설명할 수 있는 항목입니다. 이름이 같은 두 에이전트 중 하나가 조용히 이기거나, 어떤 항목에 아예 도달할 수 없게 되는 식입니다. 🟢에는 확인 성격의 항목도 들어갑니다. 미러된 사본이 완전히 일치한다는 보고가 그렇고, 이건 결함이 아니라 의도한 상태입니다.

<br/>

### `/census:portability` — 이 중 남의 환경에서도 동작할 것은 무엇인가

```
- 🟢 PORTABLE: 6  ·  🟡 PARAMETERIZABLE: 1  ·  🔴 PERSONAL: 1
- Share-ready without edits: 6/8 (75%)

Derived markers — strings that identify this machine or owner:

| Marker | Category | Derived from |
|---|---|---|
| `alex` | user | $USER |
| `code` | layout | projectRoots ~/code/* |

Plus 2 repo-scoped markers: `acme-platform`, `billing-api`

## 🟡 PARAMETERIZABLE (1)
**`guard.sh`** — ~/.claude/hooks/guard.sh
- `~/.claude/hooks/guard.sh:3` [code/layout] `code` — case "$1" in "$HOME/code/acme-platform/vendor"/*) exit 1 ;; esac

## 🔴 PERSONAL (1)
**`storage-reviewer`** — ~/code/acme-platform/.claude/agents/storage-reviewer.md
- `…/storage-reviewer.md:3` [frontmatter/repo] `acme-platform` — description: Reviews changes inside acme-platform/storage/ for retention policy.
```

마커 표를 먼저 확인하세요. 아래의 모든 등급이 그 문자열에서 파생되므로, 등급이 이상하다면 대개 판정이 틀린 게 아니라 마커가 빠진 것입니다. 히트마다 파일과 줄 번호, 그리고 그 줄의 어디에 걸렸는지까지 함께 인용하므로 아무것도 다시 읽어 볼 필요 없이 판정을 확인할 수 있습니다.

<br/>

### 처음 실행할 때

첫 보고서가 신호가 될지 잡음이 될지는 두 가지 설정에 따라 갈립니다.

**`projectRoots`**의 기본값은 `["."]`, 즉 현재 디렉터리뿐입니다. 이 상태에서는 전역 루트와 실행한 디렉터리에 마침 있는 `.claude/`만 잡히고 그 밖에는 아무것도 잡히지 않습니다. 저장소가 실제로 있는 위치(`["~/code/*"]` 같은 값)를 지정하세요.

**`pairs`**의 기본값은 `agents-ko` / `commands-ko` / `skills-ko` 번역 미러 관례를 따릅니다. 번역 미러를 두지 않는다면 `"pairs": {}`로 설정하세요. 그러지 않으면 미러가 없는 모든 항목이 🔴 미러 누락으로 보고돼 나머지가 전부 묻힙니다. 두 설정 모두 아래 설정 파일에서 지정합니다.

<br/>

## `/census:catalog` 상세

설정된 모든 루트에서 에이전트, 커맨드, 스킬, 훅을 수집한 뒤 전역과 저장소 범위로 나눠 목록을 보고합니다.

가장 쓸모 있는 출력은 다른 어디서도 드러나지 않는 것, 바로 **상시 점유 컨텍스트 비용**입니다. 모든 에이전트·커맨드·스킬의 `name`과 `description`은 *매* 세션의 시스템 프롬프트에 상주하고, 본문은 호출할 때만 로드됩니다. `claude plugin details`는 이걸 플러그인 하나 단위로만 보고할 뿐, 설정 전체를 보고해 주는 도구는 없습니다.

비용은 **세션 단위로** 보고합니다. 실제로 치르는 형태가 그것뿐이기 때문입니다. 한 세션은 전역 루트와 세션이 시작된 저장소 하나만 로드하지, 모든 저장소를 한꺼번에 로드하지 않습니다. 그래서 전체 루트를 더한 값은 존재하지 않는 세션을 묘사하는 셈입니다. 누적 합계도 그것이 무엇인지 명시해서 함께 보여줍니다.

미러된 저장소들에 걸쳐 바이트까지 똑같은 사본은 하나로 셉니다. 파일은 둘, 자산은 하나입니다.

위의 작은 예시가 아니라, 한동안 쌓여 온 설정 규모에서는 이렇게 나옵니다.

```
- Assets: 126 (80 agents, 34 commands, 7 hooks, 5 skills) — found in 166 files; 40 are identical copies across mirrored repos
- Per-session context: ~10,340 tokens from the global root, rising to ~16,511 in the heaviest repo
- Across all roots: ~30,115 tokens — accumulated total, not a session cost
```

<br/>

## `/census:drift` 상세

네 개의 축을 🔴 / 🟡 / 🟢으로 보고합니다.

**중복.** 심각도는 중복이라는 사실이 아니라 사본들이 *일치하는지*에서 나옵니다. 미러된 저장소 쌍의 동일한 사본은 의도한 상태이므로 결함이 아니라 확인으로 보고합니다. 일치하지 않는 사본은 드리프트이고, 같은 이름이 사용자 수준과 프로젝트 수준에 서로 다른 내용으로 존재하는 경우는 더 나쁩니다. 해당 저장소 안에서는 프로젝트 사본이 이기므로, 세션을 어디서 시작했는지에 따라 같은 이름이 다르게 동작합니다.

**번역 쌍.** 미러는 보통 원본과 일정한 차이를 둔 채로 갈라집니다. 번역본임을 알리는 배너를 앞에 붙이거나, 실제 정의로 로드되지 않도록 frontmatter를 코드 블록으로 감싸는 식의 하우스 스타일이 그렇습니다. 구조를 단순 비교하면 이 관례가 파일마다 한 번씩 보고돼 정작 진짜 드리프트를 묻어버립니다.

그래서 오프셋을 **미러 디렉터리 단위로 보정**합니다. 한 디렉터리의 쌍들이 대체로 공유하는 차이를 그 디렉터리의 기준선으로 삼고, 자기 형제 파일들에서 벗어나는 파일만 보고합니다. 어떤 하우스 스타일도 하드코딩하지 않으므로, 이 도구가 한 번도 본 적 없는 관례도 똑같이 보정됩니다. 벗어났는지는 양쪽 방향으로 모두 따집니다. 형제 열다섯 개가 모두 가진 배너를 혼자 빠뜨린 파일은, 혼자 섹션을 추가한 파일만큼이나 이상치입니다.

다만 구조가 전부는 아닙니다. 두 파일의 구조가 일치하면서도 미러가 몇 달 뒤처져 있을 수 있고, 내용으로는 판정할 수 없습니다. 번역은 애초에 다르게 읽히는 것이 *당연해서*, 내용을 비교하면 번역 자체가 드리프트로 보고됩니다. 대신 git 이력이 답합니다. 미러가 마지막으로 커밋된 뒤에 원본이 커밋됐다면 미러가 뒤처진 것이고, 이건 어느 언어로 쓰였든 성립합니다. 추적되지 않거나 아직 커밋되지 않은 파일은 틀린 판정 대신 판정 없음으로 둡니다.

**훅.** 훅은 포인터이고, 가리키는 파일이 없을 수도 있습니다. 등록은 `settings.json`에 그대로 남아 있으므로 훅은 설정된 것처럼 보이면서 아무 일도 하지 않습니다. 이걸 보고해 주는 도구는 달리 없습니다.

**Frontmatter.** 실제로 무언가를 망가뜨리는 건 `description` 누락입니다. Claude가 이 항목을 언제 꺼내 쓸지 판단하는 유일한 신호이므로, 없으면 이름을 직접 대지 않는 한 그 항목에는 도달할 수 없습니다.

<br/>

## `/census:portability` 상세

항목마다 한 대의 머신에 얼마나 강하게 묶여 있는지로 등급을 매깁니다. 훅은 실제로 실행하는 스크립트를 기준으로 평가합니다. 등록은 포인터일 뿐이고, 하드코딩된 경로는 대상 파일 안에 있기 때문입니다.

마커는 **하드코딩된 값이 아니라 파생된 값**입니다. `$USER`에서, 저장소를 정리하려고 직접 만든 디렉터리 이름에서, 그리고 스캔 대상 저장소들의 git 원격에서 나옵니다. 공개 포지(forge)에서는 계정 이름이 소유자를 식별하지만, 자체 호스팅 포지에서는 "owner"가 그저 그룹 이름이고 흔한 단어인 경우가 많아 대신 호스트명을 취합니다. 덕분에 같은 검사가 내 식별자 대신 다른 사람의 식별자를 잡아낼 수 있습니다.

각 저장소 자신의 이름도 마커지만, **그 저장소 안에서만** 그렇습니다. 저장소 범위 에이전트는 대개 자기 저장소를 상대 경로로 가리킵니다. `Reviews changes inside acme-platform/storage/` 같은 서술에는 머신 전역 식별자가 하나도 없어서, 가장 이식성이 낮은 축에 속하면서도 그대로 두면 완벽하게 이식 가능한 것으로 채점됩니다. 전역에 적용하면 `docs` 같은 이름의 저장소가 온갖 문장에 걸리지만, 자기 저장소 항목에만 한정하면 걸렸다는 사실이 곧 의미가 됩니다.

등급은 마커가 몇 개인지가 아니라 **어디에 걸렸는지**에서 나옵니다.

| 등급 | 규칙 | 조치 |
|---|---|---|
| 🟢 `PORTABLE` | 히트 없음 | 그대로 승격 |
| 🟡 `PARAMETERIZABLE` | 코드 스팬이나 코드 블록 안에만 히트 | 리터럴을 설정값으로 교체 |
| 🔴 `PERSONAL` | frontmatter나 본문에 히트 | 설정이 아니라 재작성 |

중요한 건 frontmatter에 걸린 경우입니다. 특정 저장소를 지목하는 `description`은 Claude가 라우팅에 쓰는 값이므로, 그걸 바꾸면 *언제 이 항목이 발동하는지*가 바뀝니다. 설정 변경이 아니라 재작성입니다.

이식 가능한 항목이 적게 나오는 건 성숙한 개인 설정에서 정상이며 결함이 아닙니다. 그 항목들이 상상 속의 일반적인 환경이 아니라 실제 환경을 위해 쓰였다는 뜻입니다.

> **사각지대: 이름 붙은 인프라.** 마커는 원격과 루트에서 나오므로 클러스터·환경·프로젝트 코드명 같은 이름은 어느 쪽에서도 나오지 않습니다. 저장소가 아니라 `prod-eu-1`에 묶인 항목은 여전히 🟢으로 나옵니다. 🟢 목록은 그대로 믿지 말고 한 번 읽어보고, 그런 문자열은 `portability.markers`에 넣으세요.

> **비밀 정보 스캐너가 아닙니다.** 이 도구는 "이게 남의 환경에서도 동작할까?"에 답하지, "이게 무언가를 유출할까?"에는 답하지 않습니다. 저장소 레이아웃 이름은 이식성이 없지만 무해하고, 비밀번호는 유출이 되지만 이식성은 완벽합니다. 공개를 앞두고 있다면 두 검사가 모두 필요합니다.

<br/>

## 설정

선택 사항입니다. 설정이 없으면 `census`는 `~/.claude`와 현재 디렉터리의 `.claude/`를 스캔합니다.

탐색 순서는 다음과 같고, 먼저 발견된 것이 그대로 이깁니다. 여러 파일을 병합하지는 않습니다.

1. `./.census.json` 또는 `./census.json`
2. 사용자 수준 Claude 설정 디렉터리의 `census.json`
3. 내장 기본값

```json
{
  "userRoots": ["~/.claude"],
  "projectRoots": ["~/code/*", "~/work/*"],
  "exclude": ["archived-*"],
  "excludeOssForks": true,
  "pairs": { "agents": "agents-ko", "commands": "commands-ko", "skills": "skills-ko" },
  "portability": { "markers": [] }
}
```

| 키 | 의미 |
|---|---|
| `userRoots` | 그 자체가 Claude 설정 디렉터리**인** 경로 |
| `projectRoots` | `.claude/`를 스캔할 **저장소** 디렉터리의 glob |
| `exclude` | 건너뛸 저장소 basename 패턴 |
| `excludeOssForks` | `upstream` 원격이 있는 저장소는 건너뜀 — 포크에 담긴 `.claude/`는 내 것이 아니라 원저장소 관리자들의 것 |
| `pairs` | 번역 미러 디렉터리, 미러 대상 디렉터리를 키로 지정 |
| `portability.markers` | 추가 식별 문자열, 비워두면 자동 파생 |

생성된 설정 파일이나 카탈로그에는 그것을 만든 머신의 실제 경로가 담깁니다. `.census.json`과 `--out`으로 만든 파일은 공개 저장소에 넣지 마세요.

<br/>

## 스크립트 직접 실행하기

각 스킬은 번들 스크립트 하나를 감쌉니다. 이 스크립트는 python3 **표준 라이브러리만** 사용하므로 설치 과정도, 의존성도 없습니다.

```bash
python3 scripts/census.py catalog --top 10
python3 scripts/census.py drift --limit 15
python3 scripts/census.py portability --evidence 3
python3 scripts/census.py scan          # 정규화된 자산 그래프를 JSON으로 출력
```

`--out FILE`은 파일로 쓰고, `--json`은 원본 보고서를 내보내며, `--config PATH`는 설정 파일을 지정합니다.

이 분리는 의도적입니다. 스크립트가 스캔하고 렌더링하면, 스킬이 해석합니다. 백 개 남짓한 파일을 읽는 건 모델이 할 일이 아닙니다. 느리고, 비싸고, 실행할 때마다 달라집니다. 그 숫자가 무엇을 뜻하는지 판단하는 것이 모델의 일입니다.

<br/>

## 절대 하지 않는 것

- 스캔 대상 트리 안에 파일을 쓰는 일. 유일하게 쓰는 파일은 명시적으로 지정한 `--out` 대상입니다.
- 발견한 항목을 편집·이동·동기화·삭제하는 일.
- 무언가를 플러그인이나 마켓플레이스로 승격시키는 일.

<br/>

## 라이선스

MIT — [LICENSE](../../LICENSE)를 참고하세요.
