# Claude Sonnet 4.6 — Prompting Reference

> **Model ID**: `claude-sonnet-4-6`
> **Updated**: 2026-06-29
> **Scope**: Pipeline agents, background agentic work, interactive sessions.

---

## Model Specs

| Property | Value |
|---|---|
| Model ID | `claude-sonnet-4-6` |
| Context window | 200k (standard) / 1M (beta, 2× input cost) |
| Max output | 128k tokens (sync) / 300k (Batches API w/ beta header) |
| Input price | $3/MTok |
| Output price | $15/MTok |
| Knowledge cutoff | August 2025 (training data: January 2026) |
| SWE-bench Verified | 79.6% |
| Latency tier | Fast (between Opus "Moderate" and Haiku "Fastest") |

**Pricing vs siblings:**
- Opus 4.8: $5/$25 — 67% more expensive. Only worth it for tasks Sonnet fails.
- Haiku 4.5: $1/$5 — 3× cheaper. Use for triage, classification, grep-and-report.
- Prompt caching: up to 90% savings. Batch API: 50% off.

---

## Thinking Configuration

### Adaptive thinking (recommended)

Use adaptive thinking. Claude decides internally when and how much to think based on task complexity.

```python
response = client.messages.create(
    model="claude-sonnet-4-6",
    max_tokens=16000,
    thinking={"type": "adaptive"},
    output_config={"effort": "medium"},   # see Effort section
    messages=[...],
)
```

### Extended thinking with budget_tokens (deprecated)

`thinking: {type: "enabled", budget_tokens: N}` still works but is deprecated. Migrate to adaptive.

### Thinking display

Default is `"summarized"` — returns a readable summary. You are billed for full thinking tokens regardless of display setting.

- `"summarized"`: Readable summary. Good default.
- `"omitted"`: Faster time-to-first-text-token when streaming. Thinking block is present but empty.

### Interleaved thinking

Adaptive mode automatically enables interleaved thinking between tool calls. This is what enables sophisticated agentic reasoning. Do not disable it.

---

## Effort Parameter

Anthropic recommends **`medium` as the default for Sonnet 4.6**, even though the API default is `high`.

| Level | Behavior | Use case |
|---|---|---|
| `max` | No token constraints | Deepest reasoning, maximum thoroughness |
| `high` | Almost always thinks | Complex reasoning, difficult coding |
| `medium` | **Recommended default.** May skip thinking for simple queries. | Agentic coding, tool-heavy pipelines |
| `low` | Significant savings, some capability reduction | Chat, non-coding, latency-sensitive, high-volume |

Note: `xhigh` is NOT available on Sonnet 4.6 (Opus 4.7+ and Fable 5 only).

---

## Sampling Parameters

| Parameter | Recommendation |
|---|---|
| `temperature` | 0.0–0.3 for deterministic structured output; 0.7–1.0 for creative tasks |
| `max_tokens` | Set explicitly — default is 1024, but Sonnet supports 128k |
| `top_p` | Leave at default unless you have a specific reason |

---

## System Prompt Structure

Sonnet 4.6 infers appropriate behavior from a well-defined role. Do not over-engineer. Redundant behavioral guidance causes overcorrection.

**Recommended structure:**
1. Role definition (1–2 sentences)
2. Constraints
3. Output format specification
4. Stop.

**Use XML tags** to separate content types — this is one of the most effective techniques:

```xml
<role>You are the triage stage of a CI pipeline...</role>
<constraints>
  - Only act on issues in the provided repository
  - Never modify files outside the working directory
</constraints>
<output_format>JSON only. No prose, no markdown fences.</output_format>
```

**Useful XML blocks from Anthropic docs:**
- `<use_parallel_tool_calls>` — boosts parallel tool calling to ~100%
- `<investigate_before_answering>` — minimizes hallucinations; Claude researches before responding
- `<default_to_action>` — makes Claude proactively use tools
- `<avoid_over_engineering>` — prevents extra files and unnecessary abstractions

**Long-context placement:** Put long documents at the TOP of the prompt, above queries and instructions. Queries at the end improve response quality by up to 30%.

---

## Tool Use

### Parallel tool calling

Sonnet 4.6 aggressively runs independent tool calls in parallel — multiple searches, file reads, bash commands. This is the default and is desirable for most agentic workflows.

