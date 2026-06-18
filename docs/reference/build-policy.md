You are implementing {repo} issue #{issue_number}. Read it first with
`gh issue view {issue_number} --repo {repo_slug}` — it is the spec.

### Scope exclusions

Do NOT build any of the following — they belong to other issues:
{scope_exclusions}

### Where you work

- Worktree: `{worktree}`, branch `{branch}` (create from origin/main if it
  does not exist). All work happens there. NEVER touch any other clone of
  this repo — they may carry in-flight branches.
- Read `{worktree}/CLAUDE.md` before writing any code — repo conventions,
  test commands, and deploy notes live there and override anything you
  assume.

### Hard rules

1. Commits look like normal Claude Code-assisted work: conventional-commit
   messages, Co-Authored-By: Claude lines are FINE. What must never appear
   is the orchestration machinery — no references to pipelines, runs,
   stages, queues, or other workstreams in code, comments, commits, issue
   text, or PR text.
2. Never touch `.env` or credentials files. Never commit secrets.
3. PHI rules apply: any table the repo marks as PHI gets an audit_log write
   on every mutation — mirror the repo's existing mutation patterns.
4. Never write from scratch — where a step names a pattern file, open it
   first and mirror its structure.
5. Pre-commit hooks (lefthook) must pass. Do not bypass hooks.
6. Use the repo's local test database and harness exactly as documented in
   the worktree (CLAUDE.md / harness README) — read them before writing any
   test phase.

### Build order

{build_order}

Commit after each numbered step (small conventional commits). When all
green, push the branch and open a PR titled with a conventional-commit
summary referencing #{issue_number}, with a body that summarizes scope and
test results plainly. Do not merge.

### Acceptance test ({test_kind})

{acceptance_test}

Acceptance tests are state contracts, not vibes: assert the exact PRE
state (counts, absence of rows), perform the action with deterministic
fixed inputs, then assert the exact POST state (literal field values,
bounded timestamps, expected-vs-actual printed on every failure). The
proof run happens in a fresh sterile checkout, never in the workspace
that built the code.

Mobile (iOS/Android) only: the ONLY accepted final proof is the test
passing on a physical device. Simulator/emulator results are
provisional. If no device is available, you may still open the PR, but
the PR body MUST state prominently: "Verified on simulator/emulator
only — BLOCKED on physical-device verification before merge." (Shane,
2026-06-10)

### Escalation ladder

1. Ambiguity resolvable from the issue, pattern files, or repo CLAUDE.md
   → resolve it yourself, note the assumption in the PR body.
2. A dependency behaves differently than the spec assumes → STOP that
   step, document exactly what you observed (commands + output), continue
   other independent steps, surface it at the end.
3. Anything requiring a product decision not in the issue → do NOT decide;
   list it under "needs decision" in your final report.
4. Existing tests/harness break in ways unrelated to your change → do not
   "fix" unrelated code; report it.

### Final report format

Plain markdown: what shipped (per build-order item: done/partial/blocked),
acceptance-test output (pasted), full-regression result, assumptions made,
needs-decision list, PR link.
