---
name: steadion-deal
description: Deal management workflow for the Steadion CTO engagement — NDA review, counter-proposals, due diligence, and meeting prep. Ops workflow — no git worktree, no GitHub source/destination.
type: ops
workflow_type: ops

budget:
  max_per_run_usd: 50.00

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
        skip: "SKIP"
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

template_variables:
  - task_description
  - prior_phases
  - recent_learnings
  - workstream_context
  - triage_output
  - execute_output
---

# steadion-deal

Ops workflow for the Steadion CTO deal — NDA review, counter-proposal drafting, due diligence prep, and meeting briefs. No git worktree — output is documents and email drafts, not PRs.

## Run

```
python -m engine --workflow steadion-deal --task "Draft the equity counter to Ron at Steadion"
```

## Reusable

The triage → execute → review ops pattern (no GitHub source/destination) is shared with cody-business. The deal-specific task types (NDA_REVIEW, COUNTER_PROPOSAL, DUE_DILIGENCE, MEETING_PREP, EMAIL_DRAFT) can be adapted for any advisory engagement. The commitment-check and NDA-safety gates in review.md are portable to any deal workflow.
