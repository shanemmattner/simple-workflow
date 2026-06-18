# Coding Agent Subscription Comparison — Pipeline Runtime Research
> Updated: 2026-06-17. Focus: **can it be called from a Python script for automated multi-turn coding work?**

---

## Quick Comparison Table

| Tool | Provider | Price/mo | CLI? | Headless/Script? | Agent (file edit + test)? | Model Quality | Pipeline Viable? |
|---|---|---|---|---|---|---|---|
| **OpenAI Codex CLI** | OpenAI | $20 (Plus) / $200 (Pro) API; or pay-per-token | Yes (npm, Rust binary) | Yes — `codex exec` scripting mode | Yes — full sandbox, subagents, test runner | GPT-5.5, #1 on agent benchmarks June 2026 | **YES — top pick** |
| **Antigravity CLI** | Google | Free (AI Pro/Ultra $20/$100-200) | Yes (Go binary `agy`) | Yes — headless mode, SDK | Yes — multi-agent, scheduled tasks | Gemini 3.5 Flash / Gemini 3 | **YES — strong free tier** |
| **OpenCode** | sst.dev | Free (MIT OSS) + API keys | Yes (npm/bun TUI) | Yes — non-interactive mode, pipe stdin | Yes — 75+ LLM providers | BYOM (Claude, GPT, Gemini, DeepSeek, local) | **YES — most flexible** |
| **Aider** | OSS (Apache 2.0) | Free + API keys | Yes (Python CLI) | Yes — designed for scripting | Yes — git-aware, multi-file | BYOM; ~$5-15/mo typical with Claude/GPT | **YES — mature scripting** |
| **GitHub Copilot CLI** | GitHub/Microsoft | $10 (Pro) / $39 (Pro+) / $100 (Max) | Yes (npm `@github/copilot`) | Partial — SDK exists, scripting limited | Yes — cloud agent, fleet/parallel subagents | Multi-model (GPT-5.5, Claude Opus 4.8, Gemini 3.1) | **PARTIAL — SDK available** |
| **Amazon Q Developer** | AWS | Free / $19/mo (Pro) | Yes (CLI included) | Yes — CLI designed for scripting/CI | Yes — 50 agentic req/mo free, 1000/mo Pro | Claude Sonnet 4.x (via Bedrock) | **YES — good AWS integration** |
| **Kiro** | AWS | Free (50 cr/mo) / $20 (Pro, 1000 cr) / $40 (Pro+) / $200 (Power) | Yes (Kiro CLI) | Yes — CLI + CI/CD integration | Yes — spec-driven, parallel task execution | Claude Sonnet 4.6 / Opus 4.8, Qwen3, DeepSeek | **YES — credit-based** |
| **Cursor CLI** | Anysphere | $20 (Pro) / $60 (Pro+) / $200 (Ultra) | Yes (Jan 2026) | Limited — "Cloud Handoff" for async | Yes — Composer 2.5, Background Agents | Multi-model (Claude, GPT, Gemini) | **PARTIAL — IDE-native primarily** |
| **GLM Coding Plan** | Zhipu AI (Z.ai) | ~$10 (Lite) / ~$30-72 (Pro) / ~$80-160 (Max) — quarterly billing | Via API key (OpenAI-compatible) | Yes — OpenAI-compatible API, works with any client | Yes — via Cline/Aider/Claude Code with GLM model | GLM-5.2: 1M context, 73.8%+ SWE-bench | **YES — flat-rate API** |
| **Augment Code** | Augment | $20 (Indie) / $60/user (Standard) / $200/user (Max) | Yes (Auggie CLI) | Yes — CLI agent + MCP server | Yes — Context Engine, Cosmos cloud sandboxes | Claude Sonnet 4.6 / Opus 4.7, GPT-5.x, Gemini 3.1 | **YES — enterprise grade** |
| **Devin Desktop** | Cognition (was Windsurf) | $20 (Core, ACUs extra) / $500/mo team | Limited — Devin Terminal CLI | Partial — Devin API available | Yes — fully autonomous cloud agent | Proprietary Genie model | **PARTIAL — API but expensive** |
| **Google Gemini Code Assist** | Google Cloud | Free (6K completions/day) / $19/seat (Standard) | Yes (Gemini CLI, → Antigravity CLI) | Yes — being replaced by Antigravity June 18 | Yes — agent mode (preview) | Gemini 3 / Gemini 3.1 Pro | **PARTIAL — sunset in progress** |
| **Amazon Q Developer CLI** | AWS | Free (50 agentic req) / $19/mo Pro | Yes (standalone CLI) | Yes — designed for terminal/CI use | Yes — file edits, terminal, tests | Claude Sonnet 4.x | **YES — same as Q Developer row** |
| **MiniMax M3** | MiniMax | Token Plan via API ($0.30/M) | Via API key | Yes — OpenAI-compatible API | Yes — through BYOK tools (Cline, Aider) | SWE-Bench Pro 59%, 1M context | **YES — cheapest API tier** |
| **DeepSeek** | DeepSeek AI | API only — V4 Flash $0.07/M, V4 Pro $0.55/M | Via API key | Yes — OpenAI-compatible API | Yes — through BYOK tools | Strong coding; no subscription plan | **YES — cheapest BYOK** |
| **Cline** | OSS (Apache 2.0) | Free + API keys | VS Code extension + CLI | Yes — via API | Yes — VS Code native, terminal, browser | BYOM (any provider) | **YES — 5M installs, mature** |
| **Qwen Code** | Alibaba | Free (Qwen3-Coder models) | Yes — CLI available | Yes — non-interactive mode | Yes — optimized for Qwen3-Coder | Qwen3-Coder frontier | **YES — free with Alibaba API** |
| **Goose** | Block (Square) | Free (OSS, MIT) + API keys | Yes (desktop + CLI) | Yes — API mode, MCP extensible | Yes — model-agnostic, Ollama support | BYOM including local | **YES — fully open, local** |
| **OpenHands** | All Hands AI | Free (self-host) / cloud available | Yes (Docker + CLI) | Yes — Docker, GitHub/GitLab integration | Yes — autonomous PR creation, 65k stars | BYOM | **YES — best for autonomous PRs** |

