# github_openhands engine

Fork of `github_claude` that replaces the Claude CLI subprocess runtime with the [OpenHands SDK](https://github.com/OpenHands/software-agent-sdk). Uses DeepSeek V4 Flash via OpenRouter as the default model.

## What changed from github_claude

Only `runtime.py` differs. Everything else (orchestrator, source, destination, workspace, storage, gates, eval) is identical.

| | github_claude | github_openhands |
|---|---|---|
| **Runtime** | `claude` CLI subprocess | OpenHands SDK (`openhands.sdk.Conversation`) |
| **Default model** | Claude Sonnet (subscription) | DeepSeek V4 Flash via OpenRouter |
| **Auth** | Claude CLI subscription login | `OPENROUTER_API_KEY` env var |
| **Tools** | Claude's built-in tools | OpenHands TerminalTool + FileEditorTool |
| **Docker** | Not needed | Not needed (local workspace mode) |

## How to run

```bash
python -m engines.github_openhands owner/repo#123
python -m engines.github_openhands owner/repo#123 --budget 2.00
python -m engines.github_openhands owner/repo#123 --model deepseek/deepseek-v4-pro
```

Or via the project wrapper:

```bash
./scripts/run.sh owner/repo#123 --engine openhands
```

## Prerequisites

- **Python 3.12+**
- **`gh` CLI** -- authenticated (`gh auth status` should show logged in)
- **`OPENROUTER_API_KEY`** env var (or `LLM_API_KEY`)
- **Git** -- the target repo must be cloned locally
- **openhands-sdk + openhands-tools** installed:

```bash
pip install -U openhands-sdk openhands-tools
```

## Model shortcuts

The orchestrator's `workflow.yaml` uses short names like `sonnet`, `haiku`, `opus`. The runtime maps these to OpenRouter model strings:

| Short name | Model (OpenRouter format) |
|---|---|
| `sonnet` | `anthropic/claude-sonnet-4-5-20250929` |
| `haiku` | `anthropic/claude-3-5-haiku-20241022` |
| `opus` | `anthropic/claude-opus-4-20250514` |
| `deepseek` / `deepseek-flash` | `deepseek/deepseek-v4-flash` |
| `deepseek-pro` | `deepseek/deepseek-v4-pro` |

Any OpenRouter model string (e.g. `meta-llama/llama-4-maverick`) passes through unchanged. Do NOT use the `openrouter/` prefix — base_url is always set to OpenRouter's API endpoint.

## How it works

Same phase sequence as github_claude (triage, plan, test-plan, wave-planner, execute, review). The only difference is the `call_agent()` implementation:

1. Creates an `openhands.sdk.LLM` with the resolved model + API key
2. Creates an `Agent` with TerminalTool and FileEditorTool
3. Creates a `Conversation` pointed at the worktree directory
4. Sends the phase prompt and calls `conversation.run()`
5. Extracts content from the event log, metrics from `conversation_stats`
6. Returns the same `{content, tokens_in, tokens_out, cost, duration_s, finish_reason}` dict the orchestrator expects
