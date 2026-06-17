# SQLite Schema Research: LLM Agent Pipeline Run Storage

Research completed 2026-06-16. Context: designing a per-run SQLite database for a multi-phase pipeline (triage -> plan -> test-plan -> wave-plan -> execute -> review) where each phase is a multi-turn Claude CLI agent session.

---

## 1. How Existing Tools Store Run/Trace Data

### LangSmith (LangChain)

LangSmith's data model is built on **Runs** — a unified object that serves as both trace (root run) and span (child run). Key fields per Run:

| Field | Type | Purpose |
|-------|------|---------|
| `id` | UUID | Unique run identifier |
| `trace_id` | UUID | Groups all runs in one execution |
| `parent_run_id` | UUID | Tree hierarchy |
| `dotted_order` | string | Encodes timestamp + UUID for ordering (e.g., `20240101T000000Z<parent>.20240101T000001Z<child>`) |
| `run_type` | enum | `llm`, `chain`, `tool`, `retriever`, `embedding`, `prompt`, `parser` |
| `name` | string | Function/chain name |
| `inputs` | JSON dict | Serialized function parameters |
| `outputs` | JSON dict | Return values |
| `error` | string | Exception traceback if failed |
| `start_time` / `end_time` | timestamp | Execution boundaries |
| `extra` | JSON dict | Metadata, model params, `ls_method` marker |
| `events` | JSON list | Streaming tokens, custom events with timestamps |
| `tags` | string[] | Categorization labels |
| `session_name` | string | Project workspace |
| `attachments` | binary | File/binary content separated from inputs |

Key insight: LangSmith uses a **single Run type** for everything — the hierarchy is encoded via `parent_run_id` and `dotted_order`. No separate "message" or "turn" tables. The conversation is reconstructed from nested runs.

In 2026, LangChain introduced **SmithDB** — purpose-built for the explosion of agent trace data. Modern agent traces have "hundreds of deeply nested spans" that "arrive in pieces with start and end events potentially minutes or hours apart." SmithDB stores all spans of a trace within the same physical index for fast cold starts.

### Langfuse

Langfuse uses ClickHouse with a schema transitioning toward a unified `events_full` table:

| Column | Type | Purpose |
|--------|------|---------|
| `project_id` | String | Partition key |
| `trace_id` | String | Parent trace |
| `span_id` | String | Unique event ID |
| `parent_span_id` | String | Tree structure |
| `start_time` / `end_time` | DateTime64(6) | Temporal boundaries |
| `name` | String | Event/span name |
| `type` | LowCardinality(String) | SPAN, GENERATION, EVENT |
| `trace_name` | String | Denormalized |
| `user_id` / `session_id` | String | Denormalized context |
| `input` / `output` | String (ZSTD) | Compressed payloads |
| `metadata_names` / `metadata_values` | Array(String) | Flattened KV pairs |
| `calculated_input_cost` | Float | Materialized at write-time |
| `calculated_output_cost` | Float | Materialized at write-time |
| `calculated_total_cost` | Float | Materialized at write-time |
| `input_length` / `output_length` | Int | Pre-computed lengths |

Key insight: Langfuse is **migrating away from normalized tables** (separate traces, observations, scores) toward a **single denormalized events table** to eliminate expensive joins. Costs are pre-computed at write time.

The separate **scores** table stores evaluation results (ANNOTATION, EVAL, or API-created) linked to traces/observations — useful for our review phase outcomes.

### Braintrust

Braintrust built **Brainstore** — a custom database for agent observability:

- Uses a WAL design where reads can access data as soon as a WAL entry is written (strongly consistent)
- Stores all spans of a trace within the same physical index
- Automatically detects base64-encoded attachments (images, PDFs) and uploads them to object storage, replacing them with references
- Optimized for "large traces with hundreds of deeply nested spans"

