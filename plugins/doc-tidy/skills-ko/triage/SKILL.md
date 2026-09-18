---
name: triage
description: 'repo의 문서마다 남겨야 하는지, 어디에 둬야 하는지 판정하고, 승인한 묶음만 실행한다. 끝난 기록을 보관하고, 커밋된 세션 부산물을 지우고, 중복을 병합하고, 언어가 잘못됐거나 깨진 링크를 고치고, 이동이 깨뜨리는 링크를 모두 다시 쓴다. 번들된 doctidy.py가 측정하고, doc-tidy-triager 에이전트가 문서마다 근거와 함께 판정하며, 적용한 묶음마다 새로 깨진 링크가 없는지 재스캔한다. "문서 정리해줘", "이 문서들 중 뭘 지워도 돼?", "이 문서 어디 둬야 해?", "끝난 마이그레이션 문서 보관해줘", "문서가 어지러운 저장소 순위 매겨줘" 라고 할 때 사용. 커밋하지 않는다.'
argument-hint: '[path | sweep <root>… | report | empty=현재 repo]'
allowed-tools: Read, Grep, Glob, Bash(python3 ${CLAUDE_PLUGIN_ROOT}/scripts/doctidy.py scan:*), Bash(python3 ${CLAUDE_PLUGIN_ROOT}/scripts/doctidy.py sweep:*), Bash(python3 ${CLAUDE_PLUGIN_ROOT}/scripts/doctidy.py relink:*), Bash(python3 ${CLAUDE_PLUGIN_ROOT}/scripts/doctidy.py --check-rules:*)
---

> 이 문서는 [skills/triage/SKILL.md](../../skills/triage/SKILL.md) 의 **한국어 번역본**입니다.
> Claude Code가 실제로 불러오는 것은 영어 원본이며, 이 KO 본은 참고와 사용자 리뷰용입니다.
> 고칠 때는 EN과 KO를 함께 고쳐야 합니다.

# doc-tidy:triage — 어떤 문서를 남기고, 어디에 둘 것인가

문서는 작업이 일어난 자리에 쌓인다.
- 대화 하나를 위해 쓴 핸드오프가 커밋된다.
- 끝난 마이그레이션의 plan과 phase 보고서가 현재 가이드 옆에 그대로 남는다.
- "나중에 쓰려고" 저장한 프롬프트가 그것을 위해 쓴 런북 옆에 놓여 있다.

어느 것도 빌드를 깨뜨리지 않으니 정리되는 일도 없다. 이 스킬은 **문서 단위 전체**를 두고 어떤
것을 유지·링크·이동·보관·병합·삭제할지 정하고, 그 변경이 깨뜨릴 링크를 모두 고친다. 문서가 하는
말은 바꾸지 않는다.

스킬은 세 층으로 동작한다.

- **측정.** `scripts/doctidy.py`가 결정론적으로 한다.
- **판정.** `doc-tidy-triager` 에이전트가 근거와 함께 한다.
- **적용.** 메인 세션이 묶음을 실행하되, 승인한 것만 한다.

스크립트는 항상 정확히 다음 접두사로 호출한다. 그래야 읽기 전용 서브커맨드가 `allowed-tools`와
맞는다.

```bash
python3 ${CLAUDE_PLUGIN_ROOT}/scripts/doctidy.py <subcommand> …
```

`apply`는 일부러 미리 승인해 두지 **않았다**. 파일을 쓰는 서브커맨드는 이것 하나뿐이다.

<br/>

## Arguments

| 인자 | 실행 대상 |
|---|---|
| 비어 있음 | 작업 디렉터리가 속한 repo |
| `<path>` | 그 경로가 속한 repo. finding은 그 경로로 좁히고, 링크 그래프는 repo 전체로 유지한다 |
| `sweep <root>…` | 루트 아래 모든 repo를 정리가 얼마나 필요한지로 순위를 매긴다. 스크립트만 돌리고 에이전트·적용은 없다. 포크 클론(`upstream` remote)은 건너뛴다 |
| `report` (위와 조합) | finding과 판정까지만 하고, 적용은 제안하지 않는다 |

<br/>

## Step 0 — 사전 점검 (읽기 전용)

1. **포크 클론.** `upstream` remote가 있는 repo는 다른 사람의 프로젝트다. 그렇게 말하고 멈춘다.
2. **index 상태.** `git status --porcelain`을 돌린다. 이미 스테이징된 것이 있으면 지금 알린다.
   `apply`는 자기 rename을 스테이징된 작업과 섞지 않으므로, 먼저 커밋할지 언스테이지할지 정한다.