---

## Pipeline Viability Deep Dive

### Tier 1: Best Pipeline Runtime Candidates

#### 1. OpenAI Codex CLI — `npm i -g @openai/codex` or `curl install.sh`
- **Scripting**: `codex exec "<task>"` runs non-interactively. Approval modes: `suggest` (manual), `auto-edit` (no shell), `full-auto` (unattended). `CODEX_NON_INTERACTIVE=1` env var for CI.
- **Subscription**: Plus ($20/mo) = 10-60 cloud tasks / 5h window, 45-225 local tasks / 5h. Pro ($200/mo) = 50-400 cloud / 5h, 300-1500 local. Business ($30/user) same as Plus limits.
- **API**: `gpt-5.2-codex` model — $1.75/M input, $14/M output + container fee ($0.03-$1.92/task). OpenAI-compatible API.
- **Agent capability**: Full sandbox, file edit, test runner, subagents, web search, MCP. Tops several agent benchmarks (Terminal-Bench 2.0, SWE-bench) in mid-2026.
- **Limitation**: Container sandbox adds latency; rolling 5-hour rate windows not 24h daily limits. Cloud-only.
- **Pipeline call**: `codex exec "fix all failing tests in /path/to/repo" --approval-mode full-auto`

#### 2. Antigravity CLI — Google's replacement for Gemini CLI (June 18, 2026)
- **Install**: Go binary `agy`, or `npm i -g @google/antigravity`
- **Scripting**: Headless mode supported. SDK available for programmatic agent invocation.
- **Subscription**: Free with Google AI Pro ($20/mo) and Ultra ($100-$200/mo). Shares quota with Antigravity desktop app.
- **Free tier**: Generous — AI Pro users get full CLI access included. Google Gemini CLI had 1K req/day free (now migrating to Antigravity limits).
- **Agent capability**: Multi-agent teams, scheduled tasks, Gemini 3.5 Flash (default), 1M context, MCP, file edit, shell execution.
- **Pipeline call**: `agy run "<task>" --headless` (exact flags TBD per migration docs)
- **Limitation**: Migration from Gemini CLI happening June 18; quotas shared with desktop app; new product so ecosystem thinner than Claude Code.

#### 3. OpenCode — `npm i -g opencode` (sst.dev, MIT)
- **Scripting**: `opencode "<prompt>"` non-interactive mode; pipe stdin. Fully scriptable.
- **Cost**: $0 for the tool itself. Bring your own API keys or use Claude Pro/Max subscription ($20-200/mo unlocks via `opencode auth login`).
- **Agent capability**: 75+ LLM providers, LSP integration, dual build/plan agents, MCP, AGENTS.md project memory, SDK for ACP integration.
- **Key feature**: With Claude Pro subscription, this is effectively a free Claude Code replacement — same auth, same models, MIT licensed.
- **Limitation**: 150K GitHub stars, very active, but younger than Aider. Some rough edges on non-standard model providers.
- **Pipeline call**: `opencode "read all test files and fix failures" --model claude-sonnet-4-6`

