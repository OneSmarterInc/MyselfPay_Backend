# Working rules for Claude in this repository

Vikram Sethi is the architect and decides when code ships. These rules make his job one word.

## The two words

- **push**: Vikram types "push". Claude commits and pushes the current `claude/` branch, opens the PR if none exists, and reports CI.
- **merge**: Vikram types "merge" (naming the PR, or with one PR in play). Claude then does everything else: checks the base branch, waits for CI to pass, merges, deletes the merged branch when the proxy allows it, pulls main, and reports in one short message.

Claude never merges or pushes on any other message: a question, a screenshot, a garbled line, or "looks good". If the message is unclear, Claude asks one short question.

## Before every merge, Claude checks and states

1. CI is green on the PR head.
2. The base branch is correct (feature PRs go to the stated base; Claude never retargets).
3. The branch is up to date with its base and has no conflicts.
4. The PR body lists any manual step (database change, scheduler, environment variable) and whether it is done.

If a manual step must come first, Claude does not merge until Vikram types **approve db** for that change record. Then Claude applies nothing itself: Vikram or the named operator runs the hosted-database step after a verified backup.

## Always in force

- Branches Claude creates start with `claude/`. Claude never pushes straight to `main`.
- Every write to a live database needs a change record, a verified backup, and Vikram's "approve db".
- Production deploys come from `main`, and Vikram starts them.
- Test data is synthetic. Secrets and tokens are never printed or committed.
- After a merge, only `main` (and any named base branch) stays. Claude deletes the merged `claude/` branch.

## When Claude makes a mistake

Claude says so first, states the impact, and offers the fix. It does not revert without Vikram's word.
