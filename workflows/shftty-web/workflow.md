---
name: shftty-web
description: Issue-to-PR workflow for the shftty healthcare staffing web app (Next.js + Supabase monorepo).
type: code

repo: shanemmattner/shftty
repo_path: repos/shftty

budget:
  max_per_run_usd: 10.00

phases:
  - name: triage
    model: sonnet
    max_turns: 30
    prompt: prompts/triage.md
    decision_signal:
      section: "## Decision"
      keywords:
        proceed: "PROCEED"
        skip: "SKIP:"
        escalate: "ESCALATE:"
    on_skip: post_issue_comment
    on_escalate: post_issue_comment

  - name: plan
    model: sonnet
    max_turns: 20
    prompt: prompts/plan.md
    requires: [triage]

  - name: execute
    model: sonnet
    max_turns: 50
    prompt: prompts/execute.md
    requires: [triage, plan]

  - name: review
    model: sonnet
    max_turns: 20
    prompt: prompts/review.md
    requires: [triage, plan, execute]
    verdict_signal:
      section: "## Verdict"
      keywords:
        pass: "PASS"
        warn: "WARN"
        fail: "FAIL"

  - name: improve
    model: sonnet
    max_turns: 10
    prompt: prompts/improve.md
    optional: true
    requires: [triage, plan, execute, review]

template_variables:
  - repo_context
  - issue_body
  - issue_number
  - recent_learnings
  - prior_phases
---

# shftty-web

Issue-to-PR workflow for the shftty healthcare staffing web app (Next.js + Supabase monorepo).

## Run

```
./scripts/run.sh workflows/shftty-web shanemmattner/shftty#<issue>
```

## Reusable

The triage step-decomposition pattern and execute-step.md template work for any Next.js monorepo. The PROCEED/SKIP/ESCALATE decision signal and PASS/WARN/FAIL verdict signal are portable to any code workflow.