#### 4. Aider — `pip install aider-chat`
- **Scripting**: Fully designed for scripting. `aider --message "task" file1.py file2.py` or stdin. `--yes` flag skips all prompts.
- **Cost**: $0 tool + API keys. Typical: $5-15/mo with Claude Sonnet or GPT-5.4-mini. DeepSeek V4 Pro drops this to under $2/mo for most workflows.
- **Agent capability**: Git-aware (auto-commits), multi-file edits, architect mode (two-model), repo-map for context, voice coding, automatic git commits.
- **Model flexibility**: Works with any OpenAI-compatible endpoint — including GLM Coding Plan, DeepSeek, local Ollama, OpenRouter.
- **Limitation**: Terminal-only (no IDE). Steeper learning curve for complex multi-step tasks vs Claude Code. No subagents.
- **Pipeline call**: `aider --model deepseek/deepseek-v4-pro --message "fix all linting errors" --yes --no-git src/`

#### 5. GLM Coding Plan — Zhipu AI (Z.ai) flat-rate API
- **What it is**: Flat-rate subscription that gives you a `sk-sp-` API key with 5-hour rolling prompt quotas. OpenAI-compatible API endpoint at `https://api.z.ai/api/coding/paas/v4`.
- **Tiers**: Lite ~$10/mo (80 prompts/5h), Pro ~$30-72/mo (400 prompts/5h), Max ~$80-160/mo (1600 prompts/5h) — all billed quarterly.
- **Models**: GLM-5.2 (1M context, just released June 2026), GLM-5.1, GLM-5 Turbo, GLM-4.7. SWE-bench scores 73.8%+ (GLM-4.7).
- **Pipeline use**: Drop-in for any OpenAI-compatible client (Aider, Cline, Claude Code custom provider, OpenCode). No container fees, no per-token surprise bills.
- **Limitation**: 5-hour rolling quota (not daily) — heavy pipeline bursts could hit limits. Models lag Claude Opus quality on complex reasoning but competitive on coding specifically. China-based API (latency from US may vary).
- **Best for**: High-volume pipeline that runs many small-medium tasks where per-token billing would be expensive.

#### 6. Amazon Q Developer / Kiro
- **Q Developer**: Free tier (50 agentic req/mo), Pro ($19/mo, 1000 req/mo). CLI is standalone, CI-friendly. Best if already AWS-embedded.
- **Kiro**: Credit-based ($20/mo = 1000 credits, $200/mo = 10000 credits, $0.04/credit overage). Has Kiro CLI + CI/CD integration. Spec-driven (EARS notation) — generates requirements → architecture → tests before code.
- **Scripting**: Q Developer CLI was designed for terminal/CI use from day one. Kiro subscriptions explicitly listed as usable in "automation in software development (ex: reviews during CI/CD)".
- **Models**: Both use Bedrock — Claude Sonnet 4.6 / Opus 4.8, Qwen3 Coder, DeepSeek V3.2, MiniMax 2.1.
- **Best for**: Teams already on AWS. Kiro's spec-driven workflow is compelling for structured pipeline stages.

#### 7. Augment Code — `auggie` CLI
- **Subscription**: Indie $20/mo (40K credits), Standard $60/user (130K credits), Max $200/user (450K credits). Credits pooled team-wide.
- **CLI**: "Auggie CLI" is the command-line agent. Also exposes an MCP server. Cosmos = cloud sandbox VMs for agent runs.
- **Model quality**: SWE-bench Verified 70.6% (self-reported, highest code review accuracy on public benchmarks). Context Engine indexes full repo (200K token).
- **Scripting**: CLI agent can be called programmatically. MCP server integration means any MCP-compatible host can drive it.
- **Limitation**: Pricing complexity — model choice affects credit burn rate (Opus = 1.7x more credits than Sonnet). Company has changed pricing 4 times in 18 months.
- **Best for**: Enterprise teams needing high-accuracy code review + agent work with compliance (SOC 2, CMEK, ISO 42001).

---

### Tier 2: Viable But with Caveats

#### GitHub Copilot CLI — `npm i -g @github/copilot`
- **Subscription**: Pro $10/mo (AI credits allowance), Pro+ $39/mo, Max $100/mo (20K credits), Business $19/seat, Enterprise $39/seat.
- **Scripting**: SDK (`@github/copilot`) exists for building on the agentic runtime. CLI supports `/plan`, `/fleet` (parallel subagents), MCP integration.
- **Agent capability**: Cloud agent + Copilot CLI in terminal, GitHub-native MCP (issues/PRs/branches), `fleet` for parallel subagents, works across all OSes.
- **Limitation**: Usage-based flex billing went live June 1, 2026 — many users reported burning through allocations faster than expected. Max models (GPT-5.5, Claude Opus 4.8) consume 5-20x credits per interaction. New $100/mo Max plan adds ~$200 of effective usage. Not primarily designed for headless scripting — SDK is newer.
- **Best for**: Teams already on GitHub Enterprise who want native issue/PR integration.

