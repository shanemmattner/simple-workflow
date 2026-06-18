# Flat-Rate Coding Agent Subscriptions: Real Limits (June 2026)

The question: what subscriptions let you call a coding agent programmatically (headless/CLI, no human in the loop) for a flat monthly fee?

**TL;DR: Nothing is truly unlimited.** Every "flat rate" plan uses either rolling-window token budgets, weekly caps, or progressive throttling. The best options for pipeline use are Claude Code Max 20x, Cursor Ultra (headless CLI), and the Chinese-model flat-rate providers (GLM, Kimi, Qwen Cloud) which offer 3-10x more throughput per dollar but weaker coding ability.

---

## Tier 1: Frontier Coding Agents (Best Quality)

### Claude Code Max 20x — $200/mo

| Metric | Value |
|--------|-------|
| Price | $200/mo individual, $100/seat Team Premium (min 5 seats) |
| Models | Sonnet 4.6, Opus 4.7 (your choice per session) |
| 5-hour window | ~200-900 prompts (Sonnet), far fewer on Opus |
| Weekly cap | 240-480 Sonnet hrs OR 24-40 Opus hrs |
| Concurrent sessions | Technically unlimited, but all draw from one pool |
| Headless/CLI | Yes — `claude -p` pipes prompts, `--permission-mode auto` for no-human |
| Multi-file coding | Best in class. Full filesystem access, test running, git ops |

**Real capacity:** Heavy agentic use (Opus, large context) burns through the 5-hour window in 60-90 minutes. Sonnet-only workflows last much longer. Weekly cap is the binding constraint — the per-window doubling on May 6, 2026 did NOT increase the weekly budget.

**Scaling tricks:**
- Multiple terminal sessions share one pool — no benefit from parallel sessions beyond convenience
- Team plan Premium seats ($100/seat/mo, min 5 seats = $500/mo) each get their OWN pool at 6.25x Pro limits
- Enterprise: negotiated pricing, pooled across org, Bedrock/Vertex failover for extra capacity
- Usage credits: buy overage at API rates when you hit the cap

**Verdict:** Best coding agent quality. Not unlimited. Power users report hitting limits in 4-6 hours of heavy daily use on the $200 plan.

---

### OpenAI Codex (Cloud Agent) — $100-200/mo (ChatGPT Pro)

| Metric | Value |
|--------|-------|
| Price | Pro 5x: $100/mo, Pro 20x: $200/mo |
| Models | o4-mini, GPT-5.3-Codex-Spark, o3 |
| 5-hour window | ~150-750 cloud tasks (Pro 5x), higher on Pro 20x |
| Headless/CLI | Yes — `codex exec` for non-interactive, also Codex cloud tasks via API |
| Sandbox | Cloud-sandboxed (reads your repo, runs in container) |
| Multi-file coding | Good but sandboxed — can't touch local filesystem directly |
| Concurrent | Supports parallel cloud tasks |

**Key difference from Claude Code:** Codex cloud agent runs in an isolated sandbox. It clones your repo, makes changes, runs tests, returns results asynchronously. The CLI (`codex exec`) runs locally with `--full-auto` flag.

**Real limits:** Rolling 5-hour window. "Unlimited subject to abuse guardrails" is marketing — actual caps exist. No published weekly cap but users report throttling after sustained heavy use.

**Verdict:** Good for async batch work. Sandbox model means it can't interact with your local dev environment the way Claude Code can. Quality slightly below Claude for complex multi-file edits.

---

### Cursor Ultra — $200/mo

| Metric | Value |
|--------|-------|
| Price | Pro: $20, Pro+: $60, Ultra: $200 |
| Models | Claude Sonnet/Opus, GPT-4o, Gemini 2.5 Pro, cursor-small |
| Credits | $200 in monthly credits (usage-based, model-dependent) |
| Headless/CLI | Yes — `cursor --headless "prompt" --branch fix/auth` |
| Multi-file coding | Excellent — full agent mode with file edits, terminal, MCP |
| Auth | API key based (`CURSOR_API_KEY`) |

**How credits work:** Every model call costs different amounts. Sonnet is cheap (~$3/MTok input), Opus is expensive. $200 in credits running Sonnet-only lasts much longer than running Opus. There's no "prompt count" — it's pure dollar-equivalent usage.

