# Claude Haiku 4.5 — Known Quirks and Failure Modes

These are confirmed issues observed in production or documented by Anthropic. Each entry has a workaround.

---

## CRITICAL: StructuredOutput Tool Fails in Claude Code Workflows

**Observed**: Haiku wraps the entire JSON payload as a string inside a single `parameter` field rather than populating the schema keys. Schema validation fails repeatedly and Haiku gives up.

**Confirmed locally**: Haiku stage in shftty pipeline died after 2 nudges. The underlying analysis was correct — only the output serialization failed. (lessons.md `[x1]` — 2026-06-10)

**Workaround**: For any pipeline stage using Haiku, do NOT pass a `schema` option to the StructuredOutput tool. Instead:
- Instruct in the prompt: `"Final message = JSON only, no fences"`
- Parse the last message with `JSON.parse()` in the caller
- Add a fallback verdict for parse failures

**Does NOT affect** the direct Anthropic API's `output_config.format` structured outputs feature — that uses constrained decoding and works correctly with Haiku 4.5.

---

## Context Degradation Above 10k Tokens

**Observed**: Quality drops noticeably when the task context (prompt + input data) exceeds ~10k tokens. Haiku loses coherence across files, misses constraints stated earlier in the prompt, and increases hallucination rate.

**Workaround**:
- Pre-filter and compress input before sending to Haiku
- For RAG: pass max 3 chunks, pre-trimmed of boilerplate
- If task context is unavoidably large, escalate to Sonnet

---

## Hallucination on Inferred/Missing Values

**Observed**: When asked to fill in missing fields or infer data, Haiku produces confident but incorrect values. This is worst on domain-specific data (company names, product codes, reference IDs).

**Workaround**:
- Always include explicit constraint: `If a field is missing, set it to null. Do NOT infer.`
- Add confidence scoring and route low-confidence outputs (`< 0.7`) to human review or Sonnet
- Negative examples in few-shot: show `"qty": null` with `"issues": ["missing_qty"]`

---

## Output Verbosity Without Length Constraints

**Observed**: Without explicit length caps, Haiku pads output with repetition and explanatory prose. This increases costs on high-volume workloads.

**Workaround**:
- Always specify `max_tokens` in API calls
- State output length in the prompt: `Max 150 tokens. Answers over 200 tokens are non-compliant.`
- For JSON output, specify the exact keys — extra prose won't fit the schema

---

## Multi-File Coherence Loss

**Observed**: Tasks spanning more than 1-2 files cause Haiku to lose track of the overall context — it may apply changes inconsistently or reference variables that don't exist in the current file.

**Workaround**: Hard escalation rule — if a task touches more than 2 files, route to Sonnet. This is not a prompt-fixable issue; it is a capacity constraint.

---

## Repetition at High Temperature or With Greedy Decoding

**Note from related models (pattern generalizes)**: Small-tier models can produce repetition loops under greedy decoding. Haiku is not immune.

**Workaround**:
- Do not set `temperature=0` for open-ended generation tasks
- Use `temperature=0.2–0.5` for classification/extraction (enough variance to avoid loops)
- Default temperature (1.0) is fine for most tasks

---

## Structured Outputs API — Supported but Schema Limitations Apply

The `output_config.format` API with `json_schema` type is fully supported on Haiku 4.5. However:

- `minimum`, `maximum`, `minLength`, `maxLength` constraints in the schema are stripped before sending to the model; the SDK enforces them post-response
- `additionalProperties: false` is added automatically by the SDK
- Complex nested schemas with many optional fields increase error rate vs flat schemas

**Workaround**: Keep JSON schemas flat where possible. Use `required` for all critical fields. Add descriptions on fields where the model needs guidance (e.g., `"confidence": {"type": "number", "description": "0-1 scale, 0.9+ means high confidence"}`).

---

## Tool Use — API Level Works, Workflow StructuredOutput Does Not

**Tool use via the Anthropic Messages API** (`tools`, `tool_choice`) works reliably with Haiku 4.5. Anthropic's own docs state: "Strong coding and tool use" for Haiku 4.5.

**Tool use via the Claude Code `StructuredOutput` tool in pipeline stages** fails — see the CRITICAL entry above.

**Guidance**:
- For standalone scripts calling the API directly: tool_use works, use it
- For pipeline stages orchestrated through Claude Code: skip schema/StructuredOutput, parse raw JSON from the final message

---

## Knowledge Cutoff Gap

Training data cutoff: **Jul 2025**. Reliable knowledge cutoff: **Feb 2025**.

This is a 6-month gap from Sonnet's reliable cutoff (Aug 2025). For tasks involving recent frameworks, library versions, or APIs released after Feb 2025, Haiku may be confidently wrong.

**Workaround**: Pass reference documentation in the prompt for any framework-specific task where the version matters. Do not rely on Haiku's parametric knowledge for anything released after mid-2025.

---

## Not Adaptive — No Dynamic Reasoning Budget

Haiku 4.5 does NOT support adaptive thinking (automatic reasoning budget allocation that Sonnet and Opus have). Extended thinking IS supported but is manual and adds latency.

**Implication**: Haiku doesn't "decide" to think harder on harder tasks. It has a fixed reasoning capacity. Tasks that genuinely need more compute will just fail, not escalate internally.

**Workaround**: Escalation must happen at the routing layer (your pipeline), not inside the model.

---

## Escalation Decision Rules

| Signal | Action |
|---|---|
| Task touches >2 files | Escalate to Sonnet |
| Context > 10k tokens | Escalate to Sonnet |
| User sees the output directly | Escalate to Sonnet |
| Haiku confidence score < 0.7 | Escalate to Sonnet or human review |
| Task requires architectural judgment | Escalate to Sonnet or Opus |
| Framework released after Feb 2025, no docs in context | Pass docs in context or escalate |
| Stage uses StructuredOutput tool | Remove schema, parse raw JSON instead |
