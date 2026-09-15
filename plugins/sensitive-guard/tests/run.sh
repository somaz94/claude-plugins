#!/usr/bin/env bash
# sensitive-guard bundled-script tests
#
# This plugin is the only one here that can STOP you working: the hook returns
# exit 2 and the commit does not happen. Both directions of failure are silent
# and expensive — a miss puts a secret in a public repo, and a false positive
# wedges every commit in that repo until someone finds the hook. So the suite
# pins the exemptions as hard as it pins the block.
#
# Run directly (`bash plugins/sensitive-guard/tests/run.sh`) or through the whole
# suite (`bash tests/run.sh`). CI calls this same file.
set -euo pipefail
. "$(dirname "${BASH_SOURCE[0]:-$0}")/../../../tests/lib.sh"

PLUGIN="$ROOT/plugins/sensitive-guard"
HOOK="$PLUGIN/hooks/pre-commit-sensitive-scan.sh"
SCANNER="$PLUGIN/scripts/find-sensitive.sh"

# A value the scanner recognises out of the box, so the fixtures do not depend on
# anyone's personal markers. Split so this file never contains the literal token
# shape a scanner would flag when this very repo is scanned.
SECRET_KEY="AKIA""IOSFODNN7EXAMPLE"
SECRET_PW='password = "hunter2-not-a-real-password"'

# Build a git repo that has opted in, with a clean committed baseline.
# Prints the repo path.
new_repo() {
  local dir
  dir="$(mktemp -d)/${1:-repo}"
  mkdir -p "$dir"
  git -C "$dir" init -q
  git -C "$dir" config user.email t@example.com
  git -C "$dir" config user.name tester
  git -C "$dir" config commit.gpgsign false
  : > "$dir/.sensitive-patterns"
  printf 'clean baseline\n' > "$dir/README.md"
  git -C "$dir" add -A
  git -C "$dir" commit -qm baseline
  printf '%s' "$dir"
}

# Run the hook the way Claude Code does: PreToolUse JSON on stdin. Echoes the
# hook's stderr and returns its exit code.
run_hook() {
  local cwd="$1" command="$2"
  python3 -c '
import json, sys
print(json.dumps({"tool_input": {"command": sys.argv[2]}, "cwd": sys.argv[1]}))
' "$cwd" "$command" | CLAUDE_PLUGIN_ROOT="$PLUGIN" bash "$HOOK" 2>&1
}

# Assert the hook exits with `want` for `command`, and say what it was proving.
expect_hook() {
  local want="$1" cwd="$2" command="$3" what="$4" out rc
  set +e
  out="$(run_hook "$cwd" "$command")"
  rc=$?
  set -e
  if [ "$rc" != "$want" ]; then
    echo "FAIL: $what — expected exit $want, got $rc"
    [ -n "$out" ] && printf '%s\n' "$out"
    return 1
  fi
  HOOK_OUT="$out"
  return 0
}

step 'the guard blocks a secret in the lines a commit adds'
(
set -euo pipefail
repo="$(new_repo)"
printf '%s\n' "$SECRET_PW" > "$repo/config.env"
git -C "$repo" add -A

expect_hook 2 "$repo" "git commit -m 'add config'" "a staged secret" || exit 1

# The transcript must carry the category, never the value: the whole point of
# invoking the scanner with -q is that the secret does not get copied into a
# conversation log on its way to being reported.
case "$HOOK_OUT" in
  *BLOCKED:*) : ;;
  *) echo "FAIL: no BLOCKED line in the hook output"; printf '%s\n' "$HOOK_OUT"; exit 1 ;;
esac
case "$HOOK_OUT" in
  *generic_secret_assignment*) : ;;
  *) echo "FAIL: the category that fired is not named"; printf '%s\n' "$HOOK_OUT"; exit 1 ;;
esac
if printf '%s' "$HOOK_OUT" | grep -qF 'hunter2-not-a-real-password'; then
  echo "FAIL: the secret itself was echoed into the transcript"
  exit 1
fi
echo "PASS: blocked with the category named and the value withheld"
)

step 'the guard stays out of the way of a clean commit'
(
set -euo pipefail
repo="$(new_repo)"
printf 'nothing to see\n' > "$repo/notes.md"
git -C "$repo" add -A

expect_hook 0 "$repo" "git commit -m 'add notes'" "a clean staged change" || exit 1
if [ -n "$HOOK_OUT" ]; then
  echo "FAIL: a clean commit produced output: $HOOK_OUT"
  exit 1
fi
echo "PASS: silent on a clean commit"
)