Their recommended **minimum viable span schema**:
- Span type: tool call, reasoning, state transition, or memory operation
- Inputs: structured arguments, queries, prior state
- Outputs: raw return value, new state
- Timing: start time, end time, duration
- Errors and retries: typed error state, retry count, parent retry context
- Identifiers: trace ID, parent span ID, session ID, user/tenant ID

### MLflow Tracing

MLflow separates **TraceInfo** (metadata) from **TraceData** (list of spans):

**TraceInfo**: trace_id, trace_location, request_time, execution_duration, state (OK/ERROR/IN_PROGRESS), request_preview, response_preview, trace_metadata, tags, assessments

**Span**: span_id, trace_id, parent_id, name, status, start_time_ns, end_time_ns, inputs (JSON), outputs (JSON), attributes (dict), events (list)

Compatible with OpenTelemetry Span spec. Uses nanosecond timestamps.

### promptfoo

Stores evals in `~/.promptfoo/promptfoo.db` (SQLite via Drizzle ORM). Schema includes `evals` table with timestamps and results. Designed for eval comparison rather than trace replay — less relevant to our use case but validates SQLite as the storage choice for dev tooling.

### OpenAI Agents SDK (AdvancedSQLiteSession)

Most directly relevant — stores multi-turn agent conversations in SQLite:

**message_structure table:**
- `id`, `session_id`, `message_id`, `branch_id`
- `message_type` (user, assistant, tool_call)
- `sequence_number` — global ordering
- `user_turn_number` — which user input prompted this
- `branch_turn_number` — turn within branch
- `tool_name` — if tool call message
- `created_at`

**turn_usage table:**
- `id`, `session_id`, `branch_id`, `user_turn_number`
- `requests` — count of API requests
- `input_tokens`, `output_tokens`, `total_tokens`
- `input_tokens_details` (JSON) — detailed breakdown
- `output_tokens_details` (JSON) — detailed breakdown
- `created_at`

Key insight: Separates **message structure** from **usage tracking**. Usage is aggregated per turn, not per individual API call.

### Hermes Agent

SQLite schema (version 11) with:

**sessions table**: IDs, source (cli/telegram/discord), user ID, model config, token counts (input, output, cache_read, cache_write, reasoning), billing info (provider, base_url, mode, costs), parent_session_id for lineage, title

**messages table**: role, content, timestamps, token counts, tool_calls (JSON string), tool_name, reasoning text, reasoning_details (JSON), finish_reason

**messages_fts**: FTS5 full-text search on content, tool names, tool calls

Key insight: Stores **cache_read** and **cache_write** token counts separately — critical for Anthropic cost tracking.

### TapeAgents (ServiceNow)

Uses a "Tape" abstraction — a structured, replayable log where:
- Each **Step** has metadata about the agent, the node responsible, and the prompt that led to its creation
- Steps can be: thoughts, actions, observations, tool calls
- Each step links back to the exact LLM call that generated it
- Tapes have parent tapes (for branching/forking)
- The same tape serves as memory during execution AND as replay artifact afterward

Key insight: The tape is both the **execution log** and the **input to the agent** — perfect for our replay requirement.

### agent-replay

A CLI tool specifically for "time-travel debugging AI agents":
- Stores traces in a single SQLite file (`.agent-replay/traces.db`)
- Supports replay, behavioral diffing, forking runs to test fixes
- Works with any agent framework via JSON trace import

---

## 2. Schema Design Trade-offs

### Flat vs Normalized

| Approach | Pros | Cons |
|----------|------|------|
| **Flat** (one row per phase) | Simple queries, easy export, fast reads | Loses turn-level granularity, bloated rows for 30-turn conversations |
| **Fully normalized** (runs → turns → messages → tool_calls) | Maximum queryability, clean data model | Many JOINs for replay, complex inserts |
| **Hybrid** (phases + messages, tool calls embedded as JSON) | Good balance, easy replay, reasonable queries | JSON fields harder to query/index |

