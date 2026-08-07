# doc-mirror

번역된 문서 쌍이 서로 어긋나지 않게 지킵니다.

> 이 문서는 [README.md](README.md)의 한국어판입니다. 내용이 어긋날 경우 영문판이 기준입니다.

`README.md` 옆에 `README-ko.md`를 두는 저장소는 지킬 방법이 없는 약속을 한 셈입니다. 한쪽만 고쳐도 모든 검사가 통과합니다. 빌드는 초록색이고, 린터는 조용하고, diff는 의도적으로 보이고, 리뷰어 눈에는 파일 하나만 들어옵니다. 그 쌍을 지켜보는 것이 어디에도 없습니다. 몇 달 뒤 미러는 박제가 되어 있고, 그 언어를 고른 독자는 지난 분기 지침을 따르고 있습니다.

```bash
/plugin marketplace add somaz94/claude-plugins
/plugin install doc-mirror@somaz94
```

설치한 뒤 아무 저장소에서나:

```
/doc-mirror:check
```

<br/>

## 무엇이 나오나

```
Root: /Users/you/code/acme-platform
Pairs: 156 documents with 155 mirrors (en) across 113 directories

🔴 Critical (2)
  docs/deploying.md
    no en mirror — 14 of 15 documents in this directory have one
  README-en.md
    relative link does not resolve: docs/runbook.md

🟡 Warning (3)
  docs/observability-en.md
    shape has diverged from its source — headings 12 vs 6, code blocks 6 vs 3
  docs/scaling-en.md
    looks like a mirror but has no source beside it — either the source was
    removed, or this suffix was never a language
```

지적은 네 가지이고, 손해가 큰 순서입니다.

- **미러 누락** — 그 디렉터리의 대부분이 미러를 갖고 있는데 이것만 없는 경우. 번역할 문서였는데 안 된 것입니다.
- **깨진 상대 링크** — 특히 여기서 가장 중요한 경우, 한쪽 언어에서는 열리고 다른 쪽에서는 안 열리는 링크.
- **구조 어긋남** — 제목·코드블록·표·목록·링크 개수가 더 이상 맞지 않음. 원본에 섹션이 늘었는데 미러에는 안 늘어난 상황이 정확히 이 모양입니다.
- **고아 미러** — 원본이 삭제되거나 이름이 바뀌어 사라진 미러.

<br/>

## 규칙은 설정하는 게 아니라 찾아냅니다

설정할 것이 없고 설정 파일도 없습니다. `README-ko.md`, `guide.ja.md`, `docs/setup-pt-br.md`가 손대지 않아도 그대로 동작합니다. 짝짓기 규칙을 선언받는 게 아니라 저장소에서 읽어내기 때문입니다.

이 성질은 양방향으로 작동하고, 리포트를 쓸 만하게 유지하는 건 두 번째 방향입니다.

- 어떤 접미사가 언어로 인정되려면 **같은 디렉터리의 다른 쌍이 그것을 이미 증명**해야 합니다. 이 규칙이 없으면 `06-followup-fluent-bit.md`는 `bit`으로, `scaling-and-ha.md`는 `ha`로 짝지어집니다. 둘 다 하이픈 뒤 소문자 세 글자이고, 둘 다 헛것입니다. 실제 저장소는 번역본처럼 생긴 파일명으로 가득합니다.
- 미러를 두지 않는 디렉터리에는 **없다고 말하지 않습니다.** 단일 언어 저장소는 "쌍 없음" 한 줄만 받고 끝납니다.
- 누락은 **그 디렉터리가 실제로 얼마나 미러를 두는지**에 비추어 판정합니다. 15개 중 14개가 번역돼 있으면 나머지 하나는 실수이고, 9개 중 2개면 나머지 7개는 결정입니다. 리포트는 어느 쪽인지 말하고 비율을 함께 보여줍니다. 그 비율이 곧 근거이기 때문입니다.
- `CHANGELOG.md`, `RELEASE.md`, `CONTRIBUTORS.md`, `LICENSE.md`, `CLAUDE.md`에는 미러를 요구하지 않습니다. 앞의 넷은 기계가 쓰고 마지막 하나는 모델이 읽습니다. `README-ko.md` 옆에 `RELEASE-ko.md`를 내놓으라는 도구는 두 번째 실행부터 무시당합니다.

<br/>

## 하지 않는 일

**의미에 대해서는 아무 판단도 하지 않습니다.** 한국어가 영어와 같은 말을 하는지는 용어·어조·정확성이 걸린 사람의 판단입니다. 그걸 추측하는 도구는 없느니만 못하므로, 이 도구는 번역을 거쳐도 변하지 않는 것만 잽니다. 제목은 여전히 제목이고, 표의 행 수는 그대로이며, 코드 펜스 안의 명령도 같습니다. 단어 수와 글자 수는 변하고, 그래서 재지 않습니다.

