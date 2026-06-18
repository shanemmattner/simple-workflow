# Zhipu AI (GLM) Setup Guide

**Date**: 2026-06-17  
**Status**: API key confirmed working. `glm-4.5` through `glm-4.7` accessible. GLM-5 returns "过大" (overloaded), try again later.

---

## 1. What Zhipu AI Actually Is

Zhipu AI is a Chinese AI company (Tsinghua-origin). Their API is served via:
- **China endpoint**: `https://open.bigmodel.cn` (your current API key routes here)
- **International endpoint**: `https://api.z.ai` (newer branding; key may not work here yet)

The API key format (`b9db9e...`) is a bigmodel.cn key. The "余额不足" error was on paid models (`glm-4.7`, `glm-4.5-air` via OpenAI endpoint) — but the **Anthropic-compatible endpoint bypasses balance restrictions** for models included in a Coding Plan subscription. See Section 4 for why.

---

## 2. Available Models (Confirmed from API)

```
GET https://open.bigmodel.cn/api/paas/v4/models
```

| Model | Status with your key | Cost (per 1M tokens) | Context |
|-------|---------------------|---------------------|---------|
| glm-4.5-flash | FREE, works | Free | — |
| glm-4.5-air | Works via /api/anthropic | $0.20 in / $1.10 out | 131K |
| glm-4.5 | Works via /api/anthropic | $0.60 in / $2.20 out | 131K |
| glm-4.6 | Works via /api/anthropic | $0.60 in / $2.20 out | 205K |
| glm-4.7 | Works via /api/anthropic | $0.60 in / $2.20 out | 205K |
| glm-5 | Overloaded (try later) | $1.00 in / $3.20 out | 203K |
| glm-5-turbo | Listed | $1.20 in / $4.00 out | 203K |
| glm-5.1 | Listed | $1.40 in / $4.40 out | 203K |
| glm-5.2 | Listed (API opens ~June 22) | TBD | 1M |

**Free models**: `glm-4.5-flash` (genuinely free, no balance needed, 203K context).

---

## 3. How to Add Credits

The "余额不足" error = zero balance on the pay-as-you-go API. Two paths:

### Option A: Add API Credits (pay-as-you-go)
1. Log into https://open.bigmodel.cn
2. Go to 充值 (Recharge) or 个人中心 → 账户充值
3. Add CNY via Alipay/WeChat Pay (approx ¥7 = $1 USD)
4. Even ¥10 ($1.40) unblocks all models

### Option B: GLM Coding Plan (subscription, recommended)
Subscribe at https://bigmodel.cn/glm-coding or https://z.ai

| Plan | Quarterly Price | Monthly Equiv | Models Included |
|------|----------------|---------------|-----------------|
| Lite | $27/quarter (Q2 discount) | ~$10/mo | GLM-5.1, GLM-5-Turbo, GLM-4.7, GLM-4.6, GLM-4.5-Air |
| Pro | $81/quarter | ~$30/mo | Everything + GLM-5 |
| Max | $216/quarter | ~$80/mo | Everything, 4x volume |

**Important**: The Coding Plan uses the `/api/anthropic` endpoint (not `/api/paas/v4`). This is why `glm-4.5-air`, `glm-4.6`, `glm-4.7` already work on your key via the Anthropic endpoint — your key appears to be a Coding Plan key.

---

## 4. Endpoint Architecture

Two separate API surfaces:

### OpenAI-compatible (pay-as-you-go billing)
```
POST https://open.bigmodel.cn/api/paas/v4/chat/completions
Authorization: Bearer $ZAI_API_KEY
```
This requires token balance. Gives "余额不足" if empty.

### Anthropic-compatible (Coding Plan billing)
```
POST https://open.bigmodel.cn/api/anthropic/v1/messages
x-api-key: $ZAI_API_KEY
anthropic-version: 2023-06-01
```
This uses the Coding Plan quota. **This is what works with your key today.**