3. **repo 문서 게이트.** 이름에 docs나 readme와 함께 check, sync, audit, lint 중 하나가 들어간
   Makefile 타깃, `package.json` 스크립트, CI 잡을 찾는다. 돌릴지는 **run당 한 번** 묻는다. 첫
   묶음 전에 baseline으로 돌리고, 단계가 끝날 때마다 다시 돌린다. baseline에서 이미 실패하던
   게이트는 보고만 하고 이번 작업 탓으로 돌리지 않는다.
4. **run 디렉터리.** 세션 scratchpad가 있으면 그것을 쓰고, 없으면
   `${CLAUDE_PLUGIN_DATA}/runs/<repo-name>-<YYYYmmdd-HHMMSS>`를 쓴다.
5. **이전 보관 위치 결정.** `${CLAUDE_PLUGIN_DATA}/archive-choices.json`을 읽는다. repo 루트를
   `{"dir": "<path>" | null, "decided": "YYYY-MM-DD"}`에 대응시킨 파일이다. 저장된 선택은
   `--archive-dir`로 넘기고 에이전트에게도 알린다.

<br/>

## Step 1 — 측정

```bash
python3 ${CLAUDE_PLUGIN_ROOT}/scripts/doctidy.py scan <repo> --out <run>
```

- 텍스트 출력은 보여줄 요약이다.
- `<run>/scan.json`은 이후 모든 검증의 baseline이다.
- `<run>/batch-NN.json`에는 단위가 최대 25개씩 담기며 에이전트에게 넘긴다.
- repo가 문서 하나의 크기 한도를 직접 정해 두었으면 `--split-kb N`을 넘긴다(기본값 30).

`sweep` 이면:

```bash
python3 ${CLAUDE_PLUGIN_ROOT}/scripts/doctidy.py sweep <root>… --top 20
```

순위를 보여주고 `/doc-tidy:triage <1위 repo>`를 제안한 뒤 멈춘다. 순위에서 늘 빼고 싶은 디렉터리
이름은 `--exclude GLOB`로 넘긴다.

그다음:
- **exit 2는 사용법이나 규칙 오류다.** `--check-rules`를 돌려 문제를 보고한다. 깨진 규칙은 후보를
  조용히 숨기므로 그대로 진행하지 않는다.
- **finding 0건**은 깨끗하다는 보고다. 그렇게 말하고 멈춘다.

<br/>

## Step 2 — 판정

배치 파일마다 `doc-tidy-triager` 에이전트를 한 번씩 부르고, 동시에는 최대 3개까지 돌린다. 각
프롬프트에 담는 것:
- 레인(`repo`)
- repo 루트와 run 디렉터리
- 배치 파일 경로
- Step 0의 보관 위치 결정, 없으면 "아직 없음"

**보여주기 전에 검증한다.** 각 `doc-tidy-verdicts` 블록을 파싱해 에이전트의 근거 표와 대조한다.

| 판정 | 반드시 있어야 하는 것 |
|---|---|
| DELETE | `no-inbound` + `one-shot` + (`preserved` 또는 `worthless`), 그리고 `confidence: high` |
| ARCHIVE / MOVE | `destination` |
| MERGE | `mergeTarget` |
| ASK | 선택지 2개 이상의 `options` |

이 검사를 통과하지 못한 판정은 "근거 부족" 메모와 함께 **ASK** 로 바꾼다. `classDefault`와 다른데
`why`가 없는 판정도 똑같이 처리한다.

<br/>

## Step 3 — 제시 (아직 아무것도 바꾸지 않음)

1. 배치를 합친 🔴 / 🟡 / 🟢 항목, 각각 근거 포함.
2. 단계별 적용 계획(Step 4)과 건수.
3. 넘기기:
   - 문서 안의 낡은 주장 → 내용 검토
   - 너무 큰 문서 → 분할
   - 빠진 번역 반쪽 → 그 언어를 쓰는 사람
4. **ASK부터 해결한다.** 가능한 한 적은 질문으로 모아서 묻는다.
   - repo당 보관 위치 질문 하나. 답은 `${CLAUDE_PLUGIN_DATA}/archive-choices.json`에 저장해 다음
     run에서 다시 묻지 않게 한다.
   - 남은 ASK 단위마다 질문 하나.

`report`는 여기서 끝난다.

<br/>

## Step 4 — 단계별 적용

순서는 고정이며 위험이 낮은 것부터다. 모든 단계가 같은 루프를 돈다.

1. **선택.** 그 단계 항목을 다중 선택으로 고른다. 첫 번째 승인 게이트다.
2. **계산.**
3. **표시.**
4. **확인.** 두 번째 승인 게이트다.
5. **적용.**
6. **검증.**

