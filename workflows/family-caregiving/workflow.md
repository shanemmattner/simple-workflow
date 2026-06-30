---
name: family-caregiving
description: Alzheimer's caregiving coordination — facility research, caregiver search, call prep, legal/financial tracking
type: ops

budget:
  max_per_run_usd: 100.00

phases:
  - name: triage
    model: sonnet
    max_turns: 10
    prompt: prompts/triage.md
    decision_signal:
      section: "## Decision"
      keywords:
        proceed: "PROCEED"
        skip: "SKIP:"
        escalate: "ESCALATE:"

  - name: execute
    model: sonnet
    max_turns: 30
    prompt: prompts/execute.md
    requires: [triage]

  - name: review
    model: sonnet
    max_turns: 10
    prompt: prompts/review.md
    requires: [triage, execute]
    verdict_signal:
      section: "## Verdict"
      keywords:
        pass: "PASS"
        warn: "WARN"
        fail: "FAIL"

  - name: improve
    model: sonnet
    max_turns: 15
    prompt: prompts/improve.md
    optional: true
    requires: [triage, execute, review]

template_variables:
  - task_description
  - prior_phases
  - recent_learnings
---

# family-caregiving

Ops workflow for Alzheimer's caregiving coordination — facility research, caregiver search, call prep, and legal/financial tracking.

## Run

```
python -m engine --workflow family-caregiving --task "Research memory care facilities in the East Bay under $8k/month"
```

## Reusable

The 3-signal triage pattern (PROCEED/SKIP/ESCALATE) with a `## Decision` section is portable to any research or coordination ops workflow. The lightweight template_variables set (task_description, prior_phases, recent_learnings) is the minimal viable context for ops tasks.