**"어느 쪽이 낡았나"에는 답하지 않습니다.** 모양이 똑같은 두 파일이 둘 다 1년 묵었을 수 있습니다. 그건 커밋 이력이 있어야 답할 수 있고, [`census`](../census)가 이미 합니다 — `drift` 커맨드가 각 반쪽이 마지막으로 손댄 시점을 비교합니다. 이 도구는 **담고 있는 내용**을 비교합니다. 둘 다 돌리면 서로 유용하게 어긋납니다.

**아무것도 쓰지 않습니다.** 없는 미러를 만들지도, 고아를 지우지도, 파일 이름을 바꾸지도 않습니다. 미러를 만든다는 건 어떤 언어로 글을 쓴다는 뜻이고, 그건 사람이 할 결정입니다.

<br/>

## 값이 쌀 때 찔러 주는 훅

리포트는 **이미** 어긋난 쌍을 찾습니다. 번들 훅은 애초에 덜 어긋나게 하려고 있습니다.

`Edit`·`Write`·`MultiEdit` 뒤마다 돌고, 같은 세션에서 다른 반쪽을 건드리지 않은 채 한쪽만 바꾸면 그 사실을 말해 줍니다.

```
doc-mirror: edited the source document README.md but its mirror (README-ko.md)
was not edited this session — the pair may now be out of step.
```

Claude Code의 `PostToolUse` 이벤트는 상태가 없어서, "이번 세션에 짝을 고쳤나"는 세션 트랜스크립트를 읽어 편집 도구가 호출된 `file_path`를 전부 모으는 방식으로 답합니다. 그 집합에 짝이 있으면 훅은 침묵합니다.

설계상 **경고 전용**입니다. `PostToolUse`는 도구가 실행된 뒤에 발동하므로 되돌리는 것도 다시 묻는 것도 없습니다 — 다른 반쪽이 아직 머릿속에 있을 때 알림이 도착할 뿐입니다. 불확실하면 전부 침묵합니다. 파싱 안 되는 입력, 못 읽는 트랜스크립트, 목록을 못 얻는 디렉터리 모두 조용히 넘어갑니다. 매 편집마다 헛발질하는 알림은 플러그인 전체를 삭제당하게 만듭니다.

<br/>

## `<br/>` 간격, 그게 이 저장소의 스타일이라면

렌더된 페이지에 숨통을 틔우려고 제목 섹션 사이에 `<br/>`를 넣는 프로젝트가 있습니다.

```markdown
... 앞 섹션의 끝 ...

<br/>

## 다음 제목
```

이건 Markdown의 규칙이 아니라 그 집의 스타일이라, **이미 지키고 있는 곳에서만** 검사합니다. 쌍에 들어 있는 섹션 제목의 80% 이상이 이미 간격을 두고 있는지로 추론하고, 그 판단이 의미를 가질 만큼 제목이 충분할 때만 켭니다. `--spacer on` / `--spacer off`로 추론을 덮어쓸 수 있습니다.

<br/>

## 스크립트 직접 실행하기

번들 스크립트 하나입니다. python3 **표준 라이브러리만** 쓰며 설치 단계가 없습니다.

```bash
python3 scripts/docmirror.py                      # 현재 디렉터리 검사
python3 scripts/docmirror.py ~/code/api           # 다른 곳
python3 scripts/docmirror.py --json               # 결과를 데이터로
python3 scripts/docmirror.py --strict             # critical 이 하나라도 있으면 exit 1
python3 scripts/docmirror.py --spacer on          # 간격 규칙을 무조건 적용
```

`--strict`가 CI용 형태입니다. 배포 전 게이트로 쓰면 "번역 문서 하나가 뒤에 남겨졌나"라는 질문 하나에 답하고, 쌍 150개짜리 저장소에서 1초쯤 걸립니다.

역할을 나눈 건 의도적이고, 이 마켓플레이스의 다른 플러그인과 같은 방식입니다. 스크립트는 재고, 스킬은 해석합니다. 제목을 세는 일은 모델이 할 일이 아닙니다. drift 6건이 전부 `_deprecated/` 아래 몰려 있으니 무시해도 된다고 말하는 쪽이 모델의 일입니다.

CI가 돌리는 검사는 직접 실행할 수 있는 파일이기도 합니다: `bash plugins/doc-mirror/tests/run.sh`.

<br/>

## 절대 하지 않는 것

- 찾아낸 파일을 편집·생성·이름변경·삭제하지 않습니다.
- 번역하지 않고, 번역이 잘됐는지 판단하지 않습니다.
- 네트워크 요청을 하지 않습니다.
- 편집을 막지 않습니다. 훅은 경고 전용이고 그럴 수밖에 없습니다 — `PostToolUse`는 이미 실행된 뒤에 돕니다.

<br/>

## 릴리스

이 마켓플레이스의 플러그인은 각자 버전을 매기고 따로 릴리스합니다. `doc-mirror`의 변경 이력은 — 커밋이 이 디렉터리로 한정된 채 — [doc-mirror releases](https://github.com/somaz94/claude-plugins/releases?q=doc-mirror&expanded=true)에 있습니다.

<br/>

## 라이선스

MIT — [LICENSE](../../LICENSE)를 참고하세요.
