# Claude Opus 4.6 Quirks & Anti-Patterns

Model ID: `claude-opus-4-6`
Source: Anthropic platform docs + system card, June 2026.

These are failure modes and behavioral quirks specific to Opus 4.6 that pipeline agents need to know about before calling this model.

---

## Thinking & Reasoning Quirks

### 1. Overthinking / extensive upfront exploration

Opus 4.6 does significantly more upfront exploration than prior models, especially at `high` or `max` effort. It will gather extensive context, pursue multiple research threads, and consider edge cases without being asked. This inflates thinking tokens and slows responses.

**Fix:**
```
When deciding how to approach a problem, choose an approach and commit to it.
Avoid revisiting decisions unless you encounter new information that directly
contradicts your reasoning. If you're weighing two approaches, pick one and
see it through.
```

Or lower effort: `"effort": "medium"`.

### 2. `budget_tokens` is deprecated — will break on upgrade

`thinking: {type: "enabled", budget_tokens: N}` still works on 4.6 but is rejected with a 400 error on 4.7+. Any code using `budget_tokens` needs to be migrated before upgrading. Use `thinking: {type: "adaptive"}` + `output_config: {effort: "..."}` instead.

### 3. Adaptive thinking triggers on complex system prompts

Large or complex system prompts can cause Opus 4.6 to think more often than intended. If you see unexpected thinking tokens on simple queries, add to the system prompt:

```
Extended thinking adds latency and should only be used when it will meaningfully
improve answer quality — typically for multi-step reasoning. For simple queries,
respond directly.
```

### 4. Cannot toggle thinking mid-turn

The entire assistant turn (including all tool call rounds) must use the same thinking mode. Switching thinking on/off mid-turn is not supported. If you set adaptive thinking and tools are involved, thinking is active throughout all tool loops for that turn.

### 5. Thinking words trigger confusion when thinking is disabled

When thinking is OFF, Opus 4.6 is sensitive to words like "think" and its variants. If you use `"think step by step"` prompts with thinking disabled, it can produce unexpected behavior. Alternatives: "consider", "evaluate", "reason through".

---

## Tool Use Quirks

### 6. Overtriggering on aggressive tool prompts

Prompts designed for previous models (e.g., `"CRITICAL: You MUST use this tool when..."`, `"If in doubt, use [tool]"`) cause Opus 4.6 to overtrigger. The model is more responsive to system prompts than previous generations.

**Fix:** Dial back aggressive language. Replace:
- `"CRITICAL: You MUST use X when..."` → `"Use X when..."`
- `"If in doubt, use [tool]"` → Remove entirely

### 7. Tool choice restrictions with thinking

When adaptive thinking is active:
- Only `tool_choice: {"type": "auto"}` or `{"type": "none"}` are supported
- `{"type": "any"}` and forced tool selection (`{"type": "tool", "name": "..."}`) return errors

### 8. Thinking blocks must be passed back in tool loops

When using tools with extended thinking (adaptive or manual), you MUST include the thinking blocks from prior assistant turns when sending tool results. Omitting them causes the model to lose reasoning continuity. The blocks must be passed back unmodified — any modification is rejected.

### 9. Subagent overuse

Opus 4.6 has a strong predilection for spawning subagents and will delegate even simple tasks like a direct grep. This burns context and costs money.

**Fix:** Add to system prompt when subagent overhead matters:
```
Use subagents when tasks can run in parallel, require isolated context, or involve
independent workstreams. For simple tasks, single-file edits, or tasks where you
need to maintain context across steps, work directly rather than delegating.
```

---

## Output & Formatting Quirks

### 10. Prose-default (no markdown unless asked)

Opus 4.6 defaults to plain prose. Headers, bullets, and bold are NOT used unless explicitly requested. If your pipeline expects markdown-formatted output, you must ask for it explicitly.

### 11. Conciseness — may skip summaries after tool calls

The model has a more direct, concise style than previous Opus versions. It may jump directly to the next action after a tool call without summarizing what it did. If your pipeline needs visibility into actions:

```
After completing a task that involves tool use, provide a quick summary of
the work you've done.
```

### 12. LaTeX by default for math

Opus 4.6 defaults to LaTeX (`\frac{}{}`, `\( \)`) for math expressions. This breaks plain text pipelines. If your pipeline produces text read by other systems, suppress it explicitly.

