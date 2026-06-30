---
name: shftty-ios
description: Domain-specific workflow for the shftty iOS app (Swift/SwiftUI healthcare staffing)
type: code

repo: shanemmattner/shftty-ios
repo_path: repos/shftty-ios

budget:
  max_per_run_usd: 10.00

models:
  haiku:
    name: claude-haiku-4-5
    max_tokens: 8192
    cost: {input_per_mtok: 0.80, output_per_mtok: 4.00}
  sonnet:
    name: claude-sonnet-4-6
    max_tokens: 16384
    cost: {input_per_mtok: 3.00, output_per_mtok: 15.00}
  opus:
    name: claude-opus-4-6
    max_tokens: 16384
    cost: {input_per_mtok: 15.00, output_per_mtok: 75.00}
  m27hs:
    name: MiniMax-M2.7-highspeed
    max_tokens: 16384
    cost: {input_per_mtok: 0.20, output_per_mtok: 0.80}
  m3:
    name: MiniMax-M3
    max_tokens: 16384
    cost: {input_per_mtok: 0.30, output_per_mtok: 1.20}

phases:
  - name: triage
    model: sonnet
    max_turns: 30

  - name: execute
    model: sonnet
    max_turns: 50

  - name: review
    model: sonnet
    max_turns: 20

  - name: validate
    model: sonnet
    max_turns: 10
    optional: true

  - name: improve
    model: opus
    max_turns: 30

decision_parsing:
  triage:
    section: "## Decision"
    signals:
      proceed: "PROCEED"
      skip: "SKIP:"
      escalate: "ESCALATE:"
    default: "proceed"
  review:
    section: "## Verdict"
    signals:
      pass: "PASS"
      warn: "WARN"
      fail: "FAIL"
    default: "pass"

gates:
  triage:
    - decision_present
    - decision_valid
  execute:
    - commits_on_branch
  review:
    - verdict_present
    - build_gate_logged

template_vars:
  - issue_number
  - issue_body
  - repo_context
  - recent_learnings
  - prior_phases
---

# shftty-ios

Domain-specific issue-to-PR workflow for the shftty iOS app (Swift/SwiftUI healthcare staffing).

## Run

```
python -m engine shanemmattner/shftty <issue> --workflow shftty-ios
```

## Reusable

The triage/execute/review/improve phase pattern and decision_parsing config are portable to any Swift/SwiftUI project. The `commits_on_branch` execute gate works for any git-based code workflow.
