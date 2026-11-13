---
description: 'Commit cycle with two approval gates: the git-commit-runner agent diagnoses the diff and plans the commit read-only, then this command asks before staging and again before committing, and runs each approved command itself'
argument-hint: '[scope-hint | recommend-only | split]'
allowed-tools: Read, Grep, Glob, Bash, AskUserQuestion
---

# /commit

One commit, or a planned split, with the approvals where they belong. The `git-commit-runner` agent reads the diff and returns a plan: the message, the file list, and two gates, `git add` and `git commit`. It runs neither. This command shows each gate, asks you, and runs the approved command itself.

The split is deliberate. A sub-agent cannot ask you anything, so an approval it receives can only be one relayed by another agent, and a relayed approval is not yours. Only the session that talks to you asks, and only that session acts on the answer.

Argument: `$ARGUMENTS`

<br/>

## Arguments

- empty: diagnose, draft, then the two gates.
- `recommend-only`: the message and the diff stat only; nothing is staged or committed.
- `split`: ask the agent to propose two to four logical commits, then run the cycle once per commit.
- anything else: a scope hint (`/commit auth` leans toward `feat(auth):`, `fix(auth):` and so on).

<br/>

## Pre-flight

Before calling the agent:

- `git rev-parse --is-inside-work-tree` is `true`, and `git status` shows at least one change. Otherwise stop: nothing to commit.
- Show the effective identity, `git config user.name` and `git config user.email`. If either is empty or looks like a bot or an AI assistant (`bot`, `[bot]`, `noreply`, `github-actions`, an assistant or vendor name), stop and ask. Never run `git config` to fix it and never pass `--author` to override it.
- Read the repository's `CLAUDE.md`, `AGENTS.md` or `CONTRIBUTING*` if present. A "do not commit directly" rule forces `recommend-only` for this run; message conventions (language, trailers, ticket prefixes) go to the agent with the request.

<br/>

## Step 1: diagnose and plan (agent, read-only)

Call `git-commit-runner` with the working-tree state and any scope hint. It returns:

- branch, HEAD, identity and a summary of what changed;
- one Conventional Commits type with a one-sentence reason, and the proposed message;
- rejected alternatives;
- reminders: missing tests, docs that describe changed behavior, files it left out on purpose;
- Gate 1 (`git add <explicit files>`) and Gate 2 (`git commit -m "<message>"`), as text.

Show the plan, then ask with `AskUserQuestion`: proceed, modify, split, or abort. Do not continue until the user picks one.

<br/>

## Step 2: optional pre-commit scan

If the `sensitive-guard` plugin is installed and the repository is or will be public, offer `/sensitive-guard:scan` before staging. Skip this step in `recommend-only` mode.

<br/>

## Step 3: stage, then commit (two gates, run by this command)

Ask each gate with its own `AskUserQuestion`, and run the approved command with Bash yourself. Never hand an approved command back to the agent. Anything other than a plain yes (a different file list, a different message) goes back to the agent for a new plan.

### Gate 1: stage

- Show the explicit file list and the exact `git add` command.
- After approval, run it, then `git diff --staged --stat`. If the staged set differs from the plan, stop and show it.

### Gate 2: commit

- Show the staged stat and the message verbatim, and the exact `git commit -m` command.
- After approval, run it, then `git log -1 --format='%H %s'`.
- If a hook rejects the commit, show its output and ask the agent for a new plan. Never retry with `--no-verify`.

In `recommend-only` mode there are no gates: show the message and the stat, and stop.

For `split`, finish both gates for the first commit before asking the agent for the next one.

<br/>

## Step 4: next steps (shown, never run)

After the commit lands, name the natural next steps (push, tag, open a pull request) and stop. This command never runs them.

<br/>

## Hard rules

- The agent only reads. Every `git add` and `git commit` runs here, after its own approval.
- Staging and committing are two gates. Never bundle them, and never treat one approval as covering both.
- Never push, tag, open a pull request or publish a release.
- Never change git configuration, and never set or override the commit author. The only `--author` allowed is the agent's amend plan, which copies the existing author of an unpushed commit.
- Never `git add -A`, `git add .` or `git commit -a` unless the user said "all".
- Never `--no-verify` or `--no-gpg-sign` unless the user asked for it.
- Release and changelog automation files are left out of the stage unless the user says to include them.