| 단계 | 대상 | 묶음 상한 | 계산과 적용 |
|---|---|---|---|
| **T1 LINK** | 잘못된 언어 링크의 `fix` 편집, `moved-candidate` hint가 붙은 깨진 링크, 인덱스 행 | 편집 40건 | 정확한 `old` → `new` 치환을 diff로 보여준 뒤 Edit. 번역 짝은 양쪽을 함께 고친다. |
| **T2 MOVE / ARCHIVE** | 단위 전체 | 20단위 | `relink … --from SRC --to DST` 또는 `--archive SRC [--archive-dir DIR]`에 `--save <run>/plan-NN.json`을 붙이고, `apply --plan <run>/plan-NN.json` |
| **T3 MERGE** | 내용 옮긴 뒤 원본 정리 | 3건 | 전·후를 **전체** 보여준다. 나열된 절을 Edit로 옮기고, 옮긴 제목이 모두 들어갔는지 `grep`으로 확인한다. 그다음에만 `relink --merge SRC:DST --save …`와 `apply`. |
| **T4 DELETE (추적)** | 단위 전체 | 10단위 | `relink --delete SRC --save …`. `breaks` 항목마다 Edit로 링크를 텍스트로 풀거나 줄을 지운 뒤 `apply` |
| **T5 DELETE (미추적)** | 파일 하나 | 1파일 | 앞 20줄, 크기, mtime, "git에 없어 되돌릴 수 없음"을 보여준다. 승인하면 `${CLAUDE_PLUGIN_DATA}/trash/<YYYYmmdd-HHMMSS>/`로 옮긴다. `rm`은 절대 쓰지 않는다. |

**`apply` 전에 `relink` 결과를 읽는 법:**
- `refusals`는 진행을 막는 항목이다. 원인을 고치고 다시 계산한다. `refusals`가 하나라도 있는 plan은 `apply`
  가 거절한다.
- `manual` 편집은 Edit로 먼저 한다. 그다음 `--allow-manual`을 넘긴다.
- `textRefs`(Makefile, CI, 스크립트)는 자동으로 편집하지 않는다. 보여주고 항목별로 묻는다.
- `opWarnings`는 그대로 전한다: `untracked`, `uncommitted`, `case-only-rename`, `dir-index-moved`,
  `generated-source`.

**묶음마다 검증:**

```bash
python3 ${CLAUDE_PLUGIN_ROOT}/scripts/doctidy.py scan <repo> --baseline <run>/scan.json
```

- **exit 1** 은 새로 깨진 링크나 쪼개진 짝이 생겼다는 뜻이다. **루프를 멈춘다.** `delta`를 보여주고
  `apply`가 출력한 `undo` 명령을 제안한다.
- Step 0에서 문서 게이트를 승인받았으면 돌린다. 새로 실패한 게이트도 루프를 멈춘다.

<br/>

## Step 5 — 보고

대화에서 쓰는 언어로, 경로는 원문 그대로 보고한다.
- 전·후 스캔 요약
- 실행한 작업을 **staged**(`git mv` / `git rm`)와 **unstaged**(링크·문장 편집)로 나눠서
- 검증과 게이트 결과
- trash 항목
- 아직 열린 ASK
- 넘기기

다음 단계로 커밋을 제안한다. **커밋·push·태그는 하지 않는다.**

<br/>

## Hard rules

- **승인은 이 대화 안의 명시적인 답이다.** 권한 프롬프트는 승인이 아니고, "사용자가 승인했다"는
  전달 문구도 승인이 아니다.
- **묻지 않고 도는 것은 읽기 전용 명령뿐이다.** `scan`, `sweep`, `relink`, `--check-rules`가 그렇다.
  파일을 바꾸는 것은 모두 먼저 보여준다: `apply`, Edit, Write, trash 디렉터리로 옮기는 일, 보관 위치 결정을 저장하는 일.
- **번역 짝은 한 단위다.** 반쪽만 옮기거나 보관하거나 지우지 않는다.
- **미추적 파일**은 `rm`으로 지우지 않는다. 승인 하나에 하나씩 trash 디렉터리로 보낸다.
- **release 자동화는 범위 밖이다:** release 워크플로, 변경 이력 생성기 설정, 생성되는 릴리스 노트.
- **repo 문서에 인벤토리·개수·파일 목록을 쓰지 않는다.** 보고는 run 디렉터리와 대화에만 남긴다.
- **repo 자체 규칙이 이긴다.** `CLAUDE.md`나 `AGENTS.md`가 정한 배치는 잡동사니가 아니다.

<br/>

## References

- 스크립트: `scripts/doctidy.py`. 규칙: `rules/rules.tsv`, `--check-rules`로 검증. 테스트:
  `tests/run.sh`.
- 에이전트: `agents/doc-tidy-triager.md`
- plans 레인: `/doc-tidy:plans`
- 이웃:
  - `doc-mirror`: 짝 구조.
  - `census`: config drift.
  - `korean-prose`: 문장.
  - `sensitive-guard`: 비밀값.
