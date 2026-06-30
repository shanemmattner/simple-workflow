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

phases:
  - name: triage
    model: sonnet
    max_turns: 30

  - name: plan
    model: sonnet
    max_turns: 20

  - name: execute
    model: sonnet
    max_turns: 50

  - name: review
    model: sonnet
    max_turns: 20

  - name: improve
    model: sonnet
    max_turns: 10

---

# shftty-android

Domain-specific issue-to-PR workflow for the shftty Android app (Kotlin/Jetpack Compose healthcare staffing). 5-phase design: triage (read-only localization) → plan (implementation steps) → execute → review → improve.

## Run

```
./scripts/run.sh workflows/shftty-android shanemmattner/shftty-android#<issue>
```

## Reusable

The `scripts/check-build.sh` gradlew assembleDebug check is portable to any Android/Gradle project.