To maximize: add `<use_parallel_tool_calls>` XML block to system prompt.
To minimize: instruct "Execute operations sequentially."

### Tool choice with thinking enabled

When thinking is active, only `tool_choice: {"type": "auto"}` or `{"type": "none"}` are supported. Cannot force specific tool with `{"type": "any"}` or `{"type": "tool", "name": "..."}`.

### Preserve thinking blocks in multi-turn conversations

When continuing a conversation that includes tool results, thinking blocks from assistant turns MUST be passed back unchanged. Stripping them breaks reasoning continuity.

---

## Pipeline Agent Templates

### Standard pipeline agent

```python
response = client.messages.create(
    model="claude-sonnet-4-6",
    max_tokens=8192,
    thinking={"type": "adaptive"},
    output_config={"effort": "medium"},
    temperature=0.2,
    system="<role>...</role>",
    messages=[...],
)
```

### High-stakes stage (code generation, implementation)

```python
response = client.messages.create(
    model="claude-sonnet-4-6",
    max_tokens=16000,
    thinking={"type": "adaptive"},
    output_config={"effort": "high"},
    temperature=0.0,
    system="<role>...</role>",
    messages=[...],
)
```

### Triage / classification (fast, cheap)

```python
response = client.messages.create(
    model="claude-sonnet-4-6",
    max_tokens=2048,
    output_config={"effort": "low"},
    temperature=0.0,
    messages=[...],
)
```

---

## Sonnet 4.6 vs Opus 4.8: Decision Guide

### Stay on Sonnet when:
- Standard implementation, file edits, research with structured output
- Coding tasks with human review (benchmark gap doesn't justify 5× cost)
- High-volume agentic workflows where cost matters
- Tool-heavy workflows at `medium` effort (comparable quality to Opus)
- Pipeline background agents (most tasks don't need Opus-level reasoning)

### Upgrade to Opus when:
- Complex multi-step tasks requiring sustained long-horizon reasoning
- Architecture decisions requiring cross-file reasoning
- Tasks where Sonnet consistently fails or produces low quality
- Autonomous agent loops with no human review checkpoint
- Tasks requiring `xhigh` effort (not available on Sonnet)

**Key behavioral differences:**
- Opus has `xhigh` effort; Sonnet maxes at `max`
- Opus may overthink/overengineer more than Sonnet
- Opus has stronger subagent orchestration instincts
- Sonnet is faster, cheaper, and often sufficient for coding

---

## Migration Checklist (from Sonnet 4.5 or earlier)

- [ ] Replace `thinking: {type: "enabled", budget_tokens: N}` with adaptive + effort param
- [ ] Remove prefilled response patterns (400 error on 4.6 — see quirks.md)
- [ ] Remove aggressive anti-laziness prompts ("MUST", "CRITICAL", "ALWAYS")
- [ ] Remove step-by-step reasoning instructions (use effort parameter instead)
- [ ] Test tool triggering — likely needs to be dialed back
- [ ] Update `max_tokens` expectations (128k cap on Sonnet 4.6 vs 64k on 4.5)
- [ ] Note context window increase: 1M tokens vs 200k on Sonnet 4.5
- [ ] Update interleaved thinking config: no beta header needed with adaptive mode

---

## Sources

- [Claude 4 best practices — Anthropic docs](https://docs.anthropic.com/en/docs/build-with-claude/prompt-engineering/claude-4-best-practices)
- [Models overview — Anthropic docs](https://docs.anthropic.com/en/docs/about-claude/models/overview)
- [Extended thinking — Anthropic docs](https://docs.anthropic.com/en/docs/build-with-claude/extended-thinking)
- [Adaptive thinking — Anthropic docs](https://docs.anthropic.com/en/docs/build-with-claude/adaptive-thinking)
- [Effort — Anthropic docs](https://docs.anthropic.com/en/docs/build-with-claude/effort)
- [Introducing Claude 4 — Anthropic](https://www.anthropic.com/news/claude-4)
- [Claude Sonnet 4.6 in Production — Caylent](https://caylent.com/blog/claude-sonnet-4-6-in-production-capability-safety-and-cost-explained)
- [Claude Sonnet 4.6 — NxCode](https://www.nxcode.io/resources/news/claude-sonnet-4-6-complete-guide-benchmarks-pricing-2026)
