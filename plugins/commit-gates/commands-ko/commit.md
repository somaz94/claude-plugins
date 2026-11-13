---
description: '승인 게이트 두 개를 둔 커밋 사이클: git-commit-runner agent가 diff를 진단하고 커밋을 읽기 전용으로 계획하면, 이 command가 stage 전에 한 번, 커밋 전에 한 번 묻고 승인된 명령을 직접 실행'
argument-hint: '[scope-hint | recommend-only | split]'
allowed-tools: Read, Grep, Glob, Bash, AskUserQuestion
---

# /commit

> 이 문서는 [commands/commit.md](../commands/commit.md)의 **한국어 번역본**입니다.
> Claude Code가 실제로 불러오는 것은 영어 원본이며, 이 KO 본은 참고와 사용자 리뷰용입니다.
> 고칠 때는 EN과 KO를 함께 고쳐야 합니다.

커밋 하나, 또는 계획된 분할 커밋을, 승인이 있어야 할 자리에 승인을 두고 만듭니다. `git-commit-runner` agent는 diff를 읽고 계획을 돌려줍니다. 계획에는 메시지, 파일 목록, 게이트 두 개(`git add`, `git commit`)가 들어 있고, agent는 둘 다 실행하지 않습니다. 이 command가 게이트마다 보여 주고, 묻고, 승인된 명령을 직접 실행합니다.

이렇게 나눈 데는 이유가 있습니다. sub-agent는 사용자에게 아무것도 물을 수 없으므로, sub-agent가 받는 승인은 다른 agent가 전달한 것일 수밖에 없고, 전달된 승인은 사용자의 승인이 아닙니다. 사용자와 대화하는 세션만이 묻고, 그 세션만이 답에 따라 행동합니다.

인자: `$ARGUMENTS`

<br/>

## 인자

- 비어 있음: 진단, 초안, 그다음 게이트 두 개.
- `recommend-only`: 메시지와 diff stat만. stage도 커밋도 하지 않습니다.
- `split`: agent에게 논리적 커밋 2~4개를 제안받고, 커밋마다 사이클을 한 번씩 돕니다.
- 그 밖의 값: scope 힌트(`/commit auth`는 `feat(auth):`, `fix(auth):` 쪽으로 기웁니다).

<br/>

## 사전 점검

agent를 부르기 전에:

- `git rev-parse --is-inside-work-tree`가 `true`이고 `git status`에 변경이 하나 이상 있어야 합니다. 아니면 멈춥니다. 커밋할 것이 없습니다.
- 실제 작성자 `git config user.name`과 `git config user.email`을 보여 줍니다. 둘 중 하나가 비어 있거나 봇이나 AI 어시스턴트처럼 보이면(`bot`, `[bot]`, `noreply`, `github-actions`, 어시스턴트나 공급사 이름) 멈추고 묻습니다. 고치려고 `git config`를 실행하지 않고, 덮어쓰려고 `--author`를 넘기지 않습니다.
- 저장소에 `CLAUDE.md`, `AGENTS.md`, `CONTRIBUTING*`이 있으면 읽습니다. "직접 커밋하지 말라"는 규칙이 있으면 이번 실행은 `recommend-only`가 됩니다. 메시지 관례(언어, trailer, 티켓 접두사)는 요청과 함께 agent에 넘깁니다.

<br/>

## 1단계: 진단과 계획(agent, 읽기 전용)

작업 트리 상태와 scope 힌트를 붙여 `git-commit-runner`를 부릅니다. agent가 돌려주는 것:

- 브랜치, HEAD, 작성자, 바뀐 것 요약
- Conventional Commits 타입 하나와 한 문장 이유, 제안 메시지
- 버린 대안
- 알림: 빠진 테스트, 바뀐 동작을 설명하는 문서, 일부러 뺀 파일
- Gate 1(`git add <명시한 파일>`)과 Gate 2(`git commit -m "<메시지>"`) 텍스트

계획을 보여 준 뒤 `AskUserQuestion`으로 묻습니다. 진행, 수정, 분할, 중단 중 하나입니다. 사용자가 고르기 전에는 넘어가지 않습니다.

<br/>

## 2단계: 커밋 전 스캔(선택)

`sensitive-guard` 플러그인이 설치돼 있고 저장소가 공개됐거나 공개될 예정이면, stage 전에 `/sensitive-guard:scan`을 권합니다. `recommend-only` 모드에서는 건너뜁니다.

<br/>

## 3단계: stage, 그다음 커밋(이 command가 실행하는 게이트 두 개)

게이트마다 `AskUserQuestion`을 따로 쓰고, 승인된 명령은 Bash로 직접 실행합니다. 승인된 명령을 agent에게 되넘기지 않습니다. 단순한 "예"가 아닌 답(다른 파일 목록, 다른 메시지)은 agent에게 돌려보내 새 계획을 받습니다.

### Gate 1: stage

- 명시한 파일 목록과 정확한 `git add` 명령을 보여 줍니다.
- 승인되면 실행하고 `git diff --staged --stat`을 봅니다. stage된 묶음이 계획과 다르면 멈추고 보여 줍니다.

### Gate 2: 커밋

- staged stat, 메시지 원문, 정확한 `git commit -m` 명령을 보여 줍니다.
- 승인되면 실행하고 `git log -1 --format='%H %s'`를 봅니다.
- hook이 커밋을 거부하면 출력을 보여 주고 agent에게 새 계획을 받습니다. `--no-verify`로 다시 시도하지 않습니다.

`recommend-only` 모드에는 게이트가 없습니다. 메시지와 stat을 보여 주고 멈춥니다.

`split`이면 첫 커밋의 게이트 두 개를 끝낸 뒤에 agent에게 다음 커밋을 요청합니다.

<br/>

## 4단계: 다음 단계(보여 주기만, 실행하지 않음)

커밋이 들어가면 자연스러운 다음 단계(push, 태그, pull request 열기)를 알려 주고 멈춥니다. 이 command는 그것들을 실행하지 않습니다.

<br/>

## 하드 룰

- agent는 읽기만 합니다. 모든 `git add`와 `git commit`은 각자의 승인을 받은 뒤 여기서 실행됩니다.
- stage와 커밋은 게이트 두 개입니다. 묶지 않고, 승인 하나가 둘 다를 덮는다고 보지 않습니다.
- push, 태그, pull request 열기, 릴리스 게시는 하지 않습니다.
- git 설정을 바꾸지 않고, 커밋 작성자를 지정하거나 덮어쓰지 않습니다. 허용되는 `--author`는 agent의 amend 계획뿐이며, push하지 않은 커밋의 기존 작성자를 복사합니다.
- 사용자가 "전부"라고 하지 않는 한 `git add -A`, `git add .`, `git commit -a`를 쓰지 않습니다.
- 사용자가 요청하지 않는 한 `--no-verify`나 `--no-gpg-sign`을 쓰지 않습니다.
- 릴리스와 변경 이력 자동화 파일은 사용자가 넣으라고 하지 않는 한 stage에서 뺍니다.