Confirmed working (2026-06-17 test):
- `glm-4.5-flash` → returns valid Anthropic-format response, FREE
- `glm-4.5-air` → OK
- `glm-4.5` → OK
- `glm-4.6` → OK
- `glm-4.7` → OK
- `glm-5` → "访问量过大" (overloaded, retry later)

---

## 5. Using with Claude Code

Claude Code supports custom endpoints via `ANTHROPIC_BASE_URL`. Set it to Zhipu's Anthropic-compatible base:

### ~/.claude/settings.json (recommended)
```json
{
  "env": {
    "ANTHROPIC_AUTH_TOKEN": "<your ZAI_API_KEY>",
    "ANTHROPIC_BASE_URL": "https://open.bigmodel.cn/api/anthropic",
    "API_TIMEOUT_MS": "3000000",
    "ANTHROPIC_DEFAULT_HAIKU_MODEL": "glm-4.5-air",
    "ANTHROPIC_DEFAULT_SONNET_MODEL": "glm-4.7",
    "ANTHROPIC_DEFAULT_OPUS_MODEL": "glm-4.7"
  }
}
```

### Shell env alternative
```bash
export ANTHROPIC_AUTH_TOKEN="$ZAI_API_KEY"
export ANTHROPIC_BASE_URL="https://open.bigmodel.cn/api/anthropic"
export ANTHROPIC_DEFAULT_HAIKU_MODEL="glm-4.5-air"
export ANTHROPIC_DEFAULT_SONNET_MODEL="glm-4.7"
export ANTHROPIC_DEFAULT_OPUS_MODEL="glm-4.7"
```

### Alias pattern (keep Claude Code separate)
```bash
# ~/.zshrc — add alongside ZAI_API_KEY export
alias claude-glm='ANTHROPIC_BASE_URL=https://open.bigmodel.cn/api/anthropic ANTHROPIC_AUTH_TOKEN=$ZAI_API_KEY claude'
```