**Headless mode is real:** `agent -p --force "your prompt"` runs without IDE. Supports `--stream-partial-output` for progress tracking. JSON output mode available. Shares quota with IDE usage.

**Verdict:** Legitimate pipeline option. The $200 credit pool goes far on cheaper models. Headless CLI is production-ready. Main risk: credits deplete faster on frontier models.

---

## Tier 2: Platform Agents (Varying Quality)

### Google Antigravity (was Gemini CLI) — $20-200/mo

| Metric | Value |
|--------|-------|
| Price | Free (rate-limited), Pro: $20/mo, Ultra: $200/mo |
| Models | Gemini 3.1 Pro, Gemini 3.5 Flash |
| Limits | 5-hour rolling compute budget (not prompt-counted) |
| Headless/CLI | Yes — Go-based CLI, `antigravity exec` |
| Multi-file coding | Decent but less mature than Claude Code |
| Concurrency | Not documented |

**Key change (June 18, 2026):** Gemini CLI deprecated. Must use Antigravity CLI. Google AI Pro/Ultra subscriptions grant higher daily limits automatically.

**Pay-as-you-go top-up:** $25 for 2,500 credits when you hit your limit.

**Verdict:** Cheap entry point. Gemini 3.x is competent but not Claude/GPT-5 tier for complex multi-file work. Good as overflow/secondary agent.

---

### Augment Code (Auggie CLI) — $20-200/mo

| Metric | Value |
|--------|-------|
| Price | Indie: $20/mo (40K credits), Standard: $60/mo (130K credits), Max: $200/mo (450K credits) |
| Models | Proprietary (trained on code specifically) |
| Headless/CLI | Yes — `auggie "prompt"`, pipes, `--print` flag for scripting |
| Multi-file coding | Good — full codebase awareness, safe edits |
| Overage | Auto top-up at $15 per 24K credits |

**Programmatic use:** `auggie "Generate test data" > output.json` or `git diff | auggie "Explain changes"`. Non-interactive mode with `--print`.

**Verdict:** Credit-based, not truly flat rate. 450K credits at $200/mo goes far for routine tasks but burns fast on large-context agent sessions. Good codebase understanding.

---

### Amazon Q Developer Pro — $19/user/mo

| Metric | Value |
|--------|-------|
| Price | $19/user/mo |
| Models | Amazon's internal models (not disclosed) |
| Limits | High but unpublished limits for agentic requests |
| Headless/CLI | Limited — primarily IDE-integrated |
| Multi-file coding | Basic agent capabilities, mostly autocomplete + chat |
| IP indemnity | Yes — Amazon defends against IP claims |

**Verdict:** Cheapest per-seat option. Not a serious coding agent for pipeline use. More of an autocomplete/chat assistant. No meaningful headless/programmatic mode for autonomous multi-file work.

---

### GitHub Copilot Pro+ — $39/mo

| Metric | Value |
|--------|-------|
| Price | $39/mo (includes $70 in AI credits) |
| Models | GPT-4o, Claude Sonnet, Gemini |
| Limits | Credit-based; completions unlimited, agent/chat uses credits |
| Headless/CLI | No real headless agent mode |
| Multi-file coding | Agent mode exists but IDE-bound |

**PAUSED:** New Pro+ sign-ups paused since April 20, 2026. Moved to usage-based billing June 1, 2026. Not truly flat rate anymore.

**Verdict:** Not suitable for pipeline use. No headless agent. Credit system makes it usage-based, not flat rate.

---

## Tier 3: Chinese-Model Flat-Rate Providers (Best $/throughput)

These offer dramatically more throughput per dollar but with weaker models for complex English-language coding tasks.

### GLM Coding Plan — $10-160/mo

| Metric | Value |
|--------|-------|
| Price | $10-160/mo tiered |
| Models | GLM-5.1, GLM-4.7 |
| Limits | 80-1,600 prompts per 5-hour window |
| Headless | Yes — native Anthropic-compatible endpoint |
| Quality | Good for routine tasks, weaker on complex architecture |

### Qwen Cloud Pro — $50/mo

| Metric | Value |
|--------|-------|
| Price | $50/mo |
| Models | Qwen3.5-Plus + cross-model access |
| Limits | 6,000 requests per 5-hour window |
| Headless | Yes — Anthropic-compatible API endpoint |
| Quality | Strong for code generation, competitive with GPT-4o |

### Kimi Code — ~$19/mo + metered

