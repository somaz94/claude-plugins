# doc-tidy

문서마다 남겨야 하는지, 어디에 둬야 하는지 판정하고 — 승인한 것만 적용합니다.

> 이 문서는 [README.md](README.md)의 한국어판입니다. 내용이 어긋날 경우 영문판이 기준입니다.

문서는 작업이 일어난 자리에 쌓입니다. 대화 하나를 위해 쓴 핸드오프가 커밋되고, 끝난 마이그레이션의 plan과 phase 보고서가 현재 가이드 옆에 그대로 남습니다. "나중에 쓰려고" 저장한 프롬프트는 그것을 위해 쓴 런북 옆에 놓여 있고, `CLAUDE.md`의 한 줄은 Claude Code가 지난달 지운 plan 파일을 여전히 가리킵니다. 어느 것도 빌드를 깨뜨리지 않으니 정리되는 일도 없습니다. 그리고 낡은 문서 하나하나가 다음에 읽는 사람, 또는 다음 에이전트가 걸러내야 할 짐이 됩니다.

Claude Code 세션 안에서:

```
/plugin marketplace add somaz94/claude-plugins
/plugin install doc-tidy@somaz94
```

대화형 세션에 들어가지 않고 셸에서 바로 설치하려면:

```bash
claude plugin marketplace add somaz94/claude-plugins
claude plugin install doc-tidy@somaz94
```

설치한 뒤 아무 저장소에서나:

```
/doc-tidy:triage
```

<br/>

## 왜 필요한가

문서를 검사하는 도구는 문서 **안**을 봅니다. 링크가 유효한지, 번역이 맞춰져 있는지, 문장이 어색한지. 이 문서가 아직 존재해야 하는지, 제자리에 있는지, 옮기면 무엇이 깨지는지는 아무도 묻지 않습니다. 어시스턴트를 많이 쓰는 저장소에서 잔해가 가장 많이 쌓이는 곳이 바로 이 질문입니다. 어시스턴트는 오래된 파일을 정리하는 것보다 새 파일을 만드는 데 훨씬 적극적이기 때문입니다.

이 질문에 안전하게 답하려면 한 번의 훑기로는 얻을 수 없는 두 가지가 필요합니다. 추측하지 않는 **측정**: 누가 이 파일을 어디서 어떤 언어로 링크하는지, 파일이 설명하는 코드가 언제 마지막으로 바뀌었는지. 그리고 저장소 자체의 규칙을 손에 들고 파일을 읽는 **판정**: 커밋된 프롬프트가 README에 색인된 재사용 템플릿일 수 있고, 마이그레이션 폴더가 그 저장소가 요구하는 바로 그 배치일 수 있기 때문입니다.

<br/>

## 무엇이 나오나

먼저 스크립트가 측정합니다.

```
doc-tidy scan: /Users/you/code/acme-platform
  212 documents (agent-config 18, archived 9, byproduct 3, entry 64, guide 110, historical 8); 2 untracked
  archive: docs/_archive (mirror)
  1 critical, 9 warning, 4 suggestion
🔴 broken-link · docs/deploying.md — 1 broken relative link(s)
     L42: ../runbooks/rollback.md (moved-candidate: ops/runbooks/rollback.md)
🟡 byproduct · docs/billing-ci-handoff.md — looks like a session byproduct
🟡 migration-folder · docs/queue-migration — a migration folder whose status reads as finished
🟡 wrong-language-link · README-ko.md — 2 link(s) point at the other language's half of a pair
🟢 drift · . — 6 document(s) were last changed long before the code they describe
```

그다음 `doc-tidy-triager` 에이전트가 후보를 하나씩 읽고 근거와 함께 판정을 돌려줍니다.

```
🟡 ARCHIVE (high) · docs/queue-migration (folder, 7 documents)
   completion: docs/queue-migration/status.md:3 "Status: done — cutover finished 2025-03-28"
   destination: docs/_archive/queue-migration/
🟢 KEEP (high) · docs/alert-followup-prompt.md   default DELETE
   inbound: runbooks/README.md:18 — indexed and reused for every alert of this kind
```

<br/>

## 모든 판정에는 근거가 붙습니다

| 판정 | 필요한 것 |
|---|---|
| KEEP | 문서를 참조하는 곳, 문서의 위치를 정한 저장소 규칙, 또는 최신이라는 증거 |
| LINK | 깨진 링크, 또는 행을 추가해야 할 인덱스 |
| MOVE / ARCHIVE | 배치 규칙이나 완료 표시, 그리고 목적지 |
| MERGE | 겹치는 구간, 그리고 옮겨 올 절 |
| DELETE | **세 가지 모두**: 참조하는 곳이 없고, 한 시점만을 위한 문서였고, 핵심 내용이 다른 곳에 보존돼 있거나 남길 가치가 없음 |
| ASK | 실제 선택지가 있는 질문 — "이 저장소에서 끝난 기록은 어디로 가나?"에 대한 답은 저장되므로 저장소당 한 번만 묻습니다 |

근거가 없는 판정은 보여드리기 전에 질문으로 바뀝니다. 종류별 기본값을 뒤집는 판정은 이유를 밝혀야 합니다.

<br/>

## 위험이 낮은 것부터, 묶음으로 적용합니다

항목을 고르고 계산된 변경을 확인하기 전에는 아무것도 바뀌지 않습니다. 순서는 고정입니다.