step 'the guard gates what a commit adds, not what the repo already holds'
(
set -euo pipefail
# The behaviour that decides whether this hook is usable at all. A repo that
# already contains a flagged value — an upstream chart's demo password, a
# vendored example config — would otherwise block EVERY commit forever, and a
# guard that always fires gets uninstalled the same day.
repo="$(new_repo)"
printf '%s\n' "$SECRET_PW" > "$repo/legacy.env"
git -C "$repo" add -A
git -C "$repo" commit -qm 'pre-existing secret, already in history'

printf 'a new and entirely innocent line\n' >> "$repo/README.md"
git -C "$repo" add -A
expect_hook 0 "$repo" "git commit -m 'unrelated edit'" "an innocent edit beside an old secret" || exit 1

# ...and the same repo must still block when the NEW lines carry one.
printf '%s\n' "AWS_SECRET_ACCESS_KEY=$SECRET_KEY" >> "$repo/README.md"
git -C "$repo" add -A
expect_hook 2 "$repo" "git commit -m 'add a key'" "a newly added key" || exit 1

echo "PASS: pre-existing values are ignored, newly added ones still block"
)

step 'the guard follows commit -a out to the worktree'
(
set -euo pipefail
# `commit -a` folds in unstaged edits to tracked files, so a scan of the index
# alone would look clean and let the secret through.
repo="$(new_repo)"
printf '%s\n' "AWS_SECRET_ACCESS_KEY=$SECRET_KEY" >> "$repo/README.md"   # tracked, NOT staged

expect_hook 0 "$repo" "git commit -m 'nothing staged'" "an unstaged edit under a plain commit" || exit 1
expect_hook 2 "$repo" "git commit -am 'sweep it in'" "the same edit under commit -a" || exit 1

echo "PASS: -a widens the scan to the worktree, a plain commit does not"
)

step 'the guard reads the repo out of the command, not just the cwd'
(
set -euo pipefail
repo="$(new_repo)"
printf '%s\n' "$SECRET_PW" > "$repo/config.env"
git -C "$repo" add -A
elsewhere="$(mktemp -d)"

# Both forms name a repo that is not the cwd. Missing either one means the hook
# silently allows exactly the commits a person runs from a parent directory.
expect_hook 2 "$elsewhere" "git -C $repo commit -m x" "git -C <dir> commit" || exit 1
expect_hook 2 "$elsewhere" "cd $repo && git commit -m x" "cd <dir> && git commit" || exit 1

echo "PASS: -C and a cd chain both resolve to the right repo"
)

step 'the guard ignores everything that is not a commit'
(
set -euo pipefail
repo="$(new_repo)"
printf '%s\n' "$SECRET_PW" > "$repo/config.env"
git -C "$repo" add -A

# `git log` matches the payload's fast-path substring test for "commit" without
# being one, which is exactly the case a looser check would get wrong.
for command in "git status" "ls -la" "git log --format=%H" "git diff --cached"; do
  expect_hook 0 "$repo" "$command" "the command '$command'" || exit 1
done
echo "PASS: status, ls, log and diff all pass through untouched"
)

step 'the guard fires only in a repo that opted in'
(
set -euo pipefail
repo="$(new_repo)"
rm "$repo/.sensitive-patterns"
printf '%s\n' "$SECRET_PW" > "$repo/config.env"
git -C "$repo" add -A

# No marker file, no gate. A guard that fires in repos nobody armed is a guard
# that gets uninstalled rather than configured.
expect_hook 0 "$repo" "git commit -m x" "a repo with no .sensitive-patterns" || exit 1
echo "PASS: no opt-in file, no gate"
)

