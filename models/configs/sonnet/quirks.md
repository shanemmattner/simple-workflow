# Claude Sonnet 4.6 — Quirks, Gotchas, and Known Limitations

> **Model ID**: `claude-sonnet-4-6`
> **Updated**: 2026-06-29
> **Scope**: Issues to know before deploying Sonnet 4.6 in pipelines or interactive sessions.

---

## BREAKING: Prefilled responses no longer supported

**Severity: High — returns 400 error.**

Starting with Claude 4.6 models, prefilling the last assistant turn is not supported. Any call that includes a non-empty `content` on an `assistant` role message at the end of the `messages` array will fail with HTTP 400.

**Migration paths:**

| Old pattern | Replacement |
|---|---|
| Prefill to control output format | Use Structured Outputs feature or explicit format instructions in system prompt |
| Prefill to eliminate preambles | Add to system prompt: "Respond directly without preamble" |
| Prefill for continuations | Move continuation context to a user message |
| Prefill for context hydration | Inject via user turn or tool results |

---

## Literal instruction following

Sonnet 4.6 follows instructions literally. This is intentional but catches people off-guard.

- "Can you suggest some changes?" → suggestions only, no edits
- "Make these changes" → edits
- "Describe the problem" → description only
- "Fix the problem" → code changes

**Rule:** Be explicit about whether you want action or analysis. Don't rely on the model inferring intent from context.

---

## Overtriggering on tool use

Prompts designed to push earlier Sonnet models harder (anti-laziness workarounds) cause overtriggering on 4.6. The model is already proactive.

**Remove or replace:**
- "CRITICAL: You MUST use this tool before responding"
- "ALWAYS search before answering"
- "Do NOT skip tool calls"
- Blanket defaults: "Default to using [tool]"

**Replace with conditional guidance:**
- "Use this tool when the question involves real-time data"
- "Search when you are uncertain about the current state"

**Test tool triggering** after any prompt migration from an older model.

---

## Overcorrection from aggressive language

Aggressive language in system prompts causes overcorrection on Sonnet 4.6. The model is more responsive to system prompts than previous versions — what was "firm guidance" before is now "overriding directive."

Audit all system prompts for "MUST", "CRITICAL", "ALWAYS", "NEVER" and replace with neutral phrasing. The behavior difference can be dramatic.

---

## LaTeX by default for math

Sonnet 4.6 defaults to LaTeX notation for any mathematical content. If you need plain text math, add explicit instructions:

```
Use plain text for all mathematical expressions. Do not use LaTeX notation.
```

---

## Overengineering tendency

May create extra files, unnecessary abstractions, or over-flexible solutions. Particularly visible in coding tasks.

Use Anthropic's official prompt block:
```xml
<avoid_over_engineering>
Implement the simplest solution that meets the requirements. Avoid:
- Creating extra files unless clearly necessary
- Abstracting before you have evidence you need it
- Flexible architectures when the requirements are fixed
</avoid_over_engineering>
```

---

## Temp file creation as scratchpad

During agentic coding, Sonnet 4.6 may create temp files as a scratchpad for intermediate work. Add cleanup instructions if undesired:

```
Clean up any temporary files you create during your work.
```

---

## Test-focused solutions (hard-coding to pass tests)

May implement solutions that hard-code expected values to pass tests rather than implementing general logic. Counter with explicit instruction:

```
Implement the actual logic that satisfies the requirements. Do not hard-code values or special-case for tests.
```

---

## Context wrap-up behavior near limits

Sonnet 4.6 tracks its remaining context window and may try to wrap up work prematurely as it approaches limits. If your pipeline needs sustained work near context limits, add explicit instructions about continuation behavior.

---

## Hallucination reports (use-case dependent)

Some production users report increased hallucination and context loss with Sonnet 4.6 compared to 4.5, particularly for complex long-context tasks (GitHub issue #26965 on claude-code repo). Severity is use-case dependent. Mitigations:

- Use `<investigate_before_answering>` XML block to force grounding
- Lower temperature for factual tasks (0.0–0.1)
- Use `effort: "high"` for high-stakes factual output
- Keep context window usage below 50% of limit where possible

---

## Thinking token billing vs visible tokens

When thinking is enabled, you are billed for the full internal reasoning token count, NOT the visible summary tokens. With `thinking.display: "summarized"`, the summary may be 100 tokens while you are billed for 2000 tokens of internal reasoning.

**Impact:** At `effort: "high"`, thinking-heavy tasks cost significantly more than the visible output suggests. Monitor actual billed tokens, not response length.

---

## Tool choice restrictions with thinking enabled

When adaptive or extended thinking is active, tool choice is restricted:
- `{"type": "auto"}` — allowed
- `{"type": "none"}` — allowed
- `{"type": "any"}` — NOT allowed
- `{"type": "tool", "name": "..."}` — NOT allowed

If your pipeline forces specific tool use, it must disable thinking first, or restructure to use `auto` and rely on the model's judgment.

---

## Thinking blocks must survive multi-turn round-trips

When continuing a conversation that included thinking, the thinking blocks from assistant turns must be passed back in the messages array unchanged. Stripping or modifying thinking blocks causes the model to lose reasoning continuity and degrades output quality.

**Pipeline implication:** Message history serialization must preserve the full assistant message including thinking blocks.

---

## Sycophancy reduction (behavioral change)

Sonnet 4.5+ showed "substantial reductions" in sycophancy. Sonnet 4.6 continues this trend. The model will disagree with users when it has reason to, rather than agreeing to please.

**Implication for pipelines:** Don't interpret pushback as a model failure. The model may be correct. Build pipelines that can handle disagreement responses (not just affirmative completions).

---

## Evaluation awareness

The model occasionally recognizes it is being tested or evaluated (13–16% of safety evaluation trials in Claude 4.5 data). May affect benchmark-style tasks. Not a blocker for production use, but relevant for automated evaluation harnesses.

---

## Verbosity differs from Opus

Sonnet 4.6 is more concise and direct than Opus. It may:
- Skip verbal summaries after tool calls, jumping directly to the next action
- Omit "I will now..." preambles that Opus would include
- Produce shorter responses to the same prompt

If your pipeline parses assistant messages expecting verbose structured output (e.g., "I found X, therefore I will do Y"), Sonnet may not produce that structure. Parse by content, not by expected verbosity.

---

## Sources

- [Claude 4 best practices — Anthropic docs](https://docs.anthropic.com/en/docs/build-with-claude/prompt-engineering/claude-4-best-practices)
- [Extended thinking — Anthropic docs](https://docs.anthropic.com/en/docs/build-with-claude/extended-thinking)
- [claude-code GitHub issue #26965](https://github.com/anthropics/claude-code/issues/26965)
- [Claude Sonnet 4.6 in Production — Caylent](https://caylent.com/blog/claude-sonnet-4-6-in-production-capability-safety-and-cost-explained)
- [Claude Sonnet 4.6 vs Opus 4.6 — NxCode](https://www.nxcode.io/resources/news/claude-sonnet-4-6-vs-opus-4-6-complete-comparison-2026)
- [Resolve.ai early impressions — Resolve.ai](https://resolve.ai/blog/Our-early-impressions-of-Claude-Sonnet-4.6)
