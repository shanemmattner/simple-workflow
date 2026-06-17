# Claude Code Ecosystem: Pre-Made Skills, Agents, Workflows, and Automation

Research date: 2026-06-16

---

## Tier 1: Mega-Repos (50K+ Stars)

### affaan-m/everything-claude-code (ECC)
- **URL**: https://github.com/affaan-m/everything-claude-code
- **Stars**: ~170K+ (fastest-growing Claude Code repo ever)
- **What it provides**: 48 agents, 184 skills, 60+ slash commands, 34 rules, 20+ automated hooks, 14 MCP servers, security scanning
- **Key features**: Memory/learning system that persists across sessions, cross-platform (Claude Code, Cursor, Codex, OpenCode), 12 language ecosystems
- **Origin**: Anthropic x Forum Ventures hackathon winner (Sep 2025), open-sourced Jan 2026
- **Stealable patterns**:
  - "Instincts" system -- lightweight reactive behaviors that fire on context triggers
  - Memory optimization layer that carries learnings between sessions
  - Security scanning hooks that run automatically before commits
- **Issue-to-PR relevance**: Has research-first development workflow, but more focused on general coding than issue pipelines

### mattpocock/skills
- **URL**: https://github.com/mattpocock/skills
- **Stars**: ~77K (was #1 on GitHub Trending)
- **What it provides**: Curated skills from Matt Pocock's personal .claude directory
- **Key skills**:
  - `grill-me` -- relentless interviewing about a plan until every decision branch is resolved
  - `caveman` -- ultra-compressed communication mode cutting token usage ~75%
  - `handoff` -- compact conversation into a handoff document for another agent
- **Stealable patterns**:
  - The "handoff" pattern for session continuity between agents
  - Caveman mode for token-efficient inner-loop agents
  - Grill-me as a pre-implementation validation gate

---

## Tier 2: Major Community Collections (1K-50K Stars)

### hesreallyhim/awesome-claude-code
- **URL**: https://github.com/hesreallyhim/awesome-claude-code
- **Stars**: ~44K+
- **What it provides**: Curated directory of skills, hooks, slash-commands, agent orchestrators, applications, and plugins
- **Key value**: The canonical "awesome list" for the Claude Code ecosystem -- best place to find new tools by category
- **Issue-to-PR relevance**: Reference directory, not a tool itself, but indexes all the pipeline repos

### VoltAgent/awesome-agent-skills
- **URL**: https://github.com/VoltAgent/awesome-agent-skills
- **Stars**: ~20K
- **What it provides**: 1000+ curated agent skills from official dev teams and community
- **Key differentiator**: Includes official skills from Anthropic, Google Labs, Vercel, Stripe, Cloudflare, Netlify, Trail of Bits, Sentry, Expo, Hugging Face, Figma
- **Compatibility**: Claude Code, Codex, Antigravity, Gemini CLI, Cursor, GitHub Copilot, OpenCode, Windsurf
- **Stealable patterns**: Cross-agent skill format that works everywhere

### VoltAgent/awesome-claude-code-subagents
- **URL**: https://github.com/VoltAgent/awesome-claude-code-subagents
- **Stars**: Unknown (significant given parent org)
- **What it provides**: 100+ specialized Claude Code subagents covering wide range of dev use cases

### alirezarezvani/claude-skills
- **URL**: https://github.com/alirezarezvani/claude-skills
- **Stars**: Unknown (frequently referenced)
- **What it provides**: 337 skills, 30+ agents, 70+ custom commands, 330+ skills, customizable references, scripts
- **Domains**: Engineering, marketing, product, compliance, C-level advisory, research, business operations, finance, daily productivity
- **Compatibility**: Claude Code, Codex, Gemini CLI, Cursor, and 8 more coding agents
- **Stealable patterns**: Domain-specific skill organization by business function

### alirezarezvani/claude-code-github-workflow
- **URL**: https://github.com/alirezarezvani/claude-code-github-workflow
- **Stars**: Unknown
- **What it provides**: Blueprint for using Claude Code + GitHub as workflow automation suite
- **Key features**:
  - Issues labeled "claude-code" + "status:ready" trigger automatic branch creation
  - Developer commits -> quality checks -> PR creation (fully automated)
  - Custom commands: blueprint init, planning, commit validation, PR creation, review, release management, status sync
- **Issue-to-PR relevance**: HIGHLY RELEVANT -- this is essentially an issue-to-PR pipeline using GitHub Actions + Claude Code
- **Stealable patterns**:
  - Label-based issue triggering ("claude-code" + "status:ready")
  - Separation of planning phase from implementation phase
  - Status sync commands that keep issues updated with PR progress

---

## Tier 3: Pipeline and Orchestration Repos (Specialized)

### aaddrick/claude-pipeline
- **URL**: https://github.com/aaddrick/claude-pipeline
- **Stars**: Unknown (actively referenced in ecosystem)
- **What it provides**: Portable multi-agent pipeline with skills, agents, hooks, orchestration scripts, quality gates
- **Key features**:
  - `handle-issues` skill: batch processes GitHub issues through setup -> plan -> implement -> test -> review -> PR
  - Rate limiting, status tracking, circuit breakers built in
  - Adaptation skill: brainstorming session to customize pipeline for your codebase
  - 19 skills for process discipline, 10 specialized agents (backend dev, frontend dev, code reviewer, test validator)
- **Issue-to-PR relevance**: DIRECTLY RELEVANT -- this IS an issue-to-PR pipeline
- **Stealable patterns**:
  - Circuit breaker pattern for batch issue processing
  - Adaptation skill that customizes the pipeline per-project
  - Orchestration: setup -> plan -> implement -> test -> review -> PR (with re-run loops)
  - Rate limiting awareness in batch processing
  - Status tracking across multiple concurrent issues

### dsifry/metaswarm
- **URL**: https://github.com/dsifry/metaswarm
- **Stars**: ~149
- **What it provides**: Self-improving multi-agent orchestration framework, 18 agents, 13 skills, 15 commands
- **Key features**:
  - TDD enforcement as blocking gate (via .coverage-thresholds.json)
  - 11-phase lifecycle: issue -> merged PR with specialist agents at each phase
  - Cross-model adversarial review (multiple LLMs review each other's work)
  - Self-reflect workflow: analyzes code review feedback to extract patterns/anti-patterns
  - Git-native knowledge base (specs, issues, knowledge all in version control)
- **Supported**: Claude Code, Gemini CLI, Codex CLI
- **Issue-to-PR relevance**: EXTREMELY RELEVANT -- full issue-to-PR with TDD gates
- **Stealable patterns**:
  - 11-phase specialist agent lifecycle
  - Cross-model adversarial review (use cheap model to critique expensive model's output)
  - Self-improvement loop: learns from review feedback
  - Coverage thresholds as hard gates before PR creation
  - Spec-driven development (specs committed to repo, agents read them)

### nwiizo/ccswarm
- **URL**: https://github.com/nwiizo/ccswarm
- **Stars**: Unknown
- **What it provides**: Multi-agent orchestration with Git worktree isolation
- **Key features**:
  - Git worktree isolation: parallel agents work without merge conflicts
  - Specialized pools: Frontend, Backend, DevOps, QA
  - Auto-accept mode with risk assessment
  - LLM Quality Judge with multi-dimensional scoring
  - NDJSON audit trails (replayable, diffable, rollback-able)
  - Pipeline: plan -> Sangha consensus -> implement -> review -> fix -> commit -> PR
  - `queue add --from-issue` to ingest GitHub issues
- **Written in**: Rust (installable via `cargo install ccswarm`)
- **Issue-to-PR relevance**: DIRECTLY RELEVANT
- **Stealable patterns**:
  - Git worktree isolation for parallel agent work (no conflicts!)
  - NDJSON audit trails for observability
  - "Sangha consensus" -- multiple agents must agree on plan before implementation
  - Risk assessment before auto-accepting changes
  - Queue-based issue ingestion

### catlog22/Claude-Code-Workflow
- **URL**: https://github.com/catlog22/Claude-Code-Workflow
- **Stars**: Unknown
- **What it provides**: JSON-driven multi-agent cadence-team framework
- **Key features**:
  - workflow.json configuration with project settings, cadence modes, team members, CLI specs
  - Team Architecture v2: role-spec based execution, inner loop framework, message bus protocol
  - Wisdom accumulation (learnings/decisions/conventions persist)
  - Workflow types: lite-plan, multi-cli-plan, TDD-plan, test-fix, brainstorm
  - Installable via npm: `npm install -g claude-code-workflow`
- **Stealable patterns**:
  - JSON-driven workflow definition (declarative pipeline config)
  - "Cadence mode" concept (strict-sync vs async team coordination)
  - Wisdom accumulation -- learnings/decisions/conventions stored and reused
  - Multi-CLI orchestration (Gemini/Qwen/Codex as different team members)

---

## Tier 4: Learning and Reference Repos

### rohitg00/pro-workflow
- **URL**: https://github.com/rohitg00/pro-workflow
- **Stars**: Unknown (well-promoted on X/Twitter)
- **What it provides**: Self-correcting memory that compounds over 50+ sessions
- **Key features**:
  - SQLite store for corrections: every correction becomes an FTS5-searchable rule
  - Auto-loaded on session start -- Claude never makes the same mistake twice
  - 34 skills, 8 agents, 22 commands, 37 hook scripts across 24 events
  - `/handoff` command for structured session handoff documents
  - Published on Claude Code plugin marketplace AND SkillKit
- **Stealable patterns**:
  - SQLite-backed correction memory (FTS5 searchable)
  - Auto-loaded rules on session start
  - Session handoff protocol with structured briefing (status, done, pending, decisions, gotchas, resume command)
  - 37 hook scripts across 24 events -- comprehensive event coverage

### rohitg00/awesome-claude-code-toolkit
- **URL**: https://github.com/rohitg00/awesome-claude-code-toolkit
- **Stars**: Unknown
- **What it provides**: 135 agents, 35 curated skills, 42 commands, 176+ plugins, 20 hooks, 15 rules, 7 templates, 14 MCP configs, 26 companion apps, 52 ecosystem entries

### ChrisWiles/claude-code-showcase
- **URL**: https://github.com/ChrisWiles/claude-code-showcase
- **Stars**: Unknown (reference/example repo)
- **What it provides**: Comprehensive configuration example with hooks, skills, agents, commands, GitHub Actions workflows
- **Key features**:
  - `github-workflow.md` agent for creating commits, managing branches, creating PRs
  - `code-reviewer.md` agent with structured feedback (critical/warning/suggestion)
  - `pr-review.md` command
  - Scheduled automation: monthly docs sync, weekly code quality reviews, biweekly dependency audits
- **Stealable patterns**:
  - Clean .claude/ directory structure as reference implementation
  - Scheduled GitHub Actions running Claude Code agents on cadence
  - Structured review output format (critical/warning/suggestion categories)

### lyndonkl/claude
- **URL**: https://github.com/lyndonkl/claude
- **Stars**: Unknown
- **What it provides**: 241 skills + 62 orchestrating agents
- **Domains**: Thinking frameworks, research, writing, design, data/ML, corporate finance, game theory, household finance, Substack growth, ML crop genetics
- **Stealable patterns**: Agent-routes-to-skills architecture (agents detect need and route to right skills)

### shinpr/claude-code-workflows
- **URL**: https://github.com/shinpr/claude-code-workflows
- **Stars**: Unknown
- **What it provides**: Production-ready dev workflows with specialized agents for requirements, design, implementation, quality checks
- **Related repos from same author**:
  - `shinpr/ai-coding-project-boilerplate` -- TypeScript boilerplate with sub-agent workflows
  - `shinpr/claude-code-discover` -- Product discovery workflows (hypotheses -> validated PRDs)
  - `shinpr/agentic-code` -- AGENTS.md-powered framework with quality gates

### glebis/claude-skills
- **URL**: https://github.com/glebis/claude-skills
- **Stars**: ~191
- **What it provides**: Balanced analysis skill (anti-sycophancy), GitHub Gist skill, deep research skill
- **Stealable patterns**: Balanced Analysis mode that replaces sycophantic responses with structured critical analysis

---

## Tier 5: Official Anthropic Resources

### anthropics/skills
- **URL**: https://github.com/anthropics/skills
- **Stars**: Unknown
- **What it provides**: Official Anthropic skills demonstrating the skills system, including skills that power Claude's native document creation

### anthropics/claude-code-action
- **URL**: https://github.com/anthropics/claude-code-action
- **Stars**: ~6.4K
- **What it provides**: Official GitHub Action for Claude Code on PRs and issues
- **Key features**:
  - Intelligent mode detection (PR review vs issue implementation vs direct mention)
  - @claude mentions trigger responses
  - Issue assignment triggers implementation
  - Multi-auth: Anthropic API, Bedrock, Vertex AI, Microsoft Foundry
  - Issue triage, duplicate detection, lifecycle management, cross-repo notifications
- **Issue-to-PR relevance**: OFFICIAL SOLUTION for basic issue-to-PR automation
- **Stealable patterns**:
  - Mode detection based on trigger context
  - Label-based routing
  - Path-specific review configurations

---

## Summary: Most Relevant for Issue-to-PR Pipeline

Ranked by direct relevance to building an issue-to-PR pipeline:

| Repo | Relevance | Key Pattern |
|------|-----------|-------------|
| aaddrick/claude-pipeline | Highest | Batch issue processing with circuit breakers, full orchestration pipeline |
| dsifry/metaswarm | Highest | 11-phase specialist lifecycle, TDD gates, self-improving |
| alirezarezvani/claude-code-github-workflow | High | Label-triggered automation, status sync, GitHub Actions native |
| nwiizo/ccswarm | High | Git worktree isolation, queue-based issue ingestion, consensus |
| anthropics/claude-code-action | High | Official, simple, well-supported (but less customizable) |
| catlog22/Claude-Code-Workflow | Medium | JSON-driven workflow definition, multi-CLI orchestration |
| rohitg00/pro-workflow | Medium | Self-correcting memory, session handoff protocol |

---

## Key Architectural Patterns Worth Stealing

### 1. Circuit Breakers for Batch Processing (claude-pipeline)
When processing multiple issues, have circuit breakers that halt the batch if too many failures occur. Prevents cascading failures from consuming your API budget.

### 2. Git Worktree Isolation (ccswarm)
Each agent works in its own git worktree. No merge conflicts between parallel agents. Clean isolation. The "pipeline" command handles one-shot tasks, "queue" handles batch ingestion.

### 3. Cross-Model Adversarial Review (metaswarm)
Use a different model (or the same model with different prompting) to review the implementation agent's output. Catches blind spots that self-review misses.

### 4. Self-Correcting Memory (pro-workflow)
SQLite + FTS5 stores every correction. Auto-loaded on session start. The agent literally never makes the same mistake twice. Compounds over 50+ sessions.

### 5. Label-Based Triggering (claude-code-github-workflow)
Issues get labels like "claude-code" + "status:ready" which trigger the automation. Simple, observable, controllable. Humans can gate automation by withholding the label.

### 6. Declarative Workflow Config (Claude-Code-Workflow)
Define your pipeline in workflow.json -- stages, team members, cadence mode, CLI tools. The framework reads the config and orchestrates. Easy to modify without touching code.

### 7. Adaptation/Customization Skill (claude-pipeline)
A meta-skill that interviews you about your project and then customizes the pipeline configuration. Instead of one-size-fits-all, the pipeline adapts to each repo.

### 8. NDJSON Audit Trails (ccswarm)
Every agent action logged as NDJSON. Replayable. Diffable. Rollback-able. Essential for debugging multi-agent pipelines.

### 9. Consensus Before Implementation (ccswarm)
"Sangha consensus" -- multiple agents must agree on the plan before any implementation begins. Catches bad plans early.

### 10. Session Handoff Protocol (pro-workflow, mattpocock/skills)
Structured handoff documents with status, done items, pending tasks, decisions, gotchas, and resume commands. Essential for long-running pipelines that may be interrupted.

---

## Ecosystem Infrastructure

### Distribution Mechanisms
- **Claude Code Plugin Marketplace**: Native plugin install (`claude install <plugin>`)
- **SkillKit** (rohitg00/skillkit): Cross-agent translator, publish once and work on 32+ agents
- **npx skills add**: Package-manager style installation (`npx skills add glebis/claude-skills`)
- **Raw .claude/ directory copy**: Clone the .claude folder into your project

### Compatibility Layer
Most major repos now target multiple agents:
- Claude Code (primary)
- OpenAI Codex
- Gemini CLI
- Cursor
- OpenCode
- Windsurf
- GitHub Copilot

The "SkillKit" format seems to be emerging as a de facto standard for cross-agent skill portability.
