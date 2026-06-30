---
name: tunedvoice
description: Domain-specific workflow for TunedVoice — macOS push-to-talk dictation app (Swift/SwiftUI, FluidAudio, ANE)
type: code

repo: shanemmattner/tunedvoice-monorepo
repo_path: repos/tunedvoice
provider: gitlab

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

context_files:
  - .workflows/context.md
  - .workflows/testing.md
---

# tunedvoice

Domain-specific issue-to-PR workflow for the TunedVoice macOS push-to-talk dictation app (Swift/SwiftUI, FluidAudio, ANE).

## Run

```
./scripts/run.sh workflows/tunedvoice shanemmattner/tunedvoice-monorepo#<issue>
```

## Reusable

The `context_files` injection pattern (`.workflows/context.md` + `.workflows/testing.md`) is the standard way to give any target repo's domain knowledge to pipeline agents — copy this pattern for new Swift/macOS workflows.
