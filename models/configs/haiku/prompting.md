# Claude Haiku 4.5 — Prompting Guide

Model ID: `claude-haiku-4-5-20251001`  
Alias: `claude-haiku-4-5`

---

## Hard Specs

| Property | Value |
|---|---|
| Context window | 200k tokens |
| Max output | 64k tokens |
| Pricing (input) | $1.00 / M tokens |
| Pricing (output) | $5.00 / M tokens |
| Batch input | $0.50 / M tokens |
| Batch output | $2.50 / M tokens |
| Cache read | $0.10 / M tokens |
| Cache write (5 min) | $1.25 / M tokens |
| Cache write (1 hr) | $2.00 / M tokens |
| vs Sonnet 4.6 | 3x cheaper (all pricing lines) |
| Extended thinking | Yes (supported, rarely needed) |
| Adaptive thinking | No |
| Latency | Fastest tier — 500–800ms for short prompts |
| Throughput | ~3–5x more tokens/sec than Sonnet |
| Training cutoff | Jul 2025 |
| Reliable knowledge cutoff | Feb 2025 |

---

## What Haiku Handles Well

Route these tasks to Haiku without hesitation:

- **Classification** — labeling, tagging, routing, triage (not final answer)
- **Short extraction** — pull structured fields from short/medium text
- **Short summarization** — prompts under ~500 words, output under ~1000 words
- **Data cleaning** — normalize fields, validate formats, flag issues at high volume
- **RAG Q&A** — answer from pre-retrieved snippets (3 chunks max)
- **Simple one-file coding** — bug fixes, small refactors, code review for style/patterns
- **Support triage** — classify and route tickets; do NOT use for the final response
- **Batch processing** — classify or transform thousands of records
- **Real-time autocomplete** — search, form fields, code suggestions
- **Template filling** — fill structured templates with provided data
- **Confidence scoring** — ask Haiku to rate its own confidence; accurate enough to use for routing

Rule of thumb: if the task is well-defined, expected output is structured or short, and a mediocre result can be caught downstream (review, validation, escalation), use Haiku.

---

## What Haiku Struggles With — Escalate to Sonnet

Escalate when any of these apply:

- Task requires understanding **multiple files** (loses coherence across files)
- **Context > 10k tokens** — quality degrades noticeably above this
- **Long-document synthesis** — 50+ page docs, annual reports, legal briefs
- **Complex algorithms** or intricate debugging (tracks fewer threads simultaneously)
- **Architectural reasoning** — system design, cross-file refactors, tech stack decisions
- **Creative writing requiring nuanced voice** — prose is flat compared to Sonnet
- **Novel problems without established patterns** — Haiku relies more on pattern-matching
- **Multi-hop reasoning** — 5+ step chains where each step depends on prior results
- **Customer-facing final answers** — triage OK, but escalate before response delivery
- **Multi-file implementation** — any feature spanning more than 1-2 files

Benchmark data: Haiku matched Sonnet quality on ~43/50 common coding tasks. The 7 failures were all complex architectural reasoning.

---

## Prompting Best Practices

### 1. Short, explicit system prompts — not long CLAUDE.md-style docs

Haiku responds better to structured, explicit instructions. Long meandering system prompts waste tokens and confuse output. Aim for:
- One-sentence role: `You are a concise technical assistant.`
- Explicit objectives as a numbered list (max 3)
- Output format declaration up front

### 2. Explicit output format — always

Never leave output format implicit. Mandate it every call:

```
Return JSON with keys: decision, rationale, risks, next_steps.
No extra text. No markdown fences.
```

For free-text output: specify audience, tone, length, format, and any must-have elements in one sentence each.

### 3. Checklists over open-ended phrasing

Bad: "Review this code."  
Good: "Review for: (a) correctness, (b) security, (c) readability. Output: pass/fail per item with 1-line justification. Max 6 findings."

### 4. Step-bounded reasoning

When reasoning is needed: `Think in up to 4 steps, then present a final answer only.`

This keeps Haiku focused without triggering verbose chain-of-thought.

### 5. Few-shot examples — 2-3, domain-specific

```
Example 1:
Input: {"sku": "ABC-123", "qty": "50 units"}
Output: {"sku": "ABC-123", "qty": 50, "issues": []}

Example 2:
Input: {"sku": "XYZ", "qty": "unknown"}
Output: {"sku": "XYZ", "qty": null, "issues": ["missing_qty"]}
```

Keep examples short and conformant to your exact output schema. Do not use generic examples — domain-specificity matters.

### 6. Explicit constraint lists

Negative constraints reduce hallucination 30-40%:

```
Constraints:
- Do NOT infer missing values. If a field is missing, set it to null.
- Do NOT correct spelling in product names.
- If unsure, flag as "uncertain" — do not guess.
```

### 7. Labeled delimiters

Use `===`, `[Context]`, `[Policy]`, `[Task]`, `[Output]` to separate prompt sections. Haiku respects these and they prevent instruction bleed.

### 8. Separate policy from task

```
[Policy] Never output PII. Keep under 150 tokens. Cite sources if provided.
[Task] Summarize the email chain for the sales lead.
```

This pattern makes prompts maintainable and reduces drift across calls.

### 9. Confidence scoring

Always ask Haiku to score its confidence:

```
Include a "confidence" field (0-1). Below 0.7 = flag for human review.
```

Use confidence downstream to route low-confidence outputs to Sonnet or human review. Haiku's self-scoring is accurate enough to gate on.

### 10. Pass snippets, not corpora

For RAG: pass 1-3 pre-retrieved chunks, pre-trimmed. Label them `[S1]`, `[S2]`. Do not pass full documents. Haiku degrades with noisy context.

---

## Sampling Parameters

- **Temperature**: Default (1.0) for most tasks. Lower (0.2–0.5) for JSON extraction and classification.
- **Max tokens**: Set explicitly. Haiku will pad output if you don't cap it.
- **Extended thinking**: Supported but unnecessary for Haiku's intended use cases. Do not enable unless the task requires multi-step reasoning — it adds latency and cost.
- **Adaptive thinking**: Not available on Haiku 4.5.

---

## Batching

- Optimal batch size: **10–50 records per request**
- 100+ records per request increases latency and risks hitting token limits
- For very large datasets, use the Batch API ($0.50/$2.50 per M) — 50% off both input and output

---

## Cost Math

For a pipeline processing 100M input / 30M output tokens per month:

| Mode | Haiku 4.5 | Sonnet 4.6 | Delta |
|---|---|---|---|
| No cache | $250 | $750 | $500 |
| 70% cache | $187 | $561 | $374 |
| Batch | $125 | $375 | $250 |
| Batch + 70% cache | $93.50 | $280.50 | $187 |

The 3x ratio is constant across all pricing lines. Caching and batching reduce both bills equally.