### 13. Generic "AI slop" frontend aesthetics

For frontend/design tasks, the model converges on predictable patterns (Space Grotesk, purple gradients, Inter). Without explicit guidance, output looks generic. Inject the `<frontend_aesthetics>` prompt from prompting.md when design quality matters.

---

## Agentic Behavior Quirks

### 14. Overengineering — adds unrequested abstractions

Opus 4.6 has a tendency to add defensive error handling, helper utilities, extra documentation, and refactors for hypothetical future requirements that were never asked for. A bug fix becomes a code cleanup. A simple feature becomes a configurable system.

**Fix:** Include in system prompt:
```
Only make changes directly requested or clearly necessary. A bug fix doesn't
need surrounding code cleaned up. Don't add abstractions for one-time operations.
Don't design for hypothetical future requirements.
```

### 15. File creation for scratchpadding

The model creates temporary files (Python scripts, test files) for iteration, then may leave them behind. If your pipeline runs in a shared environment:

```
If you create temporary files for iteration, clean them up by removing them
at the end of the task.
```

### 16. Test-gaming / hard-coding

Opus 4.6 can optimize toward making tests pass rather than solving problems generally. Symptom: solutions hardcode specific test case values or create helper scripts that only work for the given inputs.

**Fix:** Add the "high-quality, general-purpose solution" prompt from prompting.md.

### 17. Destructive actions without confirmation

Without guidance, Opus 4.6 may delete files, force-push, or post to external services autonomously. In agentic pipelines with real-world side effects, add the reversibility guard prompt from prompting.md.

### 18. Excessive parallel execution

Opus 4.6 runs tool calls in parallel aggressively — sometimes so aggressively it bottlenecks the host system with parallel bash commands. To throttle:

```
Execute operations sequentially with brief pauses between each step to
ensure stability.
```

---

## Context & Memory Quirks

### 19. Context wrapping behavior

As Opus 4.6 approaches its context limit, it may try to artificially wrap up work early. If your harness handles context compaction automatically:

```
Your context window will be automatically compacted as it approaches its limit.
Do not stop tasks early due to token budget concerns. If approaching the limit,
save current progress to memory before the context refreshes.
```

### 20. 1M context window is beta — degradation exists

The 1M context beta achieves 76% on MRCR v2 8-needle 1M variant. This is dramatically better than Sonnet 4.5 (18.5%) but still imperfect. For needle-in-haystack tasks at very long context, expect some degradation. Put the most critical information near the end of the prompt (queries last improves performance by up to 30% in tests).

---

## API / Integration Quirks

### 21. Cache breakpoints break on thinking mode change

Switching between `adaptive` and `enabled`/`disabled` thinking modes invalidates message cache breakpoints. System prompts and tool definitions remain cached. If you switch thinking modes between calls in a pipeline, budget for cache misses on the message history.

### 22. `stop_reason: max_tokens` more common at high/max effort

At `high` and `max` effort, the model may exhaust `max_tokens` on thinking tokens before producing output. Monitor for `stop_reason: "max_tokens"` and either increase `max_tokens` or lower effort. The breakdown is available in `usage.output_tokens_details.thinking_tokens`.

### 23. Thinking tokens billed differ from visible tokens

With summarized thinking (the default on 4.6), you are billed for the full thinking tokens the model generated internally, NOT the summary tokens you see in the response. The visible token count will be much lower than the billed count. Use `usage.output_tokens_details.thinking_tokens` to see what you actually paid for.

### 24. Prefill on last assistant turn returns 400

As of Opus 4.6, providing a partial assistant message as the final message in the conversation (prefill) returns a 400 error. This is not a quirk to work around — it's a permanent API change. See prompting.md migration section.

---

## Version Compatibility Notes

| Feature | Opus 4.6 | Opus 4.7+ |
|---------|----------|-----------|
| `budget_tokens` | Deprecated (works) | 400 error |
| Adaptive thinking | Supported | Only mode |
| `xhigh` effort | NOT supported | Supported |
| Prefill | NOT supported | NOT supported |
| Interleaved thinking (manual mode) | NOT supported | N/A (no manual mode) |
| Interleaved thinking (adaptive mode) | Supported | Supported |