**Caveats when using Claude Code with non-Anthropic endpoints:**
- Extended thinking (Claude's thinking blocks) not supported by GLM
- Some MCP features may behave differently
- Tool-use contract is mostly compatible but test critical paths
- `--model` flag in Claude Code still maps to Claude model names; use `ANTHROPIC_DEFAULT_*` vars to redirect

---

## 6. Integration with simple-workflow Pipeline

The pipeline at `engines/github_claude/runtime.py` calls `claude` CLI as a subprocess. It passes `--model <name>` from `workflow.yaml`. Two integration options:

### Option A: Environment-variable redirect (zero code change)

Set env vars before running the pipeline so `claude` routes to GLM. Add to `scripts/run.sh` or set in shell:

```bash
export ANTHROPIC_AUTH_TOKEN="$ZAI_API_KEY"
export ANTHROPIC_BASE_URL="https://open.bigmodel.cn/api/anthropic"
export ANTHROPIC_DEFAULT_HAIKU_MODEL="glm-4.5-air"
export ANTHROPIC_DEFAULT_SONNET_MODEL="glm-4.7"
export ANTHROPIC_DEFAULT_OPUS_MODEL="glm-4.7"
```

Then run the pipeline normally. Claude Code intercepts the model names and routes to GLM. The `--model claude-sonnet-4-6` flag in runtime.py will be overridden by `ANTHROPIC_DEFAULT_SONNET_MODEL`.

**Tradeoff**: cost tracking in `runtime.py` will be wrong (it reads `total_cost_usd` from Anthropic's JSON, but GLM returns 0.0 there). Token counts still work.

### Option B: Direct OpenAI SDK runtime (best for accurate cost tracking)

Add a new runtime: `engines/github_claude/glm_runtime.py` that calls the OpenAI-compatible endpoint directly (once API balance is loaded). This gives accurate token and cost data.

```python
"""GLM runtime — direct OpenAI-compatible API calls to Zhipu AI."""
from openai import OpenAI

def call_agent(prompt, *, model="glm-4.7", cwd, max_turns=30, timeout=600, system_prompt=None):
    client = OpenAI(
        api_key=os.environ["ZAI_API_KEY"],
        base_url="https://open.bigmodel.cn/api/paas/v4"
    )
    response = client.chat.completions.create(
        model=model,
        messages=[
            {"role": "system", "content": system_prompt or _DEFAULT_SYSTEM_PROMPT},
            {"role": "user", "content": prompt}
        ],
        max_tokens=8192,
    )
    # ... parse and return standard dict shape
```

**Tradeoff**: Loses Claude Code's agentic loop (file reading, bash execution). Only suitable for single-shot phases like triage and review, not execute.

### Option C: OpenRouter as proxy (if z.ai endpoint has access issues)

GLM-4.7 is available on OpenRouter at model ID `zhipu/glm-4.7`:
```
ANTHROPIC_BASE_URL=https://openrouter.ai/api/anthropic
ANTHROPIC_AUTH_TOKEN=<openrouter_key>
ANTHROPIC_DEFAULT_SONNET_MODEL=zhipu/glm-4.7
```

### Recommended approach: Option A

Use env-var redirect for now. It's zero code change and lets you validate GLM quality in the pipeline. Once validated, consider Option B for phases that don't need the agentic loop (triage, review).

---

## 7. Quick Test

```bash
source ~/.zshrc

# Confirm the key works
curl -s -X POST "https://open.bigmodel.cn/api/anthropic/v1/messages" \
  -H "x-api-key: $ZAI_API_KEY" \
  -H "anthropic-version: 2023-06-01" \
  -H "Content-Type: application/json" \
  -d '{"model":"glm-4.7","max_tokens":50,"messages":[{"role":"user","content":"Say hello"}]}' | python3 -m json.tool

# Run Claude Code against GLM
ANTHROPIC_BASE_URL=https://open.bigmodel.cn/api/anthropic \
ANTHROPIC_AUTH_TOKEN=$ZAI_API_KEY \
claude --model claude-sonnet-4-6 "What model are you?"
# GLM will respond as itself despite the claude-sonnet-4-6 model string
```

---

## 8. Cost Comparison vs Anthropic

For a typical pipeline run (triage + plan + execute + review):

| Model pair | Approx cost/run | Notes |
|-----------|----------------|-------|
| Claude Sonnet 4.6 + Haiku 4.5 | $0.20–$1.00 | current pipeline |
| GLM-4.7 + GLM-4.5-Air | $0.02–$0.10 | ~10x cheaper |
| GLM-4.5-Flash (free) | $0.00 | quality untested for agentic work |

GLM-5 would be ~3x cheaper than Sonnet for input tokens, 5x for output.

---

## 9. Known Issues

- **GLM-5 overloaded**: Error code 1305 "访问量过大". Retry. Not a balance issue.
- **reasoning_content leaks**: GLM-4.5-flash returns `reasoning_content` in responses with the actual answer in `content` sometimes empty. Filter `reasoning_content` or use `glm-4.7` which is cleaner.
- **Cost tracking**: `runtime.py` reads `total_cost_usd` from Claude CLI JSON. When routing via GLM, this will be 0.0. Token counts still populate via `usage`.
- **Tool use compatibility**: GLM supports function calling but the Claude Code agentic loop tool-use format (computer_use, text_editor) may not be fully compatible. Test before relying on execute phase.
- **Rate limits**: Free tier gets rate-limited quickly (error 1302). Coding Plan has higher limits.

---

## 10. Next Steps

1. **Top up balance OR subscribe** to Coding Plan Lite (~$10/mo) at https://bigmodel.cn/glm-coding
2. **Test quality** with Option A (env var redirect) on a real pipeline run against a simple issue
3. **GLM-5.2 (June 22)**: 1M context, MIT weights, likely same Anthropic endpoint. Watch https://z.ai for API key issuance.
4. If quality is acceptable, update `workflow.yaml` to document GLM model names in comments and add a `--provider glm` flag to `run.sh`