#### Cursor CLI — bundled with Cursor subscription
- **Pricing**: Free / Pro $20 / Pro+ $60 / Ultra $200. Teams $40/seat.
- **CLI**: Launched January 2026. `cursor --plan "<task>"` and `cursor --run "<task>" &` (Cloud Handoff with `&` prefix sends to async cloud agent).
- **Limitation**: CLI is designed to pair with the IDE. Background Agents are sequential, not parallel. No JetBrains support. Primarily IDE-centric — headless pipeline use is second-class.
- **Best for**: IDE users who occasionally want async agent tasks, not pipeline-first workflows.

#### Devin Desktop (was Windsurf) + Devin Cloud Agent
- **Pricing**: Core $20/mo (ACU-based, $2.25/ACU), Team $500/mo (250 credits). Devin API available.
- **CLI**: Devin Terminal CLI included. Devin API allows programmatic task submission.
- **Limitation**: Very expensive for pipeline use ($2.25/ACU = roughly $10-50+ per substantial task). Devin is the most autonomous but priced for high-value, low-frequency tasks. Cascade (Windsurf's agent) EOL July 1, 2026.
- **Best for**: High-value infrequent tasks (e.g., "build this feature from scratch") not continuous pipeline work.

---

### Tier 3: BYOK Tools (Free Tool, Pay API)

| Tool | Install | Key Strength | Best Backend |
|---|---|---|---|
| **Aider** | `pip install aider-chat` | Git-native, mature scripting, `--yes` flag | DeepSeek V4 Pro ($0.55/M) or GLM Coding Plan |
| **Cline** | VS Code extension + CLI | 5M installs, Plan/Act modes, cost tracking per task | Claude Sonnet 4.6 or DeepSeek V4 Pro |
| **OpenCode** | `npm i -g opencode` | 75+ providers, MIT, 150K stars, pipe-friendly | Claude Pro sub or DeepSeek V4 Pro |
| **Goose** | Desktop + CLI (Block) | Local models (Ollama), fully offline capable | Local Qwen3 or Claude API |
| **OpenHands** | Docker | Autonomous PR creation, GitLab/GitHub integration | Any OpenAI-compatible |

**Effective cost math for BYOK with DeepSeek V4 Pro:**
- A medium coding task (~200K input, 10K output): $0.11 + $0.06 = ~$0.17
- 100 tasks/month: ~$17/mo total — cheaper than any subscription for pipeline use.

---

## Specific Questions Answered

### GLM 5.2 (Zhipu AI)
GLM 5.2 shipped June 2026 on all GLM Coding Plan tiers with a **1M token context window**. Open weights releasing next week (at time of research). No benchmarks published yet. Prior GLM-5.1 scores 94.6% of Claude Opus 4.6 coding performance. The GLM Coding Plan flat-rate model is the main subscription product — OpenAI-compatible API, drop-in for Aider/Cline/OpenCode.

### MiniMax M3
MiniMax M3 released in 2026: SWE-Bench Pro **59%**, Terminal-Bench 2.1 **66%**, 1M context, multimodal, open weights. Available via **MiniMax Token Plan** (API pricing ~$0.30/M tokens) and the MiniMax Code desktop app. No standalone coding agent subscription — works best as a BYOK backend for Aider/Cline/OpenCode. Very cheap API.

### OpenAI Codex / ChatGPT Plus
Codex in 2026 is a cloud multi-agent on `gpt-5.2-codex`. ChatGPT Plus ($20/mo) includes Codex access (10-60 cloud tasks/5h, 45-225 local tasks/5h). Codex CLI is open source (Apache 2.0), installable standalone. **`codex exec`** command enables fully scripted/unattended runs. Most pipeline-friendly of the subscription-bundled agents.

### DeepSeek
**API only — no subscription plan.** V4 Flash: $0.07/M input / $0.28/M output. V4 Pro: $0.55/M / $2.19/M. Free off-peak window exists. Use via Aider, Cline, or OpenCode. No native coding agent product — the models are strong (top open-weights coding), accessed via BYOK tools. Not viable as a standalone agent.

### Cursor
Primarily an IDE, not designed for headless pipeline use. Cursor CLI (Jan 2026) exists but is secondary. Background Agents are sequential. Pro is $20/mo. Skip for pipeline work — use OpenCode or Aider instead, which can use Claude/GPT via the same subscription.

### Windsurf (now Devin Desktop)
Windsurf rebranded to **Devin Desktop on June 2, 2026**. Cascade agent EOL July 1, 2026. Devin Terminal CLI bundled. Devin API exists for programmatic use but at ACU pricing ($2.25/ACU) it's too expensive for pipeline automation. Skip unless you need Devin's autonomous cloud engineer for high-value infrequent tasks.

### Aider
Free OSS tool. Effective cost depends on model choice:
- With DeepSeek V4 Pro: ~$2-10/mo for typical pipeline use
- With Claude Sonnet 4.6: ~$10-30/mo
- With GLM Coding Plan: flat $10-30/mo regardless of volume
Fully scriptable: `aider --yes --message "task" --model <model> files...`. Best for git-centric pipelines with auto-commit semantics.

### Augment Code
$100/mo flat for Business plan (up to 50 seats, $100 usage pool included). Uses pooled credit model — LLM at provider list price + 40% service fee + compute. CLI agent ("Auggie") is available. MCP server. Best for teams; Indie tier ($20/mo) works for solo pipeline use. High code review accuracy is the standout differentiator.

### Amazon Q Developer
Free tier: 50 agentic requests/month + 4K lines of code, VS Code/JetBrains/CLI. Pro: $19/mo, 1000 agentic req/mo. CLI designed for CI/CD from day one. If on AWS stack, this is the path-of-least-resistance pipeline runtime.

### Google Gemini Code Assist
Being replaced by **Antigravity** for free/individual users on June 18, 2026. Enterprise Standard ($19/seat) continues with Gemini 3. Gemini CLI (open source, free) also transitioning to Antigravity CLI. Enterprise Code Assist Standard still viable for teams via Google Cloud.

### Cline / Roo Code
Roo Code **shut down May 15, 2026** — brand redirects to Roomote. Cline (the VS Code extension) continues independently with 5M installs. BYOK model, supports all major providers. VS Code only (not CLI-first). Use OpenCode or Aider for pure CLI pipeline use.

### Copilot Workspace (GitHub)
Now part of **GitHub Copilot cloud agent** (not a separate product). Included in all Copilot Pro+ and above plans. Works from CLI via Copilot CLI (`npm i -g @github/copilot`). Can go from issue to PR via CLI. SDK available for building custom agents on the same runtime.

---

## New Agents in 2026 Not to Miss

| Tool | What it is | Pipeline Viable? |
|---|---|---|
| **Qwen Code** | Alibaba CLI agent optimized for Qwen3-Coder, free via Alibaba API | YES — free tier, strong coding |
| **OpenHands** | Autonomous PR agent, Docker-based, 65K GitHub stars | YES — best for async PR work |
| **Verdent** | Parallel multi-agent IDE (not CLI-first), credit-based ($19-179/mo) | Partial — IDE focused |
| **Kimi Code CLI** | Moonshot AI CLI agent, K2.5 model (256K context), $15/mo subscription | YES — MCP, IDE integration |
| **Factory Droid** | Autonomous multi-day tasks via CLI/web/IDE, usage-based | YES — designed for async pipeline |
| **Sourcegraph Amp** | "Deep mode" code search + agent, enterprise | Partial — enterprise sales |
| **Antigravity SDK** | Google's SDK for building custom agents (same harness as Antigravity 2.0) | YES — build custom pipeline agent |

---

## Recommendation for Your Pipeline

**Primary runtime:** OpenCode (MIT, free) + Claude Pro subscription ($20/mo) or DeepSeek V4 Pro API (~$5-15/mo) as backend. Most flexible, scriptable, supports all providers, non-interactive mode confirmed.

**Flat-rate alternative:** GLM Coding Plan Pro (~$30-72/mo) — drop-in OpenAI-compatible API, no per-token billing surprises, 400 prompts/5h rolling window covers heavy pipeline use. Use with Aider for clean git-native scripting.

**If benchmarks matter most:** OpenAI Codex CLI (Plus $20/mo) — `codex exec --approval-mode full-auto` for unattended runs. Top agent benchmark scores (Terminal-Bench 2.0), sandboxed by default, cloud handoff to Codex Cloud for long async tasks.

**If Google ecosystem:** Antigravity CLI (free with AI Pro $20/mo) — same harness as Antigravity 2.0 multi-agent, headless mode, Gemini 3.5 Flash is fast.

**Avoid for pipeline use:** Cursor (IDE-first), Devin/Windsurf (ACU pricing too expensive), Copilot Workspace (credits burn fast at Max model rates).
