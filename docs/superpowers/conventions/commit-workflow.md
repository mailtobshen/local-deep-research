# Commit Workflow Convention

**Date:** 2026-07-26
**Scope:** All commits on this repository (`local-deep-research`). Applies to every session and every agent/subagent that commits.

## 1. Single active branch

`main` is the only active branch. There is no per-feature branch, no long-lived working branch, and no PR branch in normal flow. Commits land directly on `main`.

Rationale: this repo is advanced by directly committing to `main`. A prior session produced a commit landed on the wrong branch because the working tree was on a stale branch. Pinning to `main` removes that failure mode.

## 2. Pre-commit branch check (mandatory)

Before creating ANY commit, verify the current branch:

```bash
git rev-parse --abbrev-ref HEAD
```

- If the output is `main`, proceed.
- If the output is anything else, STOP. Do not commit. Either check out `main` first (when the changes belong on `main`) or surface the situation to the user before doing anything.
- Do not rely on memory of which branch was checked out at session start — the working tree can drift; re-check every time.

This check runs **in addition to** the harness's built-in git-safety behaviors; it does not replace them.

## 3. Foreground execution only

Do NOT run git operations with `run_in_background: true`. Concretely:

- `git commit`, `git add`, `git push`, `git checkout`, `git reset`, and any `git rebase`/`merge` MUST run in the foreground (synchronous, blocking).
- Reason: background execution decouples the commit from the session's reasoning state, and a commit that lands after the turn ends is invisible to subsequent verification — exactly the class of error that caused the prior misfire.
- The only legitimate background runs are long, non-git, non-mutating tasks (builds, test suites, scans), which are unrelated to this rule.

## 4. Post-commit verification (mandatory)

After EVERY commit, immediately confirm it landed as expected:

```bash
git log --oneline -3
```

Verify:

1. The new commit is at `HEAD` (top line).
2. The commit message matches what was intended.
3. The branch column / `git rev-parse --abbrev-ref HEAD` still reports `main`.

If any of these is wrong, stop and surface it before doing further work — do not "fix it with another commit" silently.

## 5. Quick reference

```bash
# 1. check branch
git rev-parse --abbrev-ref HEAD        # must print: main
# 2. stage + commit (foreground, never backgrounded)
git add <paths>
git commit -m "..."
# 3. verify
git log --oneline -3
```

## 6. When this rule is violated

If you discover a commit landed on a non-`main` branch, or a commit is missing from `git log` after a commit command returned success:

1. Stop. Do not attempt to paper over it with a follow-up commit.
2. Run `git status`, `git log --oneline -5 --all`, and `git rev-parse --abbrev-ref HEAD`.
3. Surface the exact state to the user and ask how to proceed before taking any corrective action.
