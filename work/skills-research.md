# Skills Research: mattpocock/skills and the Broader Ecosystem

Research date: 2026-06-16

---

## 1. Architecture Overview — mattpocock/skills

### What it is

A collection of **15 published agent skills** (slash commands) for Claude Code, organized as pure markdown files. No runtime code, no framework, no database. Each skill is a prose document that instructs the LLM how to behave when invoked.

### Structure

```
skills/
├── engineering/          # Daily code work (10 skills)
│   ├── triage/
│   │   ├── SKILL.md          # Main instructions
│   │   ├── AGENT-BRIEF.md    # Supporting reference
│   │   └── OUT-OF-SCOPE.md   # Supporting reference
│   ├── tdd/
│   │   ├── SKILL.md
│   │   ├── tests.md
│   │   ├── mocking.md
│   │   ├── deep-modules.md
│   │   ├── interface-design.md
│   │   └── refactoring.md
│   ├── grill-with-docs/
│   ├── to-issues/
│   ├── to-prd/
│   ├── diagnose/
│   ├── improve-codebase-architecture/
│   ├── setup-matt-pocock-skills/
│   ├── zoom-out/
│   └── prototype/
├── productivity/         # Non-code workflow (5 skills)
│   ├── caveman/
│   ├── grill-me/
│   ├── handoff/
│   ├── teach/
│   └── write-a-skill/
├── misc/                 # Rarely used
├── personal/             # Not promoted
├── in-progress/          # Drafts
└── deprecated/           # No longer used
scripts/
├── list-skills.sh
└── link-skills.sh        # Symlinks skills to ~/.claude/skills/
.claude-plugin/
└── plugin.json           # Registry of published skills
CONTEXT.md                # Domain glossary for the repo itself
CLAUDE.md                 # Meta-instructions for working on this repo
docs/adr/                 # Architectural decision records
.out-of-scope/            # Rejected feature requests (institutional memory)
```

### How it works

1. **Installation**: `npx skills@latest add mattpocock/skills` (via skills.sh marketplace) or manual symlink via `scripts/link-skills.sh`
2. **Plugin manifest**: `.claude-plugin/plugin.json` lists skill directory paths
3. **Invocation**: User types `/skill-name` in Claude Code; the agent loads `SKILL.md` and follows it
4. **Per-repo config**: `/setup-matt-pocock-skills` writes `docs/agents/issue-tracker.md`, `docs/agents/triage-labels.md`, `docs/agents/domain.md` so other skills know how to interact with the project
5. **Progressive disclosure**: `SKILL.md` is the entry point (kept under 100 lines); supporting files are loaded on demand via relative links

### Key design decisions

- **No code. Pure prose.** Skills are markdown instructions, not scripts. The LLM reads them and follows them.
- **Composable, not monolithic.** Each skill does one thing. `/grill-with-docs` can be called from within `/triage`. `/improve-codebase-architecture` suggests running `/grill-with-docs` when terms need sharpening.
- **Per-repo state lives in the repo itself.** `CONTEXT.md`, `docs/adr/`, `.out-of-scope/` are all committed files. No external database.
- **Explicit about what it DOESN'T do.** ADR 0001 documents why only hard-dependency skills mention `/setup-matt-pocock-skills`.

---

## 2. Patterns Worth Stealing

### Pattern 1: The Domain Glossary (CONTEXT.md)

Every skill that explores the codebase is told to "use the project's domain glossary vocabulary." `CONTEXT.md` is a tightly formatted file:

```markdown
**Order**:
A confirmed purchase placed by a Customer.
_Avoid_: Purchase, transaction
```

**Why it's powerful**: Agents produce consistent naming across issues, tests, commits, and code. Reduces token waste on synonyms. Makes the codebase AI-navigable.

**Steal**: Our pipeline should read/write a `CONTEXT.md` and inject it into every agent prompt.

### Pattern 2: Agent Briefs (Durable Task Specs)

The triage skill produces "agent briefs" — structured comments on GitHub issues that serve as contracts for AFK agents. Key principles:

- **Describe interfaces, not file paths** (paths go stale)
- **Behavioral, not procedural** (what, not how)
- **Complete acceptance criteria** (agent knows when it's done)
- **Explicit scope boundaries** (prevents gold-plating)

**Steal**: Our issue-to-PR pipeline should produce agent briefs as the handoff format. The brief IS the prompt for the implementation agent.

### Pattern 3: State Machine Triage with Labels

Issues move through canonical roles: `needs-triage` -> `needs-info` / `ready-for-agent` / `ready-for-human` / `wontfix`. The label mapping is configurable per repo. This gives the pipeline a clear "ready-for-agent" signal.

**Steal**: Our pipeline needs exactly this. GitHub labels ARE the coordination mechanism. `ready-for-agent` means "this issue has a complete brief and can be picked up."

### Pattern 4: Progressive Disclosure via File Links

Skills are split into a short `SKILL.md` (under 100 lines) plus supporting files loaded on demand. This keeps context window usage proportional to need.

**Steal**: Our prompts should reference supporting docs by path but only load them when relevant.

### Pattern 5: Institutional Memory (.out-of-scope/)

Rejected feature requests are documented in `.out-of-scope/` files. During triage, these are checked for matches. This prevents re-litigating decisions.

**Steal**: For a pipeline, this could be a `rejected-approaches.md` or similar that agents read before proposing solutions.

### Pattern 6: The Grilling Session (Alignment Before Action)

`/grill-me` and `/grill-with-docs` are one-question-at-a-time interviews that force the user to be precise. The agent provides recommended answers. Questions it can answer from code, it answers itself.

**Steal**: Before generating a PR, the pipeline should "grill" the issue: is it complete enough? Can questions be answered from code? If not, post a `needs-info` comment.

### Pattern 7: The Feedback Loop as First-Class Concept

`/diagnose` spends 60% of its word count on Phase 1 (building a feedback loop). The insight: "If you have a fast, deterministic, agent-runnable pass/fail signal, you will find the cause."

**Steal**: Every implementation agent should start by establishing a test or check it can run repeatedly. This is the "tracer bullet" from `/tdd`.

### Pattern 8: Vertical Slices (Tracer Bullets)

`/to-issues` breaks work into thin end-to-end slices, not horizontal layers. Each slice is independently demoable. Issues are published in dependency order.

**Steal**: When decomposing a complex issue into sub-tasks, use vertical slicing.

---

## 3. Tools and Agents

### mattpocock/skills defines NO tools or agents in the runtime sense.

The skills are pure instructions. They use whatever tools Claude Code already has:
- `gh` CLI for GitHub interactions
- File system tools (Read, Write, Edit) for docs
- Git for version control
- The Agent tool (subagents) for parallel work in `/improve-codebase-architecture`

### Agent composition patterns

1. **Sequential handoff**: `/triage` calls `/grill-with-docs` when an issue needs fleshing out
2. **Parallel subagents**: `/improve-codebase-architecture` spawns 3+ interface design subagents via the Agent tool, each with a different design constraint
3. **Cross-session continuity**: `/handoff` compresses context for the next agent; `/teach` persists learning state across sessions via filesystem

### Contrast with gstack (Garry Tan's toolkit)

gstack is much more infrastructure-heavy:
- **59 skills** with compiled Bun binaries
- **Persistent Chromium daemon** for browser access
- **State files** (`.gstack/browse.json`) for daemon lifecycle
- **Session tracking** (`~/.gstack/sessions/`, `timeline.jsonl`)
- **Learnings database** (per-project `learnings.jsonl`)
- **Decision log** (`decisions.active.json`, `gstack-decision-search`)
- **Multi-model orchestration**: Codex and Claude subagents run in parallel, producing consensus tables
- **Preamble scripts**: Every skill starts with ~130 lines of bash that establishes session state, checks for updates, loads config, syncs artifacts
- **Telemetry**: `skill-usage.jsonl`, `eureka.jsonl`, remote telemetry opt-in

gstack's `autoplan` skill is a full 4-phase review pipeline (CEO -> Design -> Eng -> DX) that auto-decides intermediate questions using 6 principles and surfaces "taste decisions" at a final gate.

---

## 4. Prompt Design

### mattpocock/skills — Prose-first, opinionated, structured

Key techniques:

**1. XML-style section markers for progressive disclosure:**
```markdown
<what-to-do>
Interview me relentlessly...
</what-to-do>

<supporting-info>
## Domain awareness
...
</supporting-info>
```

**2. YAML frontmatter for metadata:**
```yaml
---
name: tdd
description: Test-driven development with red-green-refactor loop. Use when...
---
```
The `description` is the ONLY thing the agent sees when deciding whether to load the skill. It must be specific enough for routing.

**3. Explicit "Use when" triggers in descriptions:**
```
Use when user wants to build features or fix bugs using TDD, mentions "red-green-refactor"...
```

**4. Anti-patterns called out explicitly:**
The `/tdd` skill devotes a full section to "Anti-Pattern: Horizontal Slices" with a concrete WRONG/RIGHT comparison. This is more effective than just stating the right way.

**5. Checklists embedded in workflow steps:**
```
[ ] Test describes behavior, not implementation
[ ] Test uses public interface only
[ ] Test would survive internal refactor
```

**6. No JSON demands in prompts.** Everything is natural language with markdown formatting. Templates use markdown code blocks as examples, not as strict schemas.

**7. The "disable-model-invocation" flag:**
```yaml
disable-model-invocation: true
```
This means the skill is ONLY loaded when explicitly invoked (not auto-suggested). Used for `/zoom-out` (which is just a single sentence) and `/setup-matt-pocock-skills`.

### gstack — Heavier, more structured, with decision frameworks

gstack uses:
- **Decision briefs** with a strict format (D-numbering, ELI10, pros/cons, completeness scores)
- **6 Decision Principles** for auto-answering intermediate questions
- **Classification taxonomy** (Mechanical / Taste / User Challenge)
- **Model-specific behavioral patches** (tuned per model family)
- **Voice guidelines** with explicit banned words list
- **Preamble bash scripts** that establish runtime context before the skill runs

---

## 5. Observability / Debugging

### mattpocock/skills — Minimal

There is essentially no observability built in. The philosophy is that the conversation IS the log. Skills reference their outputs (issues posted, docs written, tests created) as the evidence trail.

The `/diagnose` skill has tagged debug logs (`[DEBUG-a4f2]`) as a best practice for instrumenting code, but this is advice to the agent about debugging the USER's code, not about observing the skill itself.

### gstack — Rich local observability

- **Timeline log**: `~/.gstack/projects/$SLUG/timeline.jsonl` records every skill start/complete with branch, duration, outcome
- **Skill usage analytics**: `~/.gstack/analytics/skill-usage.jsonl`
- **Question log**: Every AskUserQuestion and its answer is logged for `/plan-tune` learning
- **Decision log**: Durable decisions stored in `decisions.active.json` with search
- **Learnings database**: Per-project operational insights in `learnings.jsonl`
- **Eureka log**: First-principles insights that contradict conventional wisdom
- **Checkpoint mode**: WIP commits with structured `[gstack-context]` metadata
- **Eval system**: E2E tests with LLM judges, diff-based selection, two-tier (gate/periodic)

### Comparable approaches in the ecosystem

- **claude-orchestrator**: SQLite database for all task state, suggestions, quality gates
- **Claustre**: SQLite database of projects, tasks, sessions, subtasks; hooks into Claude Code lifecycle events (Stop, UserPromptSubmit, TaskCompleted)
- **claude-plan-orchestrator**: Markdown plans parsed into tasks, dispatched to worktree agents

---

## 6. Database / State Management

### mattpocock/skills — Filesystem only

- `CONTEXT.md` = glossary
- `docs/adr/` = decision log
- `.out-of-scope/` = rejection log
- `docs/agents/` = per-repo config
- GitHub Issues = task state (labels = workflow state)

No database. No external services. Everything is files in the repo or on GitHub.

### gstack — ~/.gstack/ directory tree

```
~/.gstack/
├── sessions/                    # Active session tracking
├── analytics/
│   ├── skill-usage.jsonl       # Telemetry
│   └── eureka.jsonl            # Insights
├── projects/
│   └── $SLUG/
│       ├── ceo-plans/          # Plan artifacts
│       ├── checkpoints/        # WIP state
│       ├── timeline.jsonl      # Session log
│       ├── decisions.active.json
│       ├── learnings.jsonl
│       └── *-reviews.jsonl
├── .brain-last-pull            # Sync state
└── .brain-queue.jsonl          # Pending sync items
```

### What the ecosystem uses for orchestration state

| System | State Store | Key Insight |
|--------|------------|-------------|
| claude-orchestrator | SQLite | PRD -> tasks -> workers -> PRs, all in one DB |
| Claustre | SQLite | projects, tasks, sessions, subtasks; hook-driven state sync |
| claude-plan-orchestrator | Markdown + Git | Plans parsed to tasks, worktree isolation |
| gstack | JSONL files + Git | Append-only logs, git-backed sync |
| mattpocock/skills | GitHub Issues + files | Labels as state machine, files as docs |

---

## 7. Anything Else Notable

### Things that are clever

1. **The `.out-of-scope/` pattern is genius.** It's cheap institutional memory that prevents repeated discussion of the same rejected ideas. One file per concept, not per issue.

2. **"Durability over precision" in agent briefs.** The explicit instruction to avoid file paths and line numbers because "the codebase will change" shows deep understanding of async agent workflows.

3. **The deletion test** from `/improve-codebase-architecture`: "Imagine deleting the module. If complexity vanishes, it was a pass-through. If complexity reappears across N callers, it was earning its keep." This is a brilliantly simple heuristic.

4. **Lazy file creation.** Skills never create `CONTEXT.md` or `docs/adr/` proactively. They create them when there's actually something to write. This avoids empty scaffold.

5. **`/caveman` mode** — a skill that just changes the agent's communication style to save ~75% tokens. Activates persistently until explicitly turned off.

6. **The prototype branch pattern** — routing to either LOGIC (terminal TUI) or UI (multi-variant page with switcher bar) based on what question is being answered. Both produce throwaway code with a clear "capture the answer, delete the code" lifecycle.

### Things that are bad or missing

1. **No observability whatsoever.** If a triage run goes wrong, there's no log to review. The conversation is ephemeral.

2. **No multi-run coordination.** Each skill invocation is a single session. There's no concept of "this is run 3 of a multi-step pipeline." Handoff is manual.

3. **No persistence between sessions** (beyond filesystem artifacts). If you `/triage` today and come back tomorrow, the agent starts fresh and must re-read everything.

4. **The GitHub issue is implicitly the coordination point** but there's no explicit pipeline runner. You have to manually invoke `/triage` then `/to-issues` then hand off to an AFK agent. The human is the orchestrator.

5. **No testing of skills themselves.** Unlike gstack which has an eval suite, mattpocock/skills has no way to verify a skill still works correctly after changes.

### Things that surprised me

1. **How small and readable the skills are.** The most complex skill (`/triage`) is ~100 lines of SKILL.md plus 2 reference docs. This is extremely accessible compared to gstack's 1800-line autoplan.

2. **The `CONTEXT.md` approach is essentially DDD's Ubiquitous Language** applied to AI agents. The "Avoid" fields are particularly clever — they prevent the agent from using synonyms that create confusion.

3. **The write-a-skill meta-skill** has a specific constraint: "SKILL.md under 100 lines." This is a hard budget that forces progressive disclosure.

4. **The `in-progress/review` skill** spawns parallel sub-agents for Standards and Spec axes — a simple but effective dual-reviewer pattern.

---

## 8. Implications for Our Issue-to-PR Pipeline

### What to adopt from mattpocock/skills

| Pattern | Our Application |
|---------|----------------|
| Agent briefs | The format for issue -> implementation handoff |
| Label state machine | `needs-triage` -> `ready-for-agent` -> `in-progress` -> `review` |
| CONTEXT.md | Inject into every agent prompt for consistent naming |
| Prose-first prompts | No JSON schema demands; markdown templates |
| Vertical slicing | Decompose complex issues before implementation |
| The feedback loop | Every implementation starts with a test |
| .out-of-scope/ | Track rejected approaches per issue |

### What to adopt from gstack/ecosystem

| Pattern | Our Application |
|---------|----------------|
| SQLite for run state | Single DB per pipeline run with tasks, turns, decisions |
| Timeline logging | Append-only log of what happened for post-hoc analysis |
| Decision logging | Record why choices were made (for learning/debugging) |
| Multi-model consensus | Two models review independently, disagreements flagged |
| Preamble scripts | Establish context (branch, project, prior state) before agent starts |
| Eval system | Reproducible tests that verify skills still work |

### What to avoid

| Anti-pattern | Why |
|--------------|-----|
| gstack's 130-line bash preambles | Fragile, hard to debug, couples skills to infrastructure |
| gstack's telemetry/onboarding in every skill | Bloats every invocation, distracts from the task |
| mattpocock's lack of observability | Can't debug failures or learn from past runs |
| mattpocock's manual orchestration | Human must sequence skills; no pipeline runner |
| gstack's 1800-line monolithic skills | Impossible to study or modify incrementally |

### Architecture sketch for our pipeline

```
GitHub Issue (with label: ready-for-agent)
    │
    ▼
Pipeline Runner (bash, triggered by label or cron)
    │
    ├─ Creates SQLite DB for this run
    ├─ Reads issue + agent brief
    ├─ Reads CONTEXT.md
    ├─ Composes prompt (prose-first, agent brief as spec)
    │
    ▼
claude "<prompt>" --output-format json --model <model> \
       --permission-mode auto --max-turns N
    │
    ├─ Agent has full tool access (git, gh, tests, etc.)
    ├─ Each turn logged to SQLite
    ├─ Agent establishes feedback loop (test) first
    ├─ Agent implements in vertical slices
    │
    ▼
PR Created
    │
    ├─ SQLite DB closed (self-contained artifact)
    ├─ Issue label updated: in-review
    └─ Link to PR posted as issue comment
```

### Key design principles

1. **GitHub issue = coordination point.** Labels are the state machine. Comments are the communication channel. The issue body (or agent brief comment) is the spec.
2. **SQLite DB = run artifact.** One DB per run. Contains: prompt, turns, tool calls, decisions, outcome. Can be replayed, audited, compared.
3. **Prose-first prompts.** Inject CONTEXT.md vocabulary. Use agent brief format. No JSON demands.
4. **Modular skills as prompt fragments.** Each "skill" is a markdown file that gets composed into the prompt based on what the task needs.
5. **The agent IS the pipeline.** Multi-turn with full tool access. No micro-orchestration of individual steps from outside.

---

## Sources

- [mattpocock/skills](https://github.com/mattpocock/skills) — primary research target
- [garrytan/gstack](https://github.com/garrytan/gstack) — 59-skill toolkit with browser daemon and multi-model review
- [levnikolaevich/claude-code-skills](https://github.com/levnikolaevich/claude-code-skills) — Agile pipeline with multi-model AI review
- [claude-did-this/claude-hub](https://github.com/claude-did-this/claude-hub) — Webhook service connecting Claude Code to GitHub
- [Hochfrequenz/claude-plan-orchestrator](https://github.com/Hochfrequenz/claude-plan-orchestrator) — Autonomous dev orchestrator with worktree isolation
- [rohitg00/awesome-claude-code-toolkit](https://github.com/rohitg00/awesome-claude-code-toolkit) — Comprehensive toolkit listing
- [nwiizo/ccswarm](https://github.com/nwiizo/ccswarm) — Multi-agent orchestration with git worktree isolation
- [VoltAgent/awesome-agent-skills](https://github.com/VoltAgent/awesome-agent-skills) — 1000+ agent skills collection