**Industry trend**: Langfuse is moving FROM normalized TO denormalized. LangSmith uses a single Run type with hierarchy. The consensus is that **deep normalization hurts more than it helps** for trace data because:
1. Trace data is write-heavy, read-occasionally
2. The primary read pattern is "give me everything for this run" (full scan)
3. Analytical queries can use JSON functions or materialized views

### Storing Prompts/Responses

**As blobs (TEXT/JSON columns)**: LangSmith, Langfuse, MLflow all store inputs/outputs as JSON strings. Langfuse uses ZSTD compression. This is the universal pattern.

**Structured columns**: Only for metadata extracted from the content (token counts, model name, finish reason). Never for the actual content.

**Recommendation**: Store full content as TEXT (SQLite handles this well), extract queryable metadata into proper columns.

### Multi-turn Conversations

Three patterns observed:

1. **Sequence number** (OpenAI Agents SDK): `sequence_number` + `user_turn_number`. Simple, works well for linear conversations.

2. **Parent-child hierarchy** (LangSmith, Langfuse): `parent_id` creates a tree. Better for branching/parallel tool calls.

3. **Tape/log order** (TapeAgents, Claude CLI JSONL): Append-only log, order is implicit from insertion. Simplest for replay.

For our use case (linear multi-turn, no branching): **sequence number is sufficient**. Add `parent_id` only if we need to represent parallel tool calls within a turn.

### Token Counts and Cost Tracking

Industry consensus: track at **multiple granularities simultaneously**:

| Level | What to store |
|-------|--------------|
| Per-message | input_tokens, output_tokens, cache_read_tokens, cache_creation_tokens |
| Per-turn | Aggregated from messages (a turn may have multiple API calls for tool use) |
| Per-phase | SUM of all turns in phase |
| Per-run | SUM of all phases (can be computed, doesn't need storage) |

Critical fields for Anthropic/Claude specifically:
- `cache_read_input_tokens` — costs 10% of standard input price
- `cache_creation_input_tokens` — costs 25% more than standard input
- `input_tokens`, `output_tokens` — standard pricing

Cost should be **computed at write time** and stored (Langfuse pattern) because pricing changes over time — the price at execution time is what matters.

### Tool Calls and Tool Results

Two patterns:

1. **Inline in messages** (Claude CLI, Hermes): `tool_calls` JSON column on the message row, paired with a subsequent message of role `tool` containing the result.

2. **Separate span** (LangSmith, Langfuse, OpenTelemetry): Each tool call is its own span with `parent_id` pointing to the LLM call that generated it.

For replay fidelity, **inline storage** (pattern 1) is better — it exactly mirrors the API wire format. For analysis ("which tools are slowest?"), separate spans (pattern 2) are better.

**Recommendation**: Store the full message array (including tool_use and tool_result content blocks) as-is in a messages table, AND extract tool call metadata into a lightweight index table for queries.

### Agent Actions for Replay

Claude CLI already stores complete JSONL transcripts with every tool_use and tool_result. The key fields per action:

- Tool name (Read, Write, Edit, Bash, WebSearch, etc.)
- Input arguments (file path, command, search query)
- Output/result (file contents, command output, search results)
- Duration
- Whether it succeeded or errored

For replay: store the **exact content blocks** from the Claude API response. This is the source of truth.

---

## 3. What Metadata Matters

### Commonly Tracked (Don't Miss These)

| Category | Fields |
|----------|--------|
| **Git state** | branch, commit_hash, is_dirty, dirty_files list |
| **Model config** | model_id, temperature, max_tokens, top_p, system_prompt_hash |
| **Timing** | wall_clock_start, wall_clock_end, total_model_latency_ms, total_tool_latency_ms |
| **Environment** | machine_id, OS, working_directory, env_vars (filtered) |
| **Pipeline context** | issue_number, issue_url, issue_title, PR_number (if created) |
| **Agent identity** | agent_version, prompt_version/hash, tools_available |
| **Outcome** | phase_result (success/fail/skip), error_message, exit_code |
| **Retry context** | retry_count, parent_run_id (if retry of failed run) |
| **Session** | session_id (groups related runs), run_id |

### Often Overlooked But Valuable

- **Finish reason per message**: `stop`, `max_tokens`, `tool_use` — reveals if the model was cut off
- **Context window utilization**: tokens_used / max_context_window — shows pressure
- **Cache effectiveness**: cache_read_tokens / total_input_tokens ratio
- **Tool call count per turn**: reveals agent efficiency
- **Thinking/reasoning tokens**: Claude extended thinking token counts (separate from output)
- **Rate limit events**: 429 responses, retry delays
- **Content truncation markers**: whether tool results were truncated before sending

### OpenTelemetry GenAI Semantic Conventions (Standardized)

The OTEL GenAI SIG defines these standard attributes:
- `gen_ai.operation.name` (chat, text_completion)
- `gen_ai.provider.name` (anthropic)
- `gen_ai.request.model` / `gen_ai.response.model`
- `gen_ai.usage.input_tokens` / `gen_ai.usage.output_tokens`
- `gen_ai.usage.cache_read.input_tokens` / `gen_ai.usage.cache_creation.input_tokens`
- `gen_ai.response.finish_reasons`
- `gen_ai.tool.name` / `gen_ai.tool.call.arguments` / `gen_ai.tool.call.result`
- `gen_ai.client.operation.duration`

---

## 4. Queryability

### Common Queries Against Run Data

| Query | Schema Implications |
|-------|-------------------|
| "Show me all runs where review failed" | Need: phase_name + outcome as indexed columns |
| "What's the average cost per phase?" | Need: cost_usd as a numeric column per phase (not buried in JSON) |
| "Find runs for issue #123" | Need: issue_number as indexed column on the run |
| "What's the total cost of this run?" | Need: SUM over phase costs — easy if cost is a column |
| "Show me the execute phase conversation" | Need: messages retrievable by phase |
| "Which runs hit rate limits?" | Need: events/errors table or flag column |
| "What's the p95 latency for the plan phase?" | Need: duration_ms per phase as a numeric column |
| "Show me all tool calls that failed" | Need: tool_calls table or JSON extraction |
| "Compare token usage across runs" | Need: token counts as numeric columns, not just JSON |
| "Find runs where the agent exceeded 20 turns" | Need: turn_count per phase |

### What Makes Queries Easy

1. **Numeric columns for metrics** — never bury costs/tokens/durations inside JSON blobs
2. **Phase name as an indexed column** — enables instant filtering
3. **Outcome/status as enum column** — enables WHERE clauses without JSON parsing
4. **Issue number at the run level** — avoids needing to parse inputs

### What Makes Queries Hard

1. Storing everything in one big JSON blob per phase
2. No pre-computed aggregates (forces scanning all messages to get phase cost)
3. Deeply nested structures requiring multiple JOINs for simple questions

---

## 5. Recommended Schema

### Option A: Minimal (3 tables)

```sql
-- The run itself
CREATE TABLE run (
    id TEXT PRIMARY KEY,           -- run UUID (matches filename)
    issue_number INTEGER,
    issue_url TEXT,
    issue_title TEXT,
    repo TEXT,
    git_branch TEXT,
    git_commit TEXT,
    git_dirty INTEGER,             -- boolean
    started_at TEXT,               -- ISO-8601
    finished_at TEXT,
    outcome TEXT,                  -- success | failure | partial
    total_cost_usd REAL,
    total_input_tokens INTEGER,
    total_output_tokens INTEGER,
    total_cache_read_tokens INTEGER,
    total_cache_creation_tokens INTEGER,
    total_duration_ms INTEGER,
    pr_number INTEGER,             -- NULL if no PR created
    pr_url TEXT,
    error_summary TEXT,
    metadata JSON                  -- overflow bag for anything else
);

-- One row per pipeline phase
CREATE TABLE phase (
    id INTEGER PRIMARY KEY,
    run_id TEXT REFERENCES run(id),
    name TEXT NOT NULL,            -- triage | plan | test-plan | wave-plan | execute | review
    seq INTEGER NOT NULL,          -- 0-based phase order
    model TEXT,                    -- claude-sonnet-4-20250514
    started_at TEXT,
    finished_at TEXT,
    duration_ms INTEGER,
    outcome TEXT,                  -- success | failure | skipped
    turn_count INTEGER,
    cost_usd REAL,
    input_tokens INTEGER,
    output_tokens INTEGER,
    cache_read_tokens INTEGER,
    cache_creation_tokens INTEGER,
    error_message TEXT,
    conversation JSON              -- FULL message array (content blocks, tool calls, everything)
);

-- Index for common queries
CREATE INDEX idx_phase_name ON phase(name);
CREATE INDEX idx_phase_outcome ON phase(outcome);
```

**Pros**: Dead simple. Two tables cover everything. The `conversation` JSON column holds the full replay data.
**Cons**: Can't query individual messages/tool calls without JSON parsing. The `conversation` blob could be 1-10MB per phase for 30-turn agents.

### Option B: Balanced (5 tables)

```sql
CREATE TABLE run (
    id TEXT PRIMARY KEY,
    issue_number INTEGER,
    issue_url TEXT,
    issue_title TEXT,
    repo TEXT,
    git_branch TEXT,
    git_commit TEXT,
    git_dirty INTEGER,
    started_at TEXT,
    finished_at TEXT,
    outcome TEXT,
    total_cost_usd REAL,
    total_input_tokens INTEGER,
    total_output_tokens INTEGER,
    total_cache_read_tokens INTEGER,
    total_cache_creation_tokens INTEGER,
    total_duration_ms INTEGER,
    pr_number INTEGER,
    pr_url TEXT,
    error_summary TEXT,
    config JSON                    -- pipeline config, model params, agent versions
);

CREATE TABLE phase (
    id INTEGER PRIMARY KEY,
    run_id TEXT REFERENCES run(id),
    name TEXT NOT NULL,
    seq INTEGER NOT NULL,
    model TEXT,
    system_prompt_hash TEXT,       -- detect prompt drift
    started_at TEXT,
    finished_at TEXT,
    duration_ms INTEGER,
    outcome TEXT,
    turn_count INTEGER,
    cost_usd REAL,
    input_tokens INTEGER,
    output_tokens INTEGER,
    cache_read_tokens INTEGER,
    cache_creation_tokens INTEGER,
    error_message TEXT
);

-- Every message in the conversation (faithful to Claude API format)
CREATE TABLE message (
    id INTEGER PRIMARY KEY,
    phase_id INTEGER REFERENCES phase(id),
    turn_number INTEGER,          -- user turn (increments on each user message)
    seq INTEGER NOT NULL,         -- global order within phase
    role TEXT NOT NULL,           -- user | assistant | system
    content JSON NOT NULL,        -- array of content blocks (text, tool_use, tool_result, thinking)
    input_tokens INTEGER,         -- tokens for this specific API call
    output_tokens INTEGER,
    cache_read_tokens INTEGER,
    cache_creation_tokens INTEGER,
    cost_usd REAL,
    model TEXT,                   -- model used for this specific call
    finish_reason TEXT,           -- stop | max_tokens | tool_use
    duration_ms INTEGER,          -- model response latency
    created_at TEXT
);

-- Extracted tool call index (denormalized for queryability)
CREATE TABLE tool_call (
    id INTEGER PRIMARY KEY,
    message_id INTEGER REFERENCES message(id),
    phase_id INTEGER REFERENCES phase(id),
    tool_name TEXT NOT NULL,       -- Read, Write, Edit, Bash, WebSearch, etc.
    input_summary TEXT,            -- first 500 chars of input (for browsing)
    input JSON,                    -- full tool input arguments
    output_summary TEXT,           -- first 500 chars of output
    output JSON,                   -- full tool result
    duration_ms INTEGER,
    success INTEGER,              -- boolean
    error TEXT
);

-- Events/signals that don't fit the message model
CREATE TABLE event (
    id INTEGER PRIMARY KEY,
    phase_id INTEGER,
    type TEXT NOT NULL,            -- rate_limit | error | retry | checkpoint | user_signal
    timestamp TEXT,
    details JSON
);

CREATE INDEX idx_phase_name ON phase(name);
CREATE INDEX idx_phase_outcome ON phase(outcome);
CREATE INDEX idx_message_phase ON message(phase_id, seq);
CREATE INDEX idx_tool_call_name ON tool_call(tool_name);
CREATE INDEX idx_tool_call_phase ON tool_call(phase_id);
CREATE INDEX idx_event_type ON event(type);
```

**Pros**: Full queryability. Can answer "which tools failed?" or "average cost per turn" without JSON parsing. Messages table is the replay source of truth. Tool call index enables analysis.
**Cons**: More complex writes. The tool_call table partially duplicates data in message.content. 5 tables is more to manage.

### Option C: Document-Oriented Hybrid (3 tables + views)

```sql
CREATE TABLE run (
    id TEXT PRIMARY KEY,
    issue_number INTEGER,
    issue_url TEXT,
    issue_title TEXT,
    repo TEXT,
    git_branch TEXT,
    git_commit TEXT,
    git_dirty INTEGER,
    started_at TEXT,
    finished_at TEXT,
    outcome TEXT,
    total_cost_usd REAL,
    total_input_tokens INTEGER,
    total_output_tokens INTEGER,
    total_duration_ms INTEGER,
    pr_number INTEGER,
    pr_url TEXT,
    error_summary TEXT,
    metadata JSON
);

CREATE TABLE phase (
    id INTEGER PRIMARY KEY,
    run_id TEXT REFERENCES run(id),
    name TEXT NOT NULL,
    seq INTEGER NOT NULL,
    model TEXT,
    started_at TEXT,
    finished_at TEXT,
    duration_ms INTEGER,
    outcome TEXT,
    turn_count INTEGER,
    cost_usd REAL,
    input_tokens INTEGER,
    output_tokens INTEGER,
    cache_read_tokens INTEGER,
    cache_creation_tokens INTEGER,
    tool_call_count INTEGER,
    error_message TEXT,
    -- The full tape: ordered array of steps (messages + tool calls interleaved)
    tape JSON NOT NULL
);

-- Materialized index of tool calls extracted from tape JSON
CREATE TABLE tool_call_index (
    phase_id INTEGER REFERENCES phase(id),
    seq INTEGER,                  -- position in tape
    tool_name TEXT,
    success INTEGER,
    duration_ms INTEGER,
    PRIMARY KEY (phase_id, seq)
);

CREATE INDEX idx_phase_name ON phase(name);
CREATE INDEX idx_phase_outcome ON phase(outcome);
CREATE INDEX idx_tool_name ON tool_call_index(tool_name);

-- Convenience view: phase summary with run context
CREATE VIEW phase_summary AS
SELECT
    r.issue_number, r.repo, r.outcome as run_outcome,
    p.name as phase, p.outcome as phase_outcome,
    p.cost_usd, p.duration_ms, p.turn_count, p.tool_call_count
FROM phase p JOIN run r ON p.run_id = r.id;
```

**Pros**: The `tape` JSON is the single source of truth for replay (TapeAgents pattern). Numeric columns on phase enable all aggregate queries. The tool_call_index is lightweight (no content duplication). Views make common queries trivial.
**Cons**: Individual message queries require JSON extraction. The tape blob can be large.

---

## Recommendation: Option B (Balanced)

**Why Option B wins for our use case:**

1. **Replay fidelity**: The `message` table stores the exact Claude API content blocks in order. Replaying a phase = SELECT messages WHERE phase_id=? ORDER BY seq. No JSON array parsing needed.

2. **Queryability without compromise**: "Average cost per phase" is a one-liner. "Which tools failed?" is a one-liner. "Show me turn 5 of the execute phase" is a one-liner. No JSON extraction functions needed for the common queries.

3. **Per-message cost tracking**: Claude's pricing with cache hits means each API call has different effective rates. Storing cost per message means we can answer "which turn was most expensive?" and spot cache misses.

4. **Tool call analysis**: The denormalized `tool_call` table is write-once (populated as the agent runs) and enables powerful analysis: "How many Bash commands did the execute phase run?" "What files did it edit?" "Which tool calls took >10s?"

5. **Self-contained replay**: One .db file contains everything needed to reconstruct the full run. No external references. The `message.content` JSON is the wire format.

6. **Manageable complexity**: 5 tables is not excessive. The write pattern is append-only (messages stream in as the agent runs). No updates needed after initial write.

7. **SQLite strengths**: SQLite handles TEXT columns up to 1GB. JSON functions (json_extract, json_each) are available for ad-hoc queries into content blocks. WAL mode handles concurrent reads during active writes.

**Implementation notes:**
- Use WAL mode (`PRAGMA journal_mode=WAL`)
- Use `PRAGMA foreign_keys=ON`
- Write messages as they arrive (streaming insert)
- Compute phase/run aggregates at phase completion
- The tool_call table can be populated lazily (after message insert) or eagerly (during)
- Consider `PRAGMA page_size=8192` for the large JSON content blocks

---

## Sources

- [LangSmith Tracing Deep Dive](https://medium.com/@aviadr1/langsmith-tracing-deep-dive-beyond-the-docs-75016c91f747)
- [SmithDB: The Data Layer for Agent Observability](https://www.langchain.com/blog/introducing-smithdb)
- [Langfuse Data Model](https://langfuse.com/docs/observability/data-model)
- [Langfuse ClickHouse Schema (DeepWiki)](https://deepwiki.com/langfuse/langfuse/3.3-data-visualization)
- [Braintrust Agent Observability Guide 2026](https://www.braintrust.dev/articles/agent-observability-complete-guide-2026)
- [Brainstore Database](https://www.braintrust.dev/blog/brainstore)
- [MLflow Trace Data Structure](https://mlflow.org/docs/3.0.1/tracing/tracing-schema)
- [OpenTelemetry GenAI Semantic Conventions](https://greptime.com/blogs/2026-05-09-opentelemetry-genai-semantic-conventions)
- [OpenTelemetry GenAI Observability Blog](https://opentelemetry.io/blog/2026/genai-observability/)
- [TapeAgents Framework](https://medium.com/@EleventhHourEnthusiast/tapeagents-a-holistic-framework-for-agent-development-and-optimization-93b4110b5c41)
- [OpenAI Agents SDK AdvancedSQLiteSession](https://openai.github.io/openai-agents-python/sessions/advanced_sqlite_session/)
- [Hermes Agent Session Storage](https://hermes-agent.nousresearch.com/docs/developer-guide/session-storage)
- [agent-replay](https://github.com/clay-good/agent-replay)
- [SQLite for Durable Workflows (Obelisk)](https://obeli.sk/blog/sqlite-is-all-you-need-for-durable-workflows/)
- [Claude Code Cost Tracking](https://code.claude.com/docs/en/agent-sdk/cost-tracking)
- [Claude API Pricing](https://platform.claude.com/docs/en/about-claude/pricing)
- [Braintrust Token Usage Tracking](https://www.braintrust.dev/articles/how-to-track-llm-token-usage-2026)
- [Promptfoo via Simon Willison](https://simonwillison.net/2025/Apr/24/exploring-promptfoo/)
- [SQLite Is the Best Database for AI Agents](https://dev.to/nathanhamlett/sqlite-is-the-best-database-for-ai-agents-and-youre-overcomplicating-it-1a5g)
