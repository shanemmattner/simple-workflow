# IndyDevDan (Dan Disler) -- Research Report

> Researched 2026-06-17. Note: his handle is spelled "IndyDevDan" (with a 'y'), not "IndieDevDan".

## Who He Is

- **GitHub**: [github.com/disler](https://github.com/disler) (30+ public repos, many with 100-3700+ stars)
- **YouTube**: [@indydevdan](https://www.youtube.com/@indydevdan)
- **Blog/Products**: [agenticengineer.com](https://agenticengineer.com)
- **Twitter/X**: [@IndyDevDan](https://x.com/indydevdan)
- **Secondary GitHub org**: [IndyDevDanAgents](https://github.com/IndyDevDanAgents) (mostly empty, links back to disler)

Sr. Engineer and indie developer. Betting the next 10 years of his career on agentic software. Produces educational content (courses: "Principled AI Coding" for beginners, "Tactical Agentic Coding" for advanced). Prolific open-source contributor in the Claude Code ecosystem.

---

## His Key Repos (Relevant to Workflow/Orchestration/Monitoring)

### 1. claude-code-hooks-multi-agent-observability (1,467 stars)
**The closest thing to a "dashboard" he's built.**

- **What**: Real-time monitoring platform for Claude Code agent swarms
- **Tech stack**: Bun + SQLite (WAL mode) backend, Vue 3 + Tailwind + Canvas charts frontend, Python hook scripts, WebSocket for live updates
- **Tracks 12 event types**: PreToolUse, PostToolUse, PostToolUseFailure, Notification, UserPromptSubmit, PermissionRequest, SessionStart, SessionEnd, Stop, SubagentStart, SubagentStop, PreCompact
- **Dashboard features**:
  - Event timeline with auto-scroll
  - Live pulse chart showing activity density with session-colored bars
  - Multi-criteria filtering (app, session ID, event type)
  - Chat transcript viewer with syntax highlighting
  - Dual-color coding (app colors + session colors)
  - Dark/light theme
- **How it works**: Hook scripts intercept Claude Code lifecycle events -> HTTP POST -> Bun server -> SQLite -> WebSocket -> Vue dashboard
- **Repo**: https://github.com/disler/claude-code-hooks-multi-agent-observability

### 2. claude-code-hooks-mastery (3,772 stars)
**His most-starred repo. Comprehensive hook toolkit.**

- **What**: 13 lifecycle hooks for controlling every stage of Claude Code execution
- **Teaches**: Validation layers, security controls, multi-agent orchestration
- **Key patterns**:
  - Sub-agent delegation (sub-agents start fresh, no conversation history)
  - Team-based validation (`/plan_w_team`) with builder + validator agents
  - Task dependencies and parallel execution
  - Status lines showing real-time session metadata (git info, tokens, cost)
  - Meta-Agent pattern ("build the thing that builds the thing")
- **Repo**: https://github.com/disler/claude-code-hooks-mastery

### 3. pi-agent-observability (105 stars)
**Same concept as #1, but for the Pi agent SDK.**

- **Tech stack**: Bun + SQLite backend, vanilla JS + Vue frontend
- **Dashboard views**: Single agent timeline, swimlane (N agents compared turn-by-turn), race (step-completion ordering)
- **Captures 16 event types** including full boot snapshots (exact system prompt, tools, skills loaded)
- **Repo**: https://github.com/disler/pi-agent-observability

### 4. the-library (377 stars)
**Skill/agent distribution system.**

- **What**: Meta-skill for distributing agentics (skills, agents, prompts) across agents, devices, and teams
- **How**: `library.yaml` stores pointers to skill locations (local paths or GitHub URLs). Skills pulled on-demand via `/library use deploy`
- **Not a task tracker** -- solves the "I have 10+ codebases with scattered agent skills" problem
- **Repo**: https://github.com/disler/the-library

### 5. infinite-agentic-loop (593 stars)
**Experimental parallel agent dispatch.**

- **What**: Two-prompt system that deploys sub-agents in parallel waves
- **Pattern**: `/project:infinite <spec_file> <output_dir> <count>` -- batches of 5 agents, infinite waves until context exhaustion
- **Repo**: https://github.com/disler/infinite-agentic-loop

### 6. fork-repository-skill (155 stars)
**Agent forking for parallel work.**

- **What**: Skill that forks the current agent N times into separate terminals
- **Use case**: Delegate work to independent agents running in parallel
- **Repo**: https://github.com/disler/fork-repository-skill

### 7. claude-code-damage-control (473 stars)
**Safety guardrails for agents.**

- **What**: PreToolUse hooks that validate commands against security rules
- **Features**: Bash command blocking, path-based access control (zero-access, read-only, no-delete), approval workflows for dangerous-but-valid ops
- **Repo**: https://github.com/disler/claude-code-damage-control

### 8. Other Notable Repos
- **bowser** (248 stars): Agentic browser automation with composable 4-layer architecture (skills -> subagents -> commands -> justfiles)
- **mac-mini-agent** (264 stars): CLI + skills for operating Mac devices with agents
- **big-3-super-agent** (298 stars): Gemini Computer Use + OpenAI Realtime + Claude Code multi-agent experiment
- **live-bench** (80 stars): FastAPI + Vue 3 benchmarking platform with real-time SSE bar charts for local LLM performance
- **agent-sandbox-skill** (375 stars): Isolated execution environments for agents
- **just-prompt** (733 stars): MCP server for unified LLM provider interface

---

## His Philosophy on Task Tracking & Workflow

From his 2026 roadmap at agenticengineer.com:

1. **Minimal tooling over dashboards**: "Build the minimum tooling -- not even an MCP server." He does NOT recommend heavy task tracking infrastructure.

2. **Deferred trust model**: "Let the agent complete the work, then pull the result down when ready." Rather than watching agents constantly, verify output at the end.

3. **Progressive scaling**: "You don't start with 30 agents, you start with 1, then 2, then 3." Build trust incrementally.

4. **Out-loop vs in-loop**: Maximize "out-loop" work (agents running autonomously) and minimize "in-loop" work (you watching them). Build trust gradually.

5. **Best-of-N validation**: Spin up multiple agents on the same task, verify the winner's output.

6. **Sandbox isolation**: Agents run in isolated environments. Trust is deferred "until the merge."

**Bottom line**: Dan builds observability tools (dashboards for watching hooks/events) but does NOT build or recommend traditional task management (Kanban boards, issue trackers, Gantt charts). His approach is: give agents work, sandbox them, watch the hooks if you need to debug, verify the output.

---

## Tools He Recommends

- **Claude Agent SDK** -- primary recommendation for custom agents
- **Pi Agent SDK** -- "great open-source, zero lock-in solution"
- **OpenClaw** -- personal AI assistant for multi-agent systems (runs on Mac Mini)
- **just** (task runner) -- used across nearly all his repos as the command orchestrator
- **uv** (Astral) -- Python package manager of choice
- **Bun** -- TypeScript runtime for servers
- **SQLite (WAL mode)** -- database for all his observability tools
- **Vue 3** -- frontend framework for dashboards

---

## Broader Ecosystem: Agent Dashboards & Workflow Tools

### Official: Claude Code Agent View (May 2026)
- Built into Claude Code v2.1.139+
- CLI dashboard showing every background session at a glance
- Status per agent (working, waiting for input, done)
- Peek preview, inline reply, background execution
- Worktree isolation per session
- Launch: `claude agents` from any session

### Community-Built Dashboards

**Claude Code Agent Monitor** (hoangsonww)
- https://github.com/hoangsonww/Claude-Code-Agent-Monitor
- Tech: Node.js, Express, React 18, SQLite3, WebSockets, D3.js, Electron
- Features: Kanban board (agents + sessions), D3.js workflow DAGs + Sankey diagrams, cost tracking, token analytics, subagent hierarchy trees, rules-based alerting (14 webhook providers), Web Push notifications, MCP server (25 tools), VS Code extension, macOS/Windows desktop app
- Most feature-rich community dashboard

**Orchestro** (khaoss85)
- https://github.com/khaoss85/mcp-orchestro
- "Trello for Claude Code" with built-in Scrum Master
- MCP server + web dashboard
- Auto-decomposes user stories into technical tasks with dependencies
- Agents move cards across the Kanban board as work happens
- `npx @orchestro/init` then `npm run dashboard` on localhost:3000

**CCFlow** (ScaleLabs-Dev)
- https://github.com/ScaleLabs-Dev/CCFlow
- Workflow system with TDD enforcement, memory bank context management
- 6 workflow agents + 3 implementation agents
- 13 `/cf:` commands for init, plan, code, review
- 4-level complexity assessment for task routing

**Observatory** (cognoco) -- fork of Dan's observability repo
- https://github.com/cognoco/observatory

### Lightweight Self-Hosted Task Trackers (Not Claude-Specific)

- **Leaf** (github.com/wezm/leaf): Single binary, ~160us response, ~1.4MB RAM
- **Super Productivity** (noted.lol): Jira/GitHub/GitLab integrations, privacy-focused
- **Uptime Kuma**: Self-hosted status page monitoring
- **Gatus**: Kubernetes-native config-as-code status pages

---

## Key Takeaways for Our System

1. **Dan's approach aligns with ours**: He builds lightweight observability (SQLite + hooks + Vue dashboard) rather than heavy project management tools. His multi-agent-observability repo is the closest model to what we might want.

2. **The hook-based event pipeline is the pattern**: Claude Code hooks -> HTTP POST -> SQLite -> WebSocket -> live dashboard. This is the standard architecture everyone converges on.

3. **Official Agent View covers basic needs**: Claude Code's built-in `claude agents` command handles session management. Community tools add Kanban boards, cost tracking, and alerting on top.

4. **Orchestro is the most interesting "task board" option**: It's the only tool that combines Kanban-style task management with actual agent dispatch through MCP. Worth evaluating if we want a visual task board.

5. **Dan's stack choices**: just + uv + Bun + SQLite(WAL) + Vue 3. Consistently across all his repos. Battle-tested combo for lightweight agent tooling.
