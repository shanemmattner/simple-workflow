---
name: comms-triage
description: Scan Gmail + iMessage, prioritize messages, draft replies, produce digest
type: ops

budget:
  max_per_run_usd: 0.50

models:
  haiku:
    name: claude-haiku-4-5
    max_tokens: 8192
    cost: {input_per_mtok: 0.80, output_per_mtok: 4.00}
  sonnet:
    name: claude-sonnet-4-6
    max_tokens: 16384
    cost: {input_per_mtok: 3.00, output_per_mtok: 15.00}
  m27hs:
    name: MiniMax-M2.7-highspeed
    max_tokens: 16384
    cost: {input_per_mtok: 0.20, output_per_mtok: 0.80}
  m3:
    name: MiniMax-M3
    max_tokens: 16384
    cost: {input_per_mtok: 0.30, output_per_mtok: 1.20}

phases:
  - name: scan
    model: sonnet
    max_turns: 10
  - name: prioritize
    model: sonnet
    max_turns: 3
  - name: draft-replies
    model: sonnet
    max_turns: 5
  - name: digest
    model: sonnet
    max_turns: 3

gates:
  scan:
    - messages_array_present
  prioritize:
    - buckets_present
  digest:
    - digest_path_present
---

# comms-triage

Ops workflow for unified inbox triage — scans Gmail and iMessage, prioritizes messages, drafts replies, and produces a digest.

## Run

```
python -m engines.comms_claude --hours 24 --budget 0.50 --model sonnet
```

## Reusable

The scan → prioritize → draft → digest phase pattern is reusable for any multi-source communications triage. The cost-optimized model routing (m27hs for scan/prioritize/digest, m3 only for drafting) is the template for budget-sensitive comms workflows.