1. **링크** — 잘못된 언어 링크, 후보가 하나뿐인 깨진 링크, 인덱스 행.
2. **이동과 보관** — 언어 짝 전체, 폴더 전체. 반쪽만 옮기는 일은 없습니다.
3. **병합** — 옮겨 올 내용을 전부 보여주고 확인한 뒤에야 원본을 정리합니다.
4. **추적 파일 삭제** — 삭제가 깨뜨릴 링크를 하나씩 먼저 해결합니다.
5. **미추적 파일** — 하나씩, trash 디렉터리로. `rm`은 쓰지 않습니다.

이동·병합·삭제는 모두 `relink`를 거칩니다. `relink`는 링크 편집을 원래 쓰인 형식 그대로 계산합니다 — 상대 경로, 루트 기준 경로, agent config 안의 backtick 경로. 이미 존재하거나 저장소 밖으로 나가거나 git이 무시하는 목적지는 거절합니다. `apply`는 모든 편집을 현재 파일과 다시 대조하고, 무엇이든 달라졌으면 아무것도 쓰지 않습니다. 묶음마다 첫 스캔과 비교하는 재스캔에서 **새로 깨진 링크와 쪼개진 짝이 없어야** 하며, 그렇지 않으면 루프를 멈추고 되돌리는 명령을 건넵니다.

<br/>

## plan은 만료됩니다

Claude Code는 시작 시 정리 과정에서 `cleanupPeriodDays`(기본 30일)보다 오래된 최상위 plan 파일을 지웁니다. plan 입장에서는 괜찮습니다 — 하지만 plan을 가리키는 `CLAUDE.md` 한 줄, 에이전트, plan 템플릿, 메모리 노트는 그렇지 않습니다.

```
/doc-tidy:plans
```

곧 사라질 plan, 이미 사라진 plan을 가리키는 모든 durable 참조, 그리고 지정한 워크스페이스 루트에 흩어진 Markdown을 나열합니다. Claude 설정을 git으로 관리한다면 `--recover-from`이 사라진 plan 마다 그것을 지운 커밋을 찾아 주므로, 참조를 추측하지 않고 복원할 수 있습니다. 만료되기 전에 plan의 durable 부분을 밖으로 복사할 뿐, plan을 살려 두려고 편집하지는 않습니다.

<br/>

## 하지 않는 일

**문서가 하는 말은 판정하지 않습니다.** 내용 최신성, 번역 품질, 문장은 다른 도구의 몫입니다 — 낡은 주장에 대한 판정은 편집이 아니라 넘기기입니다.

**짝 구조는 검사하지 않습니다.** 번역 짝을 한 단위로 옮기기는 하지만, 두 반쪽의 절이 여전히 같은지는 [`doc-mirror`](../doc-mirror)의 일입니다.

**설정 drift는 추적하지 않습니다.** 에이전트 사본 두 개가 서로 다른 것은 [`census`](../census)의 영역입니다.

**현재 plan을 최신으로 유지하지 않습니다.** 그것은 [`session-continuity`](../session-continuity)의 일이고, 이 플러그인은 plan이 끝난 뒤 무엇을 남길지 정합니다.

<br/>

## 스크립트 직접 실행하기

번들 스크립트 하나 — python3, **표준 라이브러리만** 쓰고 설치 단계가 없습니다. `apply`를 뺀 모든 서브커맨드는 읽기 전용입니다.

```bash
python3 scripts/doctidy.py scan .                                   # 저장소 하나
python3 scripts/doctidy.py sweep ~/code --top 10                    # 여러 저장소 순위
python3 scripts/doctidy.py plans --workspace ~/code                 # plans 레인
python3 scripts/doctidy.py relink . --archive docs/queue-migration --save /tmp/plan.json
python3 scripts/doctidy.py apply --plan /tmp/plan.json              # 파일을 쓰는 유일한 명령
python3 scripts/doctidy.py --check-rules                            # rules/rules.tsv 검증
```

무언가에 이름을 붙이는 규칙 — 커밋된 프롬프트가 어떻게 생겼는지, 어떤 디렉터리 이름이 "보관"을 뜻하는지, 어떤 줄이 마이그레이션 종료를 선언하는지 — 은 코드가 아니라 `rules/rules.tsv`의 데이터입니다. 모든 행에는 반드시 걸려야 하는 예시와 걸리면 안 되는 반례가 붙어 있고, `--check-rules`가 둘 다 증명합니다. 옆에 `rules.local.tsv`를 두면 자신의 이름 짓는 습관에 맞게 id 기준으로 행을 덮어쓸 수 있습니다.

CI가 돌리는 검사는 모두 직접 돌릴 수 있는 파일이기도 합니다: `bash plugins/doc-tidy/tests/run.sh`.

<br/>

## 절대 하지 않는 것

- 커밋, push, 태그.
- 미추적 파일을 `rm`으로 지우거나, 번역 짝의 반쪽만 옮기는 일.
- release 자동화를 편집하거나, 저장소 문서에 인벤토리를 적는 일.
- plan의 결정 텍스트를 편집하거나, 수명을 늘리려고 plan을 건드리는 일.
- 네트워크 요청.

<br/>

## 릴리스

이 마켓플레이스의 플러그인은 각자 따로 버전을 매기고 릴리스합니다. `doc-tidy`의 모든 변경 사항은 이 디렉터리 범위의 커밋과 함께 [doc-tidy 릴리스](https://github.com/somaz94/claude-plugins/releases?q=doc-tidy&expanded=true)에서 볼 수 있습니다.

<br/>

## 라이선스

MIT — [LICENSE](../../LICENSE)를 참고하세요.