| Metric | Value |
|--------|-------|
| Price | ~$19/mo base |
| Models | K2.6 (1T MoE) |
| Limits | 300-1,200 calls per 5-hour window |
| Headless | Yes — Claude Code compatible endpoint |
| Quality | Good reasoning, English coding capability improving |

**Verdict on Chinese providers:** 3-10x the throughput of Claude Max for 1/4-1/20 the price. The models are genuinely capable for straightforward tasks (write this function, fix this bug, add this test). They struggle with complex architectural decisions, nuanced English docs, and multi-step reasoning that Opus handles well. Best used as volume workers for routine pipeline stages while reserving Claude for hard problems.

---

## Tier 4: Not Flat Rate (Usage-Based with Subscription Access)

### Devin — $20-500/mo + ACU billing

NOT flat rate. $20/mo is just platform access. All work billed at $2-2.25 per ACU (1 ACU = ~15 min of agent work). A 2-hour coding session costs ~$18 in ACUs on top of subscription. Team plan ($500/mo) includes 250 ACUs (~62 hours of agent time).

### Windsurf / Devin Desktop — $20-200/mo (quota-based)

Retired credit system March 2026, now uses daily/weekly quotas. NOT unlimited. Tab completions are unlimited but Cascade agent calls are quota-limited. Merged with Devin (Cognition acquired Codeium). CLI 2.0 has headless CI/CD mode.

---

## Direct Answer: How to Get More Capacity

### Option 1: Multiple Claude Code subscriptions (Team plan)
- 5 Premium seats at $100/seat/mo = $500/mo
- Each seat gets its own independent rate limit pool (6.25x Pro each)
- Total capacity: ~5x what a single Max 20x gives you
- Legitimate, supported by Anthropic

### Option 2: Claude Code Max 20x + Cursor Ultra
- $200 + $200 = $400/mo
- Use Claude Code for complex/Opus work, Cursor headless for volume Sonnet work
- Different rate limit pools, different providers
- Both have real headless CLI modes for pipeline use

### Option 3: Claude Code Max 20x + Chinese flat-rate provider
- $200 + $50 (Qwen Cloud Pro) = $250/mo
- Route routine pipeline stages (linting, simple edits, test generation) to Qwen
- Reserve Claude for architecture, complex reasoning, critical code
- Qwen gives 6,000 req/5h vs Claude's ~200-900

### Option 4: Claude Code Team + API overflow
- Team Premium seats for interactive work
- Anthropic API (pay-per-token) for batch pipeline stages via Claude Agent SDK
- Usage credits on the Team plan buy API-rate overflow when you hit subscription caps

### Option 5: Multiple accounts (gray area)
- Anthropic ToS does not explicitly prohibit multiple personal accounts
- Each $200 Max plan has its own independent pool
- Risk: account linking/banning if detected
- Safer: use different email addresses for different "projects"

---

## Concurrency and Rate Limit Summary

| Service | Max Concurrent Sessions | Shared Pool? |
|---------|------------------------|--------------|
| Claude Code Max 20x | No hard limit (practical: 2-3) | Yes, all share one 5h window |
| Cursor Ultra | Not documented | Yes, headless + IDE share credits |
| Codex Pro | Multiple cloud tasks | Yes, rolling 5h window |
| Antigravity Ultra | Not documented | Yes, 5h compute budget |
| Augment Max | Not documented | Yes, credit pool |

---

## Recommendation for Pipeline Use

For a pipeline that needs to run coding agents autonomously:

1. **Primary:** Claude Code Max 20x ($200/mo) — `claude -p --permission-mode auto` for headless pipeline stages. Best quality. Route Opus for hard problems, Sonnet for routine.

2. **Overflow:** Cursor Ultra headless ($200/mo) — `cursor --headless` when Claude is rate-limited. Different pool, similar quality on Sonnet/Opus.

3. **Volume:** Qwen Cloud Pro ($50/mo) — 6,000 req/5h for routine stages (formatting, simple edits, test scaffolding). Anthropic-compatible API means minimal code changes.

4. **Scale:** Claude Team Premium seats ($500/mo for 5 seats) — when you need 5 independent rate limit pools for parallel pipeline workers.

None of these are truly unlimited. The closest to "unlimited" for the money is Qwen Cloud Pro at $50/mo with 6,000 requests per 5-hour window — but the model quality gap vs Opus is significant for complex work.
