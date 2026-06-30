# Claude Opus 4.6 Prompting Reference

Model ID: `claude-opus-4-6`
1M context variant: `claude-opus-4-6` with beta header (no separate model string)
Source: Anthropic platform docs, June 2026.

---

## API Configuration

### Core parameters

```json
{
  "model": "claude-opus-4-6",
  "max_tokens": 16000,
  "thinking": {"type": "adaptive"},
  "output_config": {"effort": "high"},
  "messages": [...]
}
```

### Thinking modes

Opus 4.6 supports both adaptive thinking (recommended) and manual `budget_tokens` (deprecated but still functional). Do NOT use `budget_tokens` for new code — it will be removed in a future release and is already rejected on Opus 4.7+.

| Mode | Config | Use when |
|------|--------|----------|
| Adaptive (recommended) | `thinking: {type: "adaptive"}` | Default for all pipeline tasks |
| Manual budget (deprecated) | `thinking: {type: "enabled", budget_tokens: N}` | When you need a hard cost ceiling now |
| Disabled | omit `thinking` param | When latency matters and reasoning is not needed |

### Effort levels (with adaptive thinking)

| Level | Behavior | When to use |
|-------|----------|-------------|
| `low` | Minimal thinking; skips for simple tasks | Triage, classification, fast lookups |
| `medium` | Balanced; may skip on simple queries | Routine coding, standard research |
| `high` (default) | Almost always thinks; deep reasoning | Most pipeline stages |
| `max` | Thinks with no constraints | Hardest problems; expensive |

Note: `xhigh` exists on Opus 4.7+ but NOT on 4.6. The highest valid effort for 4.6 is `max`.

### Sampling parameter restrictions

When adaptive thinking is enabled:
- `temperature` and `top_k` are NOT supported — omit them entirely
- `top_p` must be between `0.95` and `1.0` if set

When thinking is disabled, all standard sampling params work normally.

### Prefill responses — REMOVED

Prefilling the last assistant turn (providing a partial assistant message to continue from) is NOT supported on Opus 4.6+. Requests with prefilled final assistant turns return a 400 error.

Migration paths:
- Force output format: use structured outputs or tell the model explicitly what format to produce
- Skip preamble: add to system prompt "Respond directly without preamble. Do not start with 'Here is...', 'Based on...', etc."
- Continuation: move to user turn: "Your previous response ended with `[text]`. Continue from where you left off."

---

## System Prompt Structure

Recommended order:

```
1. Role definition (1 sentence)
2. Core task description
3. Behavioral rules (XML-wrapped sections)
4. Output format specification
5. Examples (3-5, in <examples> tags)
```

Example structure:

```xml
You are a [role] specializing in [domain].

<task>
[What you need Claude to do]
</task>

<rules>
[Constraints and behavioral guidance]
</rules>

<output_format>
[Exact JSON schema or format spec]
</output_format>

<examples>
<example>
Input: ...
Output: ...
</example>
</examples>
```

XML tags are first-class structure — Opus was trained to treat them as semantic separators. Wrapping content in `<context>`, `<task>`, `<output_format>` produces reliably better results than unstructured prose.

---

## What Works Well

### 1. Clear, positive instructions

Tell Claude what to do, not what to avoid.

```
BAD:  "Do not use markdown. Do not add headers."
GOOD: "Write in flowing prose paragraphs. Reserve markdown only for code blocks."
```

### 2. Context and intent

Claude reasons about intent. Explain why, not just what:

```
BAD:  "NEVER use ellipses"
GOOD: "Never use ellipses — this text will be read aloud by TTS and the engine
       cannot pronounce them."
```

### 3. Explicit action instructions for tool use

Opus 4.6 follows "suggest vs act" precisely. Be unambiguous:

```
BAD:  "Can you suggest some changes to improve this function?"
GOOD: "Change this function to improve its performance."
```

For proactive tool use in agents, add to system prompt:

```xml
<default_to_action>
By default, implement changes rather than only suggesting them. If the user's
intent is unclear, infer the most useful likely action and proceed, using tools
to discover any missing details instead of guessing.
</default_to_action>
```

### 4. XML-wrapped output constraints

```
Try: "Write the output in <result> tags."
Better than: "Output only the result."
```

### 5. Few-shot examples

3-5 examples (in `<examples>` tags) is the sweet spot. Make them:
- Relevant to your exact use case
- Diverse — cover edge cases
- Structured so Claude distinguishes them from instructions

### 6. Long context handling (20k+ tokens)

- Put documents FIRST, then instructions. This improves performance by up to 30%.
- Wrap each document: `<document index="N"><source>...</source><document_content>...</document_content></document>`
- Ask Claude to quote relevant passages before answering: "First quote relevant parts in `<quotes>` tags, then answer."

### 7. Guide thinking depth via prompts

After tool use, add: "After receiving tool results, carefully reflect on their quality and determine optimal next steps before proceeding."

To steer toward or away from thinking:
```
Encourage: "This task involves multi-step reasoning. Think carefully before responding."
Discourage: "Extended thinking adds latency. Only use it when it meaningfully improves
             quality — for simple tasks, respond directly."
```

### 8. Self-checking

Append: "Before you finish, verify your answer against [criteria]." Catches errors on coding and math reliably.

### 9. Parallel tool calls

Opus 4.6 runs independent tool calls in parallel by default. Boost this to ~100% with:

```xml
<use_parallel_tool_calls>
If you intend to call multiple tools and there are no dependencies between them,
make all independent calls in parallel. Maximize parallel tool calls where possible
to increase speed and efficiency. Never use placeholders or guess missing parameters.
</use_parallel_tool_calls>
```

To slow it down: "Execute operations sequentially with brief pauses between steps."

### 10. Reversibility guard for agentic tasks

```xml
Consider the reversibility and potential impact of your actions. Take local,
reversible actions freely (editing files, running tests), but for destructive or
shared-system actions (deleting files, force-pushing, sending messages), confirm
before proceeding.
```

---

## Agentic System Patterns

### Multi-context window workflows

1. First context window: set up framework (write tests, create init scripts)
2. Subsequent windows: iterate on a todo-list with structured state files
3. Use `tests.json` + `progress.txt` pattern for state across sessions
4. Git is the preferred state-tracking mechanism; Claude reads git logs well

### Research tasks

```xml
Search for information in a structured way. As you gather data, develop competing
hypotheses. Track confidence levels in your progress notes. Regularly self-critique
your approach. Update a hypothesis file to persist information and provide
transparency.
```

### Subagent orchestration

Opus 4.6 orchestrates subagents natively and proactively. If overuse is occurring:

```xml
Use subagents when tasks can run in parallel, require isolated context, or involve
independent workstreams. For simple tasks, sequential operations, single-file edits,
or tasks where you need to maintain context across steps, work directly.
```

### Minimize overengineering

```xml
Avoid over-engineering. Only make changes directly requested or clearly necessary:
- Don't add features, refactor, or "improve" beyond what was asked
- Don't add docstrings/comments to unchanged code
- Don't add error handling for scenarios that can't happen
- Don't create abstractions for one-time operations
The right complexity is the minimum needed for the current task.
```

### Prevent test-gaming

```xml
Write a high-quality, general-purpose solution using standard tools. Do not
hard-code values or create solutions that only work for specific test inputs.
Implement the actual logic that solves the problem generally. Tests verify
correctness — they don't define the solution.
```

---

## Tool Use with Thinking

### Tool choice restrictions

When adaptive thinking is active, only `tool_choice: {"type": "auto"}` (default) or `{"type": "none"}` are supported. `{"type": "any"}` and forced tool selection (`{"type": "tool", "name": "..."}`) are NOT supported.

### Preserving thinking blocks in multi-turn

When using tools with thinking, thinking blocks MUST be passed back to the API on subsequent turns:

```python
# First turn
response1 = client.messages.create(model="claude-opus-4-6", ...)
thinking_block = [b for b in response1.content if b.type == "thinking"][0]
tool_use_block = [b for b in response1.content if b.type == "tool_use"][0]

# Second turn — MUST include thinking block in assistant content
response2 = client.messages.create(
    model="claude-opus-4-6",
    messages=[
        {"role": "user", "content": "..."},
        {"role": "assistant", "content": [thinking_block, tool_use_block]},
        {"role": "user", "content": [{"type": "tool_result", ...}]}
    ]
)
```

### Interleaved thinking

Interleaved thinking (Claude thinks between tool calls) is automatically enabled in adaptive mode on Opus 4.6. It is NOT available in manual `budget_tokens` mode on Opus 4.6. If your workflow needs thinking between tool calls, use adaptive mode.

---

## Output Formatting

### Default behavior

Opus 4.6 defaults to prose (no markdown) unless prompted. To activate structured output:

```
"Use bullet points, headers, and bold emphasis."
```

To suppress markdown in flowing content:

```xml
<avoid_excessive_markdown>
Write in clear, flowing prose. Reserve markdown for inline code, code blocks, and
simple headers (## and ###). Do not use bold, bullet lists, or numbered lists unless
the content is truly discrete items or the user explicitly requests them.
</avoid_excessive_markdown>
```

### LaTeX

Opus 4.6 defaults to LaTeX for math. To disable:

```
Format math expressions in plain text only. Use "/" for division, "*" for
multiplication, "^" for exponents. No LaTeX, MathJax, or markup.
```

### JSON-only output

```
Output JSON only. No prose, no markdown fences.
```

This is sufficient — do NOT add model-specific clauses about thinking leakage or chain-of-thought suppression.

---

## Prompt Caching

- Switching between `adaptive` and `enabled`/`disabled` thinking modes breaks message cache breakpoints
- Consecutive requests using the same thinking mode preserve cache breakpoints
- System prompts and tool definitions remain cached regardless of mode changes
- Thinking tokens from prior turns are kept in context by default on Opus 4.5+ and Sonnet 4.6+ (charged as input tokens)

---

## Opus 4.6 vs Sonnet 4.6: When to Use Which

| Use Opus 4.6 when | Use Sonnet 4.6 when |
|-------------------|---------------------|
| Deep multi-file reasoning | 80%+ of everyday tasks |
| Agent Teams (experimental) | Instruction following is top priority |
| ARC-style novel problem solving | Latency matters |
| Cross-layer feature work (frontend/backend/test) | Overengineering is a concern |
| 1M context window needed | Budget is a constraint |
| BrowseComp / complex retrieval | Tight-scope code edits |

Benchmark context (per Anthropic, June 2026):
- Terminal-Bench 2.0: 65.4%
- ARC AGI 2: 68.8% (vs Opus 4.5's 37.6%)
- BrowseComp: 84.0%

Developers prefer Sonnet 4.6 over Opus 4.5 in 59% of head-to-head tests; use Opus 4.6 when you specifically need the deepest reasoning or Agent Teams.
