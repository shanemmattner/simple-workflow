---
name: shftty-android
description: Domain-specific 5-phase workflow for the shftty Android app (Kotlin/Jetpack Compose healthcare staffing)
type: code

repo: shanemmattner/shftty-android
repo_path: repos/shftty-android

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

gates:
  triage:
    - decision_keyword_present
  execute:
    - commits_on_branch
    - build_passes
  review:
    - verdict_keyword_present
---

# shftty-android

Domain-specific issue-to-PR workflow for the shftty Android app (Kotlin/Jetpack Compose healthcare staffing).

## Run

```
python -m engine shanemmattner/shftty <issue> --workflow shftty-android
```

## Reusable

The `build_passes` execute gate (gradlew assembleDebug) is portable to any Android/Gradle project. The triage/review keyword-signal pattern works for any code workflow with text-based decision output.
