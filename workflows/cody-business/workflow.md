---
name: cody-business
description: Professional communications, business development, CTO/advisory role management. Ops workflow — no git worktree, no GitHub source/destination.
type: ops

budget:
  max_per_run_usd: 100.00

models:
  sonnet:
    name: claude-sonnet-4-6
    max_tokens: 16384
    cost: {input_per_mtok: 3.00, output_per_mtok: 15.00}
  haiku:
    name: claude-haiku-4-5
    max_tokens: 8192
    cost: {input_per_mtok: 0.80, output_per_mtok: 4.00}

phases:
  - name: triage
    model: sonnet
    max_turns: 10
    prompt: prompts/triage.md
    decision_signal:
      section: "## Decision"
      keywords:
        proceed: "PROCEED"
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
  - workstream_context
  - triage_output
  - execute_output
  - review_output
---

# cody-business

Ops workflow for professional communications, business development, and CTO/advisory role management. No git worktree — output is documents and emails, not PRs.

## Run

```
python -m engine --workflow cody-business --task "Draft a response to Acme's partnership proposal"
```

## Reusable

The triage → execute → review → improve ops pattern (no GitHub source/destination) is the baseline for all non-code task workflows. The `workstream_context` template variable injection pattern is portable to any advisory/comms workflow.