step 'the guard exempts the repos that are allowed to hold real values'
(
set -euo pipefail
for name in vault-private notes.wiki; do
  repo="$(new_repo "$name")"
  printf '%s\n' "$SECRET_PW" > "$repo/config.env"
  git -C "$repo" add -A
  expect_hook 0 "$repo" "git commit -m x" "a repo named $name" || exit 1
done

# An external OSS fork: the flagged value belongs to the upstream project, and a
# contribution must leave those files byte-for-byte intact. Detected offline
# from the two remotes' owners, so this costs nothing and needs no network.
repo="$(new_repo)"
git -C "$repo" remote add origin git@github.com:me/theirproject.git
git -C "$repo" remote add upstream git@github.com:someoneelse/theirproject.git
printf '%s\n' "$SECRET_PW" > "$repo/values.yaml"
git -C "$repo" add -A
expect_hook 0 "$repo" "git commit -m x" "a fork whose upstream owner differs" || exit 1

# ...but a normal single-remote repo must NOT be exempted by the same code path.
own="$(new_repo)"
git -C "$own" remote add origin git@github.com:me/myproject.git
printf '%s\n' "$SECRET_PW" > "$own/values.yaml"
git -C "$own" add -A
expect_hook 2 "$own" "git commit -m x" "a repo with only an origin" || exit 1

echo "PASS: -private, .wiki and forks exempt; an ordinary repo still gated"
)

step 'the guard fails open when it cannot scan'
(
set -euo pipefail
# A broken or missing scanner must not wedge every commit in every repo. Warn
# (exit 1) and let it through — the failure mode of a guard has to be quieter
# than the thing it guards against.
repo="$(new_repo)"
printf '%s\n' "$SECRET_PW" > "$repo/config.env"
git -C "$repo" add -A

hollow="$(mktemp -d)/plugin"
mkdir -p "$hollow/scripts"

set +e
out="$(python3 -c '
import json, sys
print(json.dumps({"tool_input": {"command": "git commit -m x"}, "cwd": sys.argv[1]}))
' "$repo" | CLAUDE_PLUGIN_ROOT="$hollow" bash "$HOOK" 2>&1)"
rc=$?
set -e

if [ "$rc" != 1 ]; then
  echo "FAIL: a missing scanner should warn (1), not block or pass — got $rc"
  printf '%s\n' "$out"
  exit 1
fi
case "$out" in
  *WARN*) : ;;
  *) echo "FAIL: no warning explaining why nothing was scanned"; printf '%s\n' "$out"; exit 1 ;;
esac
echo "PASS: a missing scanner warns and lets the commit through"
)

step 'a private IP alone warns rather than blocking'
(
set -euo pipefail
# RFC1918 is non-routable, so a hardcoded 10.x is at most an internal-topology
# hint. Blocking on it alone would make the guard cry wolf on every homelab
# note; the scanner still reports it, so audit visibility is unaffected.
repo="$(new_repo)"
printf 'the box lives at 10.99.99.99\n' > "$repo/topology.md"
git -C "$repo" add -A

expect_hook 1 "$repo" "git commit -m x" "a lone private IP" || exit 1
case "$HOOK_OUT" in
  *private_ip*) : ;;
  *) echo "FAIL: the downgraded category is not named"; printf '%s\n' "$HOOK_OUT"; exit 1 ;;
esac

# Paired with a real secret it must block again — the downgrade applies only
# when private_ip is the ONLY category that fired.
printf '%s\n' "AWS_SECRET_ACCESS_KEY=$SECRET_KEY" >> "$repo/topology.md"
git -C "$repo" add -A
expect_hook 2 "$repo" "git commit -m x" "a private IP alongside a key" || exit 1

echo "PASS: private_ip alone warns, private_ip plus a secret blocks"
)

step 'the scanner reports per category and says so in its exit code'
(
set -euo pipefail
tree="$(mktemp -d)"
printf 'nothing here\n' > "$tree/fine.md"

set +e
"$SCANNER" -q "$tree" >/dev/null 2>&1
clean_rc=$?
set -e
[ "$clean_rc" = 0 ] || { echo "FAIL: a clean tree should exit 0, got $clean_rc"; exit 1; }

printf '%s\n' "AWS_SECRET_ACCESS_KEY=$SECRET_KEY" > "$tree/creds.env"
set +e
out="$("$SCANNER" -q "$tree" 2>&1)"
dirty_rc=$?
set -e
[ "$dirty_rc" = 1 ] || { echo "FAIL: a match should exit 1, got $dirty_rc"; exit 1; }
case "$out" in
  *aws_key*) : ;;
  *) echo "FAIL: the aws_key category was not reported"; printf '%s\n' "$out"; exit 1 ;;
esac
# -q is what keeps the value out of the caller's transcript.
if printf '%s' "$out" | grep -qF "$SECRET_KEY"; then
  echo "FAIL: -q printed the matched value"
  exit 1
fi
echo "PASS: 0 when clean, 1 with the category named and the value withheld"
)
