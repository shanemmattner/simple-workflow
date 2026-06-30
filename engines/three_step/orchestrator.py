"""Three-step orchestrator: investigate -> implement -> review+PR.

Uses the Claude CLI subscription runtime (engines.three_step.claude_runtime)
which invokes `claude` with --output-format json. Reuses shared modules from
github_openhands for source, storage, workspace, and destination.
"""
from __future__ import annotations

import glob
import json
import logging
import os
import re
import subprocess
from datetime import datetime
from pathlib import Path
from typing import Any

from engines.three_step import claude_runtime
from engines.github_openhands import source, storage, workspace, destination

log = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")

# ---------------------------------------------------------------------------
# Prompt templates (embedded, no separate files)
# ---------------------------------------------------------------------------

INVESTIGATE_PROMPT = """\
You are investigating GitHub issue #{issue_number}.

Your working directory is the target repository. All paths are relative to it.

## Issue

{issue_body}

## Instructions

Investigate this issue using symptom-first tracing. Do NOT search the codebase broadly.

### Step 1: Identify the entry point (turns 1-3)

Begin from the user-visible symptom described in the issue. What UI element, page, \
or API endpoint is involved? Extract the user-facing action: "user does X on page Y, \
then Z breaks." Find the component or handler that directly handles that action. \
This is your starting point.

Do NOT search for keywords from the issue title. Trace from the UI entry point inward.

### Step 2: Trace the code path (turns 4-10)

Trace the symptom through the code: component -> handler -> service -> data layer. \
Use grep and file reads to follow the call chain.

From the entry point, trace through the call chain:
- What function handles the user action?
- What side effects does it trigger (API calls, state updates, navigation)?
- Where does the behavior diverge from what the user expects?

Read only files on this call path. Do NOT grep the entire codebase for keywords.

### Step 3: Construct a reproduction model (turns 11-13)

Write a numbered sequence: "1. User does A -> 2. Code calls B -> 3. B triggers C -> \
... -> N. Bug manifests as Z." If you cannot connect the proposed root cause to the \
user's symptom through a concrete code path, your root cause is WRONG. Go back to step 2.

### Step 4: Produce the report (turns 14-18)

You have a STRICT turn budget. By turn 19, you MUST stop calling tools and write \
your final investigation report as a plain text message.

If you keep calling tools until you run out of turns, your report will be lost. \
STOP and WRITE before the limit.

If you cannot identify a clear root cause within 15 turns, state what you found \
and what remains uncertain. Do NOT guess.

Produce a structured investigation report with these sections:

1. **Symptom Chain** -- the numbered reproduction sequence from step 3
2. **Root Cause** -- what exactly is wrong and why, citing specific file:line
3. **Affected Files** -- ONLY files that need to change (not files you read for context)
4. **Proposed Fix** -- for each file, show the minimal before/after code changes
5. **Risk Assessment** -- what could go wrong, confidence level (HIGH/MEDIUM/LOW)
6. **Related Issues** -- other issues that amplify or compound this bug (separate, not root cause)
7. **Peer Implementations** (mandatory when integrating with an existing API/system) -- \
when the task involves integrating with, extending, or following an existing pattern \
(API, registry, component system, state machine), identify at least 2 existing consumers \
of that API. For each, document:
   - Where they call it (file:line)
   - How often (once on init, on every state change, on specific events)
   - What lifecycle hooks or state triggers the call
This section prevents "correct structure, wrong behavior" failures where the implementation \
looks right but misses the usage contract.

### Workstream discipline

Identify the PRIMARY workstream for this issue (frontend, backend, database, \
infrastructure, etc.) and stay within it. Do NOT attempt fixes outside your \
primary workstream.

If you discover work needed in a different workstream (e.g., a frontend issue \
that needs a new API endpoint, or a UI fix that requires a database migration), \
do NOT fold it into your investigation. Instead:

1. Note the dependency in your report under **Related Issues**
2. Include a `gh issue create` command for the dependency:
   `gh issue create --title "..." --body "Dependency of #{issue_number}. ..." --repo REPO`
3. Proceed with ONLY the work in your primary workstream

Your report's **Affected Files** must contain files from a single workstream. \
Mixed workstreams produce overcomplicated PRs that fail review.

### Anti-patterns to avoid

- Do NOT fixate on the first plausible-sounding pattern match. If you find something \
that COULD cause the symptom but requires an optional feature to be active, check \
whether the bug reproduces without that feature.
- Do NOT grep broadly for error message keywords. Start from the UI component, not \
from infrastructure code.
- Do NOT propose fixes for secondary issues you discover along the way. Note them \
under Related Issues and stay focused on the primary root cause.

Be specific. Reference actual file paths, function names, and line numbers.
Do NOT make changes -- investigation only.
"""

IMPLEMENT_PROMPT = """\
You are implementing a fix for GitHub issue #{issue_number}.

Your working directory is the target repository. All file paths are relative to \
the repository root. Use `ls` and `find` to orient yourself in the first 2 turns \
if needed, but do NOT spend more than 2 turns exploring.

## Issue

{issue_body}

## Investigation Report

{investigation_report}

## Instructions

The investigation report above tells you what to change. Do NOT re-investigate. \
Start editing files immediately.

If the investigation report's proposed fix has specific before/after code, apply \
those changes directly using edit_file. Do not rewrite files from scratch.

1. Read only the files listed in "Affected Files" from the investigation report
2. Apply the code changes identified in the investigation -- match the before/after \
code shown in the report
3. After making changes, verify with:
   (a) Run any relevant test command from the repo to confirm nothing is broken
   (b) A quick grep to confirm your changes are in place
4. You MUST add or update at least one test that verifies your fix. If the repo \
has no test infrastructure for this area of code, state that explicitly in your \
summary with the reason why no test was added. "If appropriate" is not a valid \
reason to skip -- default to writing a test.
5. Before declaring done, list every acceptance criterion from the issue and confirm \
each is addressed by your changes. If any criterion is not covered, report it \
explicitly.
6. After making all changes, run `git add -A && git commit -m 'fix: resolve #{issue_number}'`

If the investigation report is wrong (a file doesn't exist, the code doesn't match \
what's described), STOP and report the discrepancy as your final message. Do not \
improvise a different fix.

When done, write a summary of what you changed as your final message.

Keep changes minimal. Do not refactor unrelated code. Do not leave debug \
statements or commented-out code.
"""

REVIEW_PROMPT = """\
You are an adversarial code reviewer for GitHub issue #{issue_number}.

You have FULL access to the codebase. Your working directory is the target repository. \
You can read any file, grep for patterns, and explore the code to verify claims. \
Use your tools aggressively -- do not trust the diff at face value.

Your job is to find problems, not confirm the fix works. Assume the implementation is \
wrong until you have verified otherwise by reading the actual code.

## Issue

{issue_body}

## Investigation Report

{investigation_report}

## Diff

```diff
{diff}
```

## Review Protocol

Work through each check below. For each one, use your tools to verify -- do not skip \
any check or rely on inference.

### 1. Read the diff carefully
Understand every changed line. Note anything that looks suspicious, incomplete, or \
inconsistent.

### 2. Verify changes in context
For every file modified in the diff, read the surrounding code (at least 50 lines \
above and below each change site). Verify the change makes sense in context -- correct \
variable names, correct function signatures, no broken imports, no violated assumptions.

### 3. Verify new code is wired up
For every new component, function, hook, utility, or export created in the diff, grep \
the codebase to verify it is actually imported and used somewhere. Dead code that was \
written but never connected is a common failure mode.

### 4. Check acceptance criteria coverage
Extract every acceptance criterion from the issue. For each one, find the specific \
line(s) in the diff that address it. If any criterion is not covered by the diff, \
flag it explicitly. Do not assume a criterion is met -- find the code.

### 5. Check for missing tests
If no test file was added or modified in the diff, flag it. If a test was added, read \
it and verify it actually tests the fix (not just a stub or trivially passing test).

### 6. Check for debug artifacts
Scan the diff for: console.log, console.warn, console.error (unless intentional logging), \
debugger statements, TODO/FIXME/HACK comments, commented-out code blocks, hardcoded \
values that should be configurable.

### 7. Verify investigation alignment
Compare the investigation report's stated root cause with what was actually fixed in \
the diff. If they diverge (e.g., investigation says "problem is in X" but diff changes Y), \
flag the mismatch.

## Output Format

Structure your response in two clearly separated sections:

### PR Description
Write a factual summary of what the diff changes. Only reference code that exists \
in the diff or that you have read from the repository. This section will be used as \
the PR body.

### Review Assessment
Your technical review with:
- **What changed** -- brief description of the fix
- **How it works** -- technical explanation referencing specific code you read
- **Issues found** -- every problem discovered during the review protocol above
- **Testing** -- what was tested, or what testing is missing
- **Verdict** -- your verdict MUST be one of: APPROVE, REQUEST_CHANGES, NEEDS_DISCUSSION

If you would not pass this in a real code review, verdict MUST be REQUEST_CHANGES.

If verdict is REQUEST_CHANGES, list each required change as a numbered item with \
the specific file and what needs to change.

Only describe code you can see in the diff or files you have read. Never infer \
behavior -- verify it.
"""

IMPLEMENT_RETRY_PROMPT = """\
You are re-implementing a fix for GitHub issue #{issue_number}.

Your working directory is the target repository. All file paths are relative to \
the repository root.

## Issue

{issue_body}

## Investigation Report

{investigation_report}

## Previous Review Findings

Your previous implementation was rejected by the reviewer. Here are the findings:

{review_findings}

## Rejected Diff

```diff
{rejected_diff}
```

## Instructions

Fix ALL the issues identified by the reviewer. Do not re-investigate — the \
investigation report is still valid. Focus only on fixing the specific problems \
listed above.

1. Read the reviewer's findings carefully — each numbered item is a required fix
2. For each finding, make the specific change requested
3. After making changes, verify with:
   (a) Run any relevant test command from the repo to confirm nothing is broken
   (b) A quick grep to confirm your changes are in place
4. After making all changes, run `git add -A && git commit -m 'fix: address review feedback for #{issue_number}'`

Keep changes minimal. Do not refactor unrelated code. Do not leave debug \
statements or commented-out code.
"""

DEEP_REVIEW_PROMPT = """\
You are an adversarial deep reviewer for GitHub issue #{issue_number}.

You have FULL access to the codebase. Your working directory is the target repository. \
Use tools aggressively -- read files, grep for patterns, run tests. Your job is to find \
problems the initial review missed.

## Issue

{issue_body}

## Investigation Report

{investigation_report}

## Diff

```diff
{diff}
```

## Initial Review

{review_text}

## PR

{pr_url}

## Deep Review Protocol

Work through EVERY step below. Do not skip any.

### 1. Read every file in the diff
For each modified file, read the full file (or at least 100 lines of surrounding context \
around each change site). Understand how the change fits into the broader module.

### 2. Verify new code is wired up
For every new component, function, import, hook, or export created in the diff, grep the \
codebase to verify it is actually imported and used somewhere. Dead code that was written \
but never connected is a common failure mode.

### 3. Check acceptance criteria
Extract every acceptance criterion from the issue. For each one, find the specific line(s) \
in the diff that address it. If any criterion is not covered, flag it explicitly.

### 4. Run tests
If the repo has a test command (check package.json scripts, Makefile, pytest.ini, etc.), \
run it. Report the results.

### 5. Look for problems
Scan for:
- Missing error handling (what happens when X fails?)
- Null safety gaps (what if this value is undefined/null?)
- Security issues (user input validation, auth checks, SQL injection, XSS)
- Performance problems (N+1 queries, unbounded loops, missing indexes)
- Missing tests for the new code
- Debug artifacts (console.log, debugger, TODO/FIXME, hardcoded values)
- Race conditions or state management issues

### 6. Check for regressions
Read related components that interact with the changed code. Could this fix break any \
other feature? Check imports, shared state, and downstream consumers.

### 7. Compare investigation vs implementation
Compare the investigation report's proposed fix against what was actually implemented. \
Flag any divergence.

## Output Format

List every problem found with specific file:line references.

End with a verdict -- one of:
- **APPROVE** -- merge-ready, no significant issues found
- **CONCERNS** -- merge with noted risks (list them)
- **BLOCK** -- should not merge without changes (list specific corrections needed)

If verdict is BLOCK or CONCERNS, list each correction as a numbered item with the \
specific file and what needs to change.
"""

EXTRACT_LESSONS_PROMPT = """\
You are a pipeline learning extractor. Analyze the pipeline run results below \
and extract actionable lessons that would prevent future failures or improve \
pipeline quality.

## Pipeline Outcome: {pipeline_outcome}

{pipeline_outcome_detail}

## Audit Analysis

{audit_text}

## Review Corrections

{review_text}

## Deep Review Findings

{deep_review_text}

## Run Metadata

- Repo: {repo}
- Issue: #{issue_number}
- Run ID: {run_id}
- Cost: ${total_cost:.2f}

## Instructions

Extract 0-5 lessons from this run. Each lesson MUST be:
1. Specific and actionable — not "investigate better" but "investigate prompt \
should require checking observer cleanup patterns when adding event listeners"
2. Grounded in DIRECT EVIDENCE from the audit/review text — quote the relevant \
finding
3. Something that would prevent the SAME class of failure in a FUTURE run on \
a DIFFERENT issue

For each lesson, classify its type:
- pipeline_prompt: a missing constraint or anti-pattern in a pipeline phase \
prompt (investigate/implement/review). Include which phase and what to add.
- repo_knowledge: factual knowledge about the target repo's codebase that \
the pipeline didn't know. Include which file/pattern was missed.
- cross_repo: a general engineering pattern applicable across all repos. \
Write it as a failure mode (what went wrong, how to avoid).

Classify confidence:
- high: the review/audit EXPLICITLY stated this correction
- medium: inferred from the pattern of failure, but not stated directly
- low: speculative — might be wrong

If the run was successful (grade A or B) with no corrections needed, extract \
zero lessons. A clean run is not a lesson — it's the goal.

If the run failed before investigation completed, extract zero lessons — \
infrastructure failures are not learning opportunities.

Do NOT extract lessons about:
- Rate limits, timeouts, or budget exceeded (infrastructure, not learning)
- Known pipeline limitations already documented
- Vague observations ("could have been better", "investigation was thorough")

Write each lesson in this exact format, separated by blank lines:

LESSON 1
Title: [short description]
Type: [pipeline_prompt | repo_knowledge | cross_repo]
Confidence: [high | medium | low]
Target Repo: [repo name]
Target File: [which file should change, or "N/A"]
Evidence: [direct quote from audit/review that supports this lesson]
Description: [2-3 sentences: what happened, what should change, why it matters]

LESSON 2
Title: ...
(etc.)

If there are no lessons to extract, write: NO LESSONS

Do not wrap in JSON. Do not add commentary before or after the lessons.
"""

ADVERSARIAL_LESSON_REVIEW_PROMPT = """\
You are an adversarial reviewer for a pipeline-extracted lesson. Your job is to \
determine whether this lesson is worth acting on or whether it should be rejected.

## The Lesson

Title: {lesson_title}
Type: {lesson_type}
Confidence: {lesson_confidence}
Target Repo: {lesson_target_repo}
Target File: {lesson_target_file}
Evidence: {lesson_evidence}
Description: {lesson_description}

## Source Run

- Repo: {repo}
- Issue: #{issue_number}
- Pipeline Outcome: {pipeline_outcome}

## Review Criteria

Evaluate this lesson against ALL of the following criteria. Be harsh — false \
positives waste engineering time and pollute knowledge bases.

1. **Actionability**: Is this specific enough that someone could make a concrete \
change based on it? "Improve investigation" fails. "Add a check for observer \
cleanup patterns when the fix involves event listeners" passes. If the lesson \
cannot be turned into a specific file edit or prompt addition, REJECT.

2. **Evidence grounding**: Is the evidence quote real and does it actually \
support the lesson? If the evidence is vague, paraphrased, or doesn't match \
the lesson's claim, REJECT.

3. **Preventive value**: Would this lesson actually prevent the same class of \
failure in a future run on a DIFFERENT issue? Lessons that are too specific to \
one issue ("always check ShiftObserver.swift") have no preventive value. REJECT.

4. **Harm potential**: Could applying this lesson cause harm? Would it make \
prompts too long, add contradictory constraints, cause agents to over-check \
irrelevant things, or slow down pipeline runs? If yes, REJECT.

5. **Novelty**: Is this lesson already covered by existing pipeline prompts or \
knowledge? If the investigate prompt already says "check peer implementations" \
and this lesson says "check peer implementations for observers," it's a \
duplicate. REJECT duplicates.

## Output Format

Write your analysis naturally. For each criterion, state your assessment in \
1-2 sentences.

End with exactly one of these verdicts on its own line:

VERDICT: ACCEPT
VERDICT: REJECT

If rejecting, add a one-line reason after the verdict:
REASON: [why this lesson should not be applied]
"""

AUDIT_PROMPT = """\
You are the Post-Run Auditor. Evaluate the pipeline run described below. Write your \
analysis naturally — reason through each dimension, then state your conclusions.

The audit is a PIPELINE EVALUATION tool, not a code review tool. It is most valuable \
when things go wrong — failure modes reveal what to fix in prompts, gates, and flow.

## Pipeline Outcome

**{pipeline_outcome}**

{pipeline_outcome_detail}

---

## Run Context

**Issue #{issue_number}**

{issue_body}

---

**Investigation Report**

{investigation_report}

---

**Full Diff**

```diff
{diff}
```

---

**Review Verdict**

{review_text}

---

**Deep Review**

{deep_review_text}

---

**Run Metadata**

- Run ID: {run_id}
- PR: {pr_url}
- Investigate: {investigate_turns} turns, ${investigate_cost:.4f}, {investigate_duration:.0f}s, stop={investigate_finish}
- Implement: {implement_turns} turns, ${implement_cost:.4f}, {implement_duration:.0f}s, stop={implement_finish}
- Review: {review_turns} turns, ${review_cost:.4f}, {review_duration:.0f}s, stop={review_finish}
- Total: ${total_cost:.4f}

---

## Evaluation Dimensions

Score each dimension 1–5. Be harsh. A score of 5 means this phase could serve as \
a training example. A score of 3 means acceptable but flawed. Below 3 means the \
phase produced output that degraded the next phase.

For each dimension, write your reasoning first, then state the score as "D1: N/5" \
(or D2, D3, D4).

If a phase did not run (marked "not available"), score it 1/5 and explain why the \
pipeline exited before reaching it. The failure to reach a phase IS the finding.

### D1: Investigation Quality (1–5)
- Did the report trace symptom → code path → root cause with specific file:line citations?
- Did it start from the user-visible entry point, or did it grep broadly for keywords?
- Did it produce a numbered reproduction chain?
- Did it correctly identify all files that needed changing?
- Penalty: -1 if root cause is stated without a concrete code path. \
  -1 if "Affected Files" includes files the implement phase did not touch. \
  -1 if report is under 600 chars (THIN_REPORT). \
  -1 if report exceeds 18 tool calls worth of investigation (OVER_EXPLORED).

### D2: Implementation Quality (1–5)
- Did the implement phase follow the investigation without re-investigating?
- Are changes minimal and focused to the files listed in the report?
- No debug statements, commented-out code, or unrelated refactors?
- Did it run tests and confirm they pass?
- Penalty: -1 for each file changed that was NOT in the investigation's "Affected Files". \
  -1 if implement turns exceeded investigate turns (RE_INVESTIGATION signal). \
  -1 if diff contains any of: `console.log`, `print(`, `debugger`, `TODO`, `FIXME` in changed lines.

### D3: Fix Correctness (1–5)
- Does the diff actually address the root cause identified in the investigation?
- Are there any obvious regressions (changed code paths with no test coverage)?
- Are edge cases from the issue addressed?
- Is the change complete, or does it fix one symptom while leaving another?
- Penalty: -1 if the review verdict is REQUEST_CHANGES. \
  -1 if the diff modifies a file but leaves the specific bug line unchanged. \
  -1 if the review identifies any regression.

### D4: Process Efficiency (1–5)
- Were turns used effectively, or was there redundant exploration?
- Did the investigate phase stay within 15 turns for straightforward issues?
- Did the implement phase start making changes within the first 3 turns?
- Was total cost under $3.00?
- Penalty: -1 if investigate used >20 turns. \
  -1 if implement used >25 turns without a complexity justification. \
  -1 if total cost exceeded $4.00.

---

## Failure Mode Analysis

If the pipeline did NOT reach PR creation, this section is MANDATORY. Analyze:

1. **Where did the pipeline exit?** Which phase failed or blocked, and why?
2. **Was the exit justified?** Should the gate have caught this, or did it over-react?
3. **Root cause of failure:** What specific weakness in the investigate/implement/review \
   prompt or gate logic caused the pipeline to fail?
4. **What would fix it?** Draft a concrete prompt or gate change (≤3 sentences) that \
   would prevent this failure mode next time.

---

## Error Taxonomy

Scan the run for these error patterns. List every category that applies by name.

| Category | Definition |
|---|---|
| NO_EDITS | Implement phase produced zero file changes |
| WRONG_FILES | Implement changed files not in investigation's Affected Files |
| THIN_REPORT | Investigation report under 600 chars or missing ≥2 required sections |
| DEBUG_LEFTOVERS | Diff contains console.log / print( / debugger / commented-out blocks |
| TYPE_ERROR | Diff introduces an obvious type mismatch or undefined variable |
| TEST_FAILURE | Review or metadata indicates tests failed |
| RE_INVESTIGATION | Implement used grep/find/read on files not in Affected Files before editing |
| INCOMPLETE_FIX | Fix addresses symptom but not root cause (review says REQUEST_CHANGES) |
| OVERCOMPLICATED | Diff modifies >3× the lines needed (can be inferred from diff size vs. fix scope) |
| EARLY_EXIT | Pipeline exited before PR creation (investigation_failed, review_blocked, etc.) |
| BUDGET_EXCEEDED | Pipeline hit budget cap before completing all phases |

---

## Golden Dataset Criteria

A run qualifies as GOLDEN if ALL of the following are true:
1. D1 ≥ 4 (investigation has concrete code path, correct files)
2. D2 ≥ 4 (implementation followed investigation, no artifacts)
3. D3 ≥ 4 (fix is correct, no regressions)
4. D4 ≥ 3 (cost < $3.00, no excessive turns)
5. Review verdict is APPROVE
6. Error categories list is empty
7. Pipeline outcome is "pr_created"

State whether this run is golden. If not, list the specific blockers.

---

## Prompt Improvement Signals

For each dimension scored ≤ 3, identify the specific phase prompt that needs changing \
and what constraint is missing or weak. Be specific: quote the failure behavior, \
state the anti-pattern, and draft the missing constraint in ≤2 sentences.

---

## What to include in your analysis

- For each dimension: your reasoning, then the score as "D1: N/5" (same for D2, D3, D4)
- Error categories found (use the exact category names from the taxonomy)
- Grade: A/B/C/D/F (A = all scores ≥ 4, B = avg ≥ 3.5, C = avg ≥ 2.5, D = avg ≥ 1.5, F = avg < 1.5)
- Whether this run is golden, and if not, what blocks it
- What went right and what went wrong
- Prompt improvement suggestions for low-scoring dimensions
- Failure mode analysis (mandatory if pipeline did not create a PR)

NEVER:
- Invent tool calls — you have no tools. All context is above.
- Assign scores higher than the evidence supports.
"""


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

PHASE_MODELS = {"investigate": "sonnet", "implement": "sonnet", "review": "sonnet", "deep_review": "opus", "audit": "opus", "extract_lessons": "opus", "adversarial_review": "sonnet"}
PHASE_MAX_TURNS = {"investigate": 40, "implement": 30, "review": 25, "deep_review": 50, "audit": 10, "extract_lessons": 10, "adversarial_review": 5}
PHASE_BUDGET = {"investigate": 1.50, "implement": 2.50, "review": 1.50, "deep_review": 3.00, "audit": 3.00, "extract_lessons": 1.50, "adversarial_review": 0.30}
LEARNING_BUDGET = 3.00  # Total cap for the entire learning phase


class BudgetExceeded(RuntimeError):
    pass


def _guard(spent: float, budget: float) -> None:
    if spent > budget:
        raise BudgetExceeded(f"${spent:.2f} > budget ${budget:.2f}")


def _content(resp: dict) -> str:
    """Extract content string from agent response."""
    return resp["content"] if isinstance(resp["content"], str) else json.dumps(resp["content"])


def _start_phase(conn, name: str) -> int:
    return storage.log_phase(conn, name)


def _finish_phase(conn, phase_id: int, resp: dict | None, failed: bool = False) -> None:
    """Log messages and mark phase complete."""
    if resp:
        prompt = resp.get("_prompt", "")
        content = resp.get("content", "")
        if prompt:
            storage.log_message(conn, phase_id, turn_number=1, role="user", content=prompt)
        if content:
            storage.log_message(
                conn, phase_id, turn_number=1, role="assistant",
                content=content if isinstance(content, str) else json.dumps(content),
                tokens_in=resp.get("tokens_in", 0),
                tokens_out=resp.get("tokens_out", 0),
                cost=resp.get("cost", 0),
            )
    storage.finish_phase(
        conn, phase_id,
        status="failed" if failed else "completed",
        cost=resp.get("cost", 0) if resp else 0,
        tokens_in=resp.get("tokens_in", 0) if resp else 0,
        tokens_out=resp.get("tokens_out", 0) if resp else 0,
        cache_read_tokens=resp.get("cache_read_tokens", 0) if resp else 0,
        cache_creation_tokens=resp.get("cache_creation_tokens", 0) if resp else 0,
        session_id=resp.get("session_id", "") if resp else "",
    )


def _resolve_model(phase: str, model_override: str | None, phase_models: dict[str, str]) -> str | None:
    """Return the effective model for a phase, applying priority:
    phase_models[phase] > model_override > PHASE_MODELS default.
    """
    return phase_models.get(phase) or model_override


def _call_agent(prompt: str, *, phase: str, cwd: str, model: str | None = None) -> dict:
    """Route to the correct backend based on model string."""
    effective_model = model or PHASE_MODELS.get(phase, "sonnet")

    if effective_model in ("haiku", "sonnet", "opus") or effective_model.startswith("claude"):
        return _call_claude(prompt, phase=phase, cwd=cwd, model=effective_model)
    else:
        return _call_openai_compat(prompt, phase=phase, cwd=cwd, model=effective_model)


def _call_openai_compat(prompt: str, *, phase: str, cwd: str, model: str) -> dict:
    """Call an OpenAI-compatible backend (Z.ai, OpenRouter) via the SDK runtime."""
    import adapters
    import models as model_configs
    from engines.three_step import runtime

    config = adapters.get_config(model)

    # Load per-model config for sampling, message processing, turn limits
    model_cfg = model_configs.get_model_config(model)

    # Turn limit: model config per-phase override > orchestrator default
    phase_turns = model_cfg.get("max_turns", {})
    max_turns = phase_turns.get(phase) or PHASE_MAX_TURNS.get(phase, 25)

    # OpenRouter adapter prefixes model with "openrouter/" for OpenHands SDK,
    # but our runtime.call_agent hits the OpenAI SDK directly — strip the prefix.
    api_model = config["model"]
    if api_model.startswith("openrouter/"):
        api_model = api_model[len("openrouter/"):]

    return runtime.call_agent(
        prompt,
        model=api_model,
        cwd=cwd,
        max_turns=max_turns,
        api_key=config["api_key"],
        base_url=config["base_url"],
        model_config=model_cfg,
    )


def _call_claude(prompt: str, *, phase: str, cwd: str, model: str | None = None) -> dict:
    """Call claude CLI and adapt the response to the orchestrator's expected dict shape.

    Maps claude_runtime.run_phase_agent() output to the keys that _finish_phase(),
    _content(), and budget tracking depend on:
        result       -> content
        cost_usd     -> cost
        duration_ms  -> duration_s (converted)
        stop_reason  -> finish_reason (mapped)
        tokens_in, tokens_out preserved as-is
    """
    phase_model = model or PHASE_MODELS.get(phase, "sonnet")
    max_turns = PHASE_MAX_TURNS.get(phase, 25)

    raw = claude_runtime.run_phase_agent(
        worktree=cwd,
        prompt=prompt,
        phase=phase,
        max_turns=max_turns,
        model=phase_model,
    )

    # Map stop_reason to finish_reason
    stop = raw.get("stop_reason", "")
    if stop in ("end_turn", ""):
        finish_reason = "end_turn"
    elif stop == "max_turns" or raw.get("hit_turn_limit"):
        finish_reason = "max_iterations"
    elif stop in ("error", "timeout") or raw.get("hit_timeout"):
        finish_reason = "error"
    else:
        finish_reason = stop or "end_turn"

    return {
        "content": raw.get("result", ""),
        "cost": raw.get("cost_usd", 0.0),
        "tokens_in": raw.get("tokens_in", 0),
        "tokens_out": raw.get("tokens_out", 0),
        "cache_read_tokens": raw.get("cache_read", 0),
        "cache_creation_tokens": raw.get("cache_creation", 0),
        "duration_s": raw.get("duration_ms", 0) / 1000.0,
        "finish_reason": finish_reason,
        "session_id": raw.get("session_id", ""),
        "num_turns": raw.get("num_turns", 0),
    }


def _extract_audit_verdict(text: str) -> dict:
    """Extract structured audit data from natural language audit text.

    Looks for dimension scores (D1-D4), error categories, grade, golden status,
    and findings. Lenient — returns None/empty for fields it can't find.
    """
    verdict: dict[str, Any] = {}

    # Extract dimension scores: "D1: 4/5" or "D1: 4" patterns
    scores: dict[str, int] = {}
    score_map = {
        "D1": "investigation_quality",
        "D2": "implementation_quality",
        "D3": "fix_correctness",
        "D4": "process_efficiency",
    }
    for key, field in score_map.items():
        match = re.search(rf'{key}\s*:\s*(\d)\s*/?\s*5?', text)
        if match:
            scores[field] = int(match.group(1))
    verdict["scores"] = scores if scores else None

    # Extract error categories from the verdict/findings section (last 30% of text),
    # not the taxonomy definition table that gets echoed in the prompt.
    # Also require contextual markers (bullet, comma-list, bracket-list) to avoid
    # matching category names that appear only in the reference table.
    error_cats = []
    known_cats = [
        "NO_EDITS", "WRONG_FILES", "THIN_REPORT", "DEBUG_LEFTOVERS",
        "TYPE_ERROR", "TEST_FAILURE", "RE_INVESTIGATION",
        "INCOMPLETE_FIX", "OVERCOMPLICATED", "EARLY_EXIT",
        "BUDGET_EXCEEDED",
    ]
    # Only scan the last 30% of the text where the actual verdict/findings live
    cutoff = len(text) - len(text) // 3
    verdict_section = text[cutoff:]
    for cat in known_cats:
        # Match in context: preceded by bullet/dash/comma/bracket/colon, or
        # appearing as a standalone word in the verdict section
        if re.search(rf'(?:[-*•,\[\s:])\s*{cat}\b', verdict_section):
            error_cats.append(cat)
    verdict["error_categories"] = error_cats

    # Extract overall grade
    grade_match = re.search(r'[Gg]rade\s*:\s*([ABCDF])\b', text)
    if grade_match:
        verdict["overall_grade"] = grade_match.group(1)
    else:
        # Try standalone grade pattern like "Overall: B" or "**B**"
        grade_match2 = re.search(r'[Oo]verall\s*[:\-]\s*([ABCDF])\b', text)
        if grade_match2:
            verdict["overall_grade"] = grade_match2.group(1)
        else:
            verdict["overall_grade"] = None

    # Extract golden status
    golden_lower = text.lower()
    if "is golden" in golden_lower or "qualifies as golden" in golden_lower:
        verdict["is_golden"] = True
    elif "not golden" in golden_lower or "does not qualify" in golden_lower or "is not golden" in golden_lower:
        verdict["is_golden"] = False
    else:
        verdict["is_golden"] = False

    # Extract findings: what went right/wrong (look for bullet points after headers)
    right: list[str] = []
    wrong: list[str] = []
    right_section = re.search(
        r'[Ww]hat went right[:\s]*\n((?:\s*[-*].+\n?)+)', text
    )
    if right_section:
        right = [line.strip().lstrip('-*').strip() for line in right_section.group(1).strip().split('\n') if line.strip()]
    wrong_section = re.search(
        r'[Ww]hat went wrong[:\s]*\n((?:\s*[-*].+\n?)+)', text
    )
    if wrong_section:
        wrong = [line.strip().lstrip('-*').strip() for line in wrong_section.group(1).strip().split('\n') if line.strip()]
    verdict["findings"] = {"what_went_right": right, "what_went_wrong": wrong}

    # Extract prompt improvements
    improvements: list[dict] = []
    improvement_blocks = re.finditer(
        r'(investigate|implement|review)\s*[:\-]\s*(.+?)(?=\n(?:investigate|implement|review)\s*[:\-]|\n##|\Z)',
        text, re.IGNORECASE | re.DOTALL,
    )
    for m in improvement_blocks:
        improvements.append({
            "phase": m.group(1).lower(),
            "suggestion": m.group(2).strip()[:500],
        })
    verdict["prompt_improvements"] = improvements

    return verdict


def _find_repo_path(repo: str) -> str:
    """Auto-resolve a local clone path for 'owner/repo' style repo strings.

    Search order:
    1. Walk up from this file's directory looking for repos/{name}
    2. Check cwd/../repos/{name}
    3. Glob /Users/shanemattner/Desktop/personal-assistant-clones/*/repos/{name}
    """
    name = repo.split("/")[-1] if "/" in repo else repo

    # 1. Walk up from script directory looking for repos/{name}
    script_dir = Path(__file__).resolve().parent
    cursor = script_dir
    for _ in range(10):  # cap to avoid infinite walk
        candidate = cursor / "repos" / name
        if candidate.is_dir() and (candidate / ".git").exists():
            log.info("auto-resolved repo_path: %s (walk-up from script)", candidate)
            return str(candidate)
        parent = cursor.parent
        if parent == cursor:
            break
        cursor = parent

    # 2. Check relative to cwd
    cwd_candidate = Path(os.getcwd()).parent / "repos" / name
    if cwd_candidate.is_dir() and (cwd_candidate / ".git").exists():
        log.info("auto-resolved repo_path: %s (relative to cwd)", cwd_candidate)
        return str(cwd_candidate)

    # 3. Glob PA clone directories
    for match in glob.glob(f"/Users/shanemattner/Desktop/personal-assistant-clones/*/repos/{name}"):
        p = Path(match)
        if p.is_dir() and (p / ".git").exists():
            log.info("auto-resolved repo_path: %s (glob)", p)
            return str(p)

    raise FileNotFoundError(
        f"Could not find local clone of {repo!r} (looked for 'repos/{name}'). "
        "Pass --repo-path explicitly."
    )


def _post_failure(repo: str, num: int, err: str) -> None:
    try:
        source.post_comment(repo, num, f"Pipeline failed.\n\n```\n{err[:2000]}\n```")
    except Exception:
        log.warning("failed to post failure comment on %s#%d", repo, num)


# ---------------------------------------------------------------------------
# Learning phase helpers
# ---------------------------------------------------------------------------


def _parse_lessons_from_text(text: str) -> list[dict]:
    """Parse structured lessons from the extractor's natural language output.

    Expects lessons in the format:
        LESSON N
        Title: ...
        Type: ...
        Confidence: ...
        Target Repo: ...
        Target File: ...
        Evidence: ...
        Description: ...
    """
    if "NO LESSONS" in text.upper():
        return []

    lessons: list[dict] = []
    # Split on LESSON N headers
    blocks = re.split(r'LESSON\s+\d+\s*\n', text, flags=re.IGNORECASE)

    for block in blocks:
        block = block.strip()
        if not block:
            continue

        lesson: dict[str, str] = {}
        fields = {
            "title": r'Title\s*:\s*(.+)',
            "type": r'Type\s*:\s*(.+)',
            "confidence": r'Confidence\s*:\s*(.+)',
            "target_repo": r'Target\s+Repo\s*:\s*(.+)',
            "target_file": r'Target\s+File\s*:\s*(.+)',
            "evidence": r'Evidence\s*:\s*(.+)',
            "description": r'Description\s*:\s*(.+)',
        }
        for key, pattern in fields.items():
            match = re.search(pattern, block, re.IGNORECASE)
            if match:
                lesson[key] = match.group(1).strip()

        # Must have at minimum a title and type to be valid
        if lesson.get("title") and lesson.get("type"):
            # Normalize type
            ltype = lesson.get("type", "").lower().strip()
            if "pipeline" in ltype or "prompt" in ltype:
                lesson["type"] = "pipeline_prompt"
            elif "cross" in ltype:
                lesson["type"] = "cross_repo"
            elif "repo" in ltype:
                lesson["type"] = "repo_knowledge"
            else:
                lesson["type"] = "repo_knowledge"

            # Normalize confidence
            conf = lesson.get("confidence", "medium").lower().strip()
            if "high" in conf:
                lesson["confidence"] = "high"
            elif "low" in conf:
                lesson["confidence"] = "low"
            else:
                lesson["confidence"] = "medium"

            lessons.append(lesson)

    return lessons[:5]  # Cap at 5


def _parse_adversarial_verdict(text: str) -> tuple[str, str]:
    """Parse ACCEPT/REJECT verdict from adversarial review output.

    Returns (verdict, reason).
    """
    verdict = "REJECT"  # Default to reject if parsing fails
    reason = ""

    verdict_match = re.search(r'VERDICT\s*:\s*(ACCEPT|REJECT)', text, re.IGNORECASE)
    if verdict_match:
        verdict = verdict_match.group(1).upper()

    reason_match = re.search(r'REASON\s*:\s*(.+)', text, re.IGNORECASE)
    if reason_match:
        reason = reason_match.group(1).strip()

    return verdict, reason


def _create_lesson_issue(
    repo: str,
    lesson: dict,
    adversarial_text: str,
    run_id: str,
    issue_number: int,
    pipeline_outcome: str,
) -> str | None:
    """Create a GitHub issue for an accepted lesson. Returns the issue URL or None."""
    title = lesson.get("title", "Untitled lesson")
    ltype = lesson.get("type", "unknown")
    confidence = lesson.get("confidence", "medium")
    target_file = lesson.get("target_file", "N/A")
    evidence = lesson.get("evidence", "N/A")
    description = lesson.get("description", "N/A")

    target_repo = repo

    body = f"""## Pipeline Learning: {title}

**Source**: Pipeline run `{run_id}` on #{issue_number} ({pipeline_outcome})
**Type**: {ltype}
**Confidence**: {confidence}
**Target file**: {target_file}

### What happened

{description}

### Evidence

> {evidence}

### Adversarial review

{adversarial_text[:2000]}

---

*Auto-generated by the pipeline learning system. This lesson passed adversarial review.*
"""

    try:
        result = subprocess.run(
            [
                "gh", "issue", "create",
                "--repo", target_repo,
                "--title", f"[pipeline-learning] {title}",
                "--body", body,
                "--label", "auto-generated",
                "--label", "pipeline-learning",
            ],
            capture_output=True, text=True, timeout=30,
        )
        if result.returncode == 0:
            url = result.stdout.strip()
            log.info("[learn] created issue: %s", url)
            return url
        else:
            # Labels might not exist — retry without labels
            log.warning("[learn] issue create failed (labels?), retrying without labels: %s", result.stderr.strip())
            result = subprocess.run(
                [
                    "gh", "issue", "create",
                    "--repo", target_repo,
                    "--title", f"[pipeline-learning] {title}",
                    "--body", body,
                ],
                capture_output=True, text=True, timeout=30,
            )
            if result.returncode == 0:
                url = result.stdout.strip()
                log.info("[learn] created issue (no labels): %s", url)
                return url
            log.warning("[learn] issue create failed: %s", result.stderr.strip())
            return None
    except Exception:
        log.exception("[learn] failed to create issue for lesson: %s", title)
        return None


def _run_learning_phase(
    *,
    conn,
    repo: str,
    issue_number: int,
    run_id: str,
    pipeline_outcome: str,
    pipeline_outcome_detail: str,
    audit_text: str,
    review_text: str,
    deep_review_text: str,
    total_cost: float,
    wt: str,
) -> dict:
    """Extract lessons from the pipeline run, adversarially review each one,
    and create GitHub issues for survivors.

    Returns a summary dict with extracted/accepted/rejected counts.
    """
    learning_spent = 0.0
    summary = {
        "lessons_extracted": 0,
        "lessons_accepted": 0,
        "lessons_rejected": 0,
        "issues_created": [],
        "learning_cost": 0.0,
    }

    # ---- Step 1: Extract lessons with Opus ----
    log.info("[learn] extracting lessons (model=opus)")
    pid = _start_phase(conn, "extract_lessons")

    prompt = EXTRACT_LESSONS_PROMPT.format(
        pipeline_outcome=pipeline_outcome,
        pipeline_outcome_detail=pipeline_outcome_detail,
        audit_text=audit_text,
        review_text=review_text,
        deep_review_text=deep_review_text,
        repo=repo,
        issue_number=issue_number,
        run_id=run_id,
        total_cost=total_cost,
    )

    resp = _call_agent(prompt, phase="extract_lessons", cwd=wt, model="opus")
    resp["_prompt"] = prompt
    learning_spent += resp.get("cost", 0.0)
    _finish_phase(conn, pid, resp, failed=(resp.get("finish_reason") == "error"))

    extract_text = _content(resp)
    lessons = _parse_lessons_from_text(extract_text)
    summary["lessons_extracted"] = len(lessons)

    storage.log_event(conn, "lessons_raw_text", {"text": extract_text})
    storage.log_event(conn, "lessons_extracted", {
        "count": len(lessons),
        "items": lessons,
    })

    log.info("[learn] extracted %d lessons", len(lessons))

    if not lessons:
        summary["learning_cost"] = learning_spent
        return summary

    # ---- Step 2: Adversarial review each lesson with Sonnet ----
    accepted: list[dict] = []
    rejected: list[dict] = []

    for i, lesson in enumerate(lessons):
        if learning_spent >= LEARNING_BUDGET:
            log.warning("[learn] learning budget exhausted ($%.2f >= $%.2f), skipping remaining lessons",
                        learning_spent, LEARNING_BUDGET)
            break

        log.info("[learn] adversarial review %d/%d: %s", i + 1, len(lessons), lesson.get("title", "?"))
        pid = _start_phase(conn, f"adversarial_review_{i + 1}")

        review_prompt = ADVERSARIAL_LESSON_REVIEW_PROMPT.format(
            lesson_title=lesson.get("title", ""),
            lesson_type=lesson.get("type", ""),
            lesson_confidence=lesson.get("confidence", ""),
            lesson_target_repo=lesson.get("target_repo", ""),
            lesson_target_file=lesson.get("target_file", "N/A"),
            lesson_evidence=lesson.get("evidence", ""),
            lesson_description=lesson.get("description", ""),
            repo=repo,
            issue_number=issue_number,
            pipeline_outcome=pipeline_outcome,
        )

        adv_resp = _call_agent(review_prompt, phase="adversarial_review", cwd=wt, model="sonnet")
        adv_resp["_prompt"] = review_prompt
        learning_spent += adv_resp.get("cost", 0.0)
        _finish_phase(conn, pid, adv_resp, failed=(adv_resp.get("finish_reason") == "error"))

        adv_text = _content(adv_resp)
        verdict, reason = _parse_adversarial_verdict(adv_text)

        storage.log_event(conn, f"adversarial_review_{i + 1}", {
            "lesson_title": lesson.get("title"),
            "verdict": verdict,
            "reason": reason,
            "text": adv_text[:3000],
        })

        if verdict == "ACCEPT":
            log.info("[learn] ACCEPTED: %s", lesson.get("title"))
            lesson["adversarial_text"] = adv_text
            accepted.append(lesson)
        else:
            log.info("[learn] REJECTED: %s — %s", lesson.get("title"), reason)
            rejected.append({"lesson": lesson, "reason": reason})

    summary["lessons_accepted"] = len(accepted)
    summary["lessons_rejected"] = len(rejected)

    # ---- Step 3: Create GitHub issues for accepted lessons ----
    for lesson in accepted:
        if learning_spent >= LEARNING_BUDGET:
            break

        url = _create_lesson_issue(
            repo=repo,
            lesson=lesson,
            adversarial_text=lesson.get("adversarial_text", ""),
            run_id=run_id,
            issue_number=issue_number,
            pipeline_outcome=pipeline_outcome,
        )
        if url:
            summary["issues_created"].append(url)

    storage.log_event(conn, "lessons_applied", {
        "accepted": len(accepted),
        "rejected": len(rejected),
        "issues_created": summary["issues_created"],
    })

    # ---- Step 4: Post summary comment on the original issue ----
    try:
        comment_lines = [
            f"**Pipeline Learning — Run `{run_id}`**",
            "",
            f"- Lessons extracted: {len(lessons)}",
            f"- Accepted (passed adversarial review): {len(accepted)}",
            f"- Rejected: {len(rejected)}",
        ]
        if accepted:
            comment_lines.append("")
            comment_lines.append("**Accepted lessons:**")
            for lesson in accepted:
                comment_lines.append(f"- {lesson.get('title', '?')} ({lesson.get('type', '?')}, {lesson.get('confidence', '?')})")
        if summary["issues_created"]:
            comment_lines.append("")
            comment_lines.append("**Issues created:**")
            for url in summary["issues_created"]:
                comment_lines.append(f"- {url}")
        if rejected:
            comment_lines.append("")
            comment_lines.append("**Rejected lessons:**")
            for r in rejected:
                comment_lines.append(f"- ~~{r['lesson'].get('title', '?')}~~ — {r.get('reason', 'no reason')}")

        source.post_comment(repo, issue_number, "\n".join(comment_lines))
    except Exception:
        log.warning("[learn] failed to post learning summary comment on %s#%d", repo, issue_number)

    summary["learning_cost"] = learning_spent
    log.info("[learn] done — extracted=%d accepted=%d rejected=%d issues=%d cost=$%.2f",
             len(lessons), len(accepted), len(rejected), len(summary["issues_created"]), learning_spent)

    return summary


# ---------------------------------------------------------------------------
# Main pipeline
# ---------------------------------------------------------------------------

def run_pipeline(
    repo: str,
    issue_number: int,
    *,
    budget: float = 5.00,
    model_override: str | None = None,
    repo_path: str | None = None,
    review_retries: int = 1,
    phase_models: dict[str, str] | None = None,
) -> dict:
    """Run the 3-step pipeline: investigate -> implement -> review+PR.

    Returns dict with keys: status, pr_url, spent_usd, run_id, error.

    Model priority per phase:
      phase_models[phase] > model_override > PHASE_MODELS default.
    """
    phase_models = phase_models or {}
    model = model_override or "sonnet"

    # Fetch issue
    issue = source.fetch_issue(repo, issue_number)
    issue_body = f"# {issue['title']}\n\n{issue['body']}"

    # Set up storage
    db_path, conn = storage.create_run_db(repo, issue_number, model=model)
    run_id = db_path.stem if hasattr(db_path, "stem") else str(db_path)
    # db_path is a string from storage module, extract run_id from filename
    run_id = os.path.splitext(os.path.basename(db_path))[0]

    # Check for existing PR on this issue before doing any work
    try:
        pr_check = subprocess.run(
            ["gh", "pr", "list", "--repo", repo, "--search", f"issue {issue_number}", "--state", "open", "--json", "number,title,url"],
            capture_output=True, text=True, timeout=15,
        )
        if pr_check.returncode == 0 and pr_check.stdout.strip() not in ("", "[]"):
            existing_prs = json.loads(pr_check.stdout)
            if existing_prs:
                urls = ", ".join(p.get("url", "") for p in existing_prs)
                log.warning("[pre-check] open PR(s) already exist for issue #%d: %s — aborting", issue_number, urls)
                conn.close()
                return {"status": "skipped", "error": f"Open PR already exists: {urls}", "spent_usd": 0.0, "run_id": run_id}
    except Exception:
        log.warning("[pre-check] failed to check for existing PRs, continuing anyway")

    # Set up workspace
    run_start = datetime.now()
    branch = f"three-step/issue-{issue_number}-{run_start.strftime('%m%d-%H%M')}"
    if not repo_path:
        repo_path = _find_repo_path(repo)
    wt = workspace.create_workspace(repo_path, branch)
    # Log workspace creation for lifecycle traceability
    try:
        base_commit = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=wt, capture_output=True, text=True, timeout=10,
        ).stdout.strip()
    except Exception:
        base_commit = ""
    storage.log_event(conn, "worktree_created", {
        "path": wt,
        "branch": branch,
        "base_commit": base_commit,
    })
    spent = 0.0

    # NOTE: CLAUDE.md hiding is handled per-phase by claude_runtime.py
    # (hide before each `claude` call, restore after). Do NOT hide at the
    # orchestrator level — that persists across phases and gets committed
    # by `git add -A` in the implement phase.
    claude_md_path = os.path.join(wt, "CLAUDE.md")
    claude_md_hidden = os.path.join(wt, "CLAUDE.md.pipeline-hidden")
    claude_dir = os.path.join(wt, ".claude")
    claude_dir_hidden = os.path.join(wt, ".claude.pipeline-hidden")

    current_phase = "init"
    phase_stats: dict[str, dict] = {}

    # Accumulated context for audit and learning — populated as phases complete
    investigation_report = "(not available — investigation did not run)"
    diff = "(not available — no diff produced)"
    review_text = "(not available — review did not run)"
    deep_review_text = "(not available — deep review did not run)"
    pr_url = "(no PR created)"
    pipeline_outcome = "unknown_error"
    pipeline_outcome_detail = ""
    pipeline_result: dict[str, Any] = {}
    audit_verdict: dict | None = None
    audit_raw = "(not available — audit did not run)"

    _default_phase_stat = {"turns": 0, "cost": 0.0, "duration": 0.0, "finish": "(not run)"}

    try:
        # ---- Phase 1: Investigate ----
        current_phase = "investigate"
        log.info("[investigate] starting (max_turns=%d, model=%s)",
                 PHASE_MAX_TURNS["investigate"],
                 _resolve_model("investigate", model_override, phase_models) or PHASE_MODELS["investigate"])
        pid = _start_phase(conn, "investigate")
        prompt = INVESTIGATE_PROMPT.format(
            issue_number=issue_number, issue_body=issue_body,
        )
        resp = _call_agent(prompt, phase="investigate", cwd=wt,
                          model=_resolve_model("investigate", model_override, phase_models))
        resp["_prompt"] = prompt
        spent += resp["cost"]
        _finish_phase(conn, pid, resp,
                      failed=(resp.get("finish_reason") == "error"))
        _guard(spent, budget)

        phase_stats["investigate"] = {
            "turns": resp.get("num_turns", 0),
            "cost": resp.get("cost", 0.0),
            "duration": resp.get("duration_s", 0.0),
            "finish": resp.get("finish_reason", ""),
        }

        phase_cost = resp["cost"]
        if phase_cost > PHASE_BUDGET["investigate"]:
            log.warning("[investigate] phase cost $%.2f exceeded cap $%.2f", phase_cost, PHASE_BUDGET["investigate"])

        investigation_report = _content(resp)
        log.info("[investigate] done (%.1fs, $%.4f, %d chars)",
                 resp["duration_s"], resp["cost"], len(investigation_report))

        log.info("[investigate] report quality: %d chars, %d sections found",
                 len(investigation_report),
                 investigation_report.count("**"))
        if len(investigation_report) < 500:
            log.warning("[investigate] thin report (%d chars) — implement phase may re-investigate", len(investigation_report))
            pipeline_outcome = "investigation_failed"
            pipeline_outcome_detail = (
                f"Investigation report too thin ({len(investigation_report)} chars). "
                "Required: >500 chars with Root Cause and Affected Files sections. "
                "Pipeline aborted to avoid wasting $3-5 on implement+review that will fail."
            )
            raise RuntimeError(pipeline_outcome_detail)

        # Validate required sections exist
        report_lower = investigation_report.lower()
        missing_sections = []
        if "root cause" not in report_lower:
            missing_sections.append("Root Cause")
        if "affected files" not in report_lower:
            missing_sections.append("Affected Files")
        if missing_sections:
            pipeline_outcome = "investigation_failed"
            pipeline_outcome_detail = (
                f"Investigation report missing required sections: {missing_sections}. "
                "Report must contain 'Root Cause' and 'Affected Files' sections."
            )
            raise RuntimeError(pipeline_outcome_detail)

        if resp.get("finish_reason") == "error" and len(investigation_report) < 200:
            pipeline_outcome = "investigation_failed"
            pipeline_outcome_detail = f"Investigate phase errored with thin output: {investigation_report[:500]}"
            raise RuntimeError(f"Investigate phase failed: {investigation_report[:500]}")
        if resp.get("finish_reason") in ("error", "max_iterations"):
            log.warning("[investigate] finished with %s but has %d chars of content — continuing",
                        resp.get("finish_reason"), len(investigation_report))

        # ---- Phase 2: Implement ----
        current_phase = "implement"
        log.info("[implement] starting (max_turns=%d, model=%s)",
                 PHASE_MAX_TURNS["implement"],
                 _resolve_model("implement", model_override, phase_models) or PHASE_MODELS["implement"])
        pid = _start_phase(conn, "implement")
        prompt = IMPLEMENT_PROMPT.format(
            issue_number=issue_number,
            issue_body=issue_body,
            investigation_report=investigation_report,
        )
        resp = _call_agent(prompt, phase="implement", cwd=wt,
                          model=_resolve_model("implement", model_override, phase_models))
        resp["_prompt"] = prompt
        spent += resp["cost"]
        phase_stats["implement"] = {
            "turns": resp.get("num_turns", 0),
            "cost": resp.get("cost", 0.0),
            "duration": resp.get("duration_s", 0.0),
            "finish": resp.get("finish_reason", ""),
        }
        phase_cost = resp["cost"]
        if phase_cost > PHASE_BUDGET["implement"]:
            log.warning("[implement] phase cost $%.2f exceeded cap $%.2f", phase_cost, PHASE_BUDGET["implement"])
        _finish_phase(conn, pid, resp,
                      failed=(resp.get("finish_reason") == "error"))
        _guard(spent, budget)

        log.info("[implement] done (%.1fs, $%.4f)",
                 resp["duration_s"], resp["cost"])

        # Safety-net commit: catch changes the agent failed to commit
        porcelain = subprocess.run(
            ["git", "status", "--porcelain"], cwd=wt,
            capture_output=True, text=True,
        ).stdout.strip()
        commits = subprocess.run(
            ["git", "log", "origin/main..HEAD", "--oneline"], cwd=wt,
            capture_output=True, text=True,
        ).stdout.strip()

        if porcelain:
            log.warning("implement phase left uncommitted changes -- safety-net commit")
            subprocess.run(["git", "add", "-A"], cwd=wt, check=True)
            subprocess.run(
                ["git", "commit", "-m", f"fix: resolve #{issue_number}"],
                cwd=wt, check=True,
            )
            storage.log_event(conn, "safety_net_commit", {"files": porcelain})
        elif not commits:
            pipeline_outcome = "implement_failed"
            pipeline_outcome_detail = (
                "Implement phase produced no changes — "
                "no commits on branch and no uncommitted files."
            )
            raise RuntimeError(
                "Implement phase produced no changes -- "
                "no commits on branch and no uncommitted files"
            )

        # ---- Phase 3: Review + PR ----
        current_phase = "review"
        diff = workspace.get_diff(wt)[:50_000]
        log.info("[review] starting (max_turns=%d, model=%s)",
                 PHASE_MAX_TURNS["review"],
                 _resolve_model("review", model_override, phase_models) or PHASE_MODELS["review"])
        pid = _start_phase(conn, "review")
        prompt = REVIEW_PROMPT.format(
            issue_number=issue_number,
            issue_body=issue_body,
            investigation_report=investigation_report,
            diff=diff,
        )
        resp = _call_agent(prompt, phase="review", cwd=wt,
                          model=_resolve_model("review", model_override, phase_models))
        resp["_prompt"] = prompt
        spent += resp["cost"]
        phase_stats["review"] = {
            "turns": resp.get("num_turns", 0),
            "cost": resp.get("cost", 0.0),
            "duration": resp.get("duration_s", 0.0),
            "finish": resp.get("finish_reason", ""),
        }
        phase_cost = resp["cost"]
        if phase_cost > PHASE_BUDGET["review"]:
            log.warning("[review] phase cost $%.2f exceeded cap $%.2f", phase_cost, PHASE_BUDGET["review"])
        _finish_phase(conn, pid, resp,
                      failed=(resp.get("finish_reason") == "error"))

        review_text = _content(resp)
        log.info("[review] done (%.1fs, $%.4f)", resp["duration_s"], resp["cost"])

        # ---- Review gate: check verdict, retry if REQUEST_CHANGES ----
        review_attempt = 0
        while "REQUEST_CHANGES" in review_text:
            review_attempt += 1
            if review_attempt > review_retries:
                # Exhausted retries — block PR creation
                log.warning("[review] verdict is REQUEST_CHANGES after %d attempt(s) — blocking PR creation",
                            review_attempt)
                storage.log_event(conn, "review_blocked", {
                    "text": review_text,
                    "attempts": review_attempt,
                })
                try:
                    source.post_comment(repo, issue_number,
                        f"**Pipeline review blocked PR creation ({review_attempt} attempt(s)).**\n\n{review_text[:3000]}")
                except Exception:
                    log.warning("failed to post review_blocked comment on %s#%d", repo, issue_number)
                pipeline_outcome = "review_blocked"
                pipeline_outcome_detail = (
                    f"Review phase returned REQUEST_CHANGES verdict after {review_attempt} attempt(s). "
                    "PR was NOT created. The review identified problems that need fixing before the code ships."
                )
                storage.finish_run(conn, "review_blocked", total_cost=spent, branch=branch)
                pipeline_result = {
                    "status": "review_blocked",
                    "review_text": review_text,
                    "spent_usd": spent,
                    "run_id": run_id,
                }
                # fall through to audit in finally block
                return pipeline_result

            # ---- Retry: feed review findings back into a new implement attempt ----
            log.info("[review] REQUEST_CHANGES on attempt %d — retrying implement (max retries: %d)",
                     review_attempt, review_retries)
            storage.log_event(conn, "review_retry", {
                "attempt": review_attempt,
                "review_findings": review_text[:5000],
            })

            rejected_diff = diff

            # Re-implement with review feedback
            current_phase = f"implement_retry_{review_attempt}"
            log.info("[implement_retry_%d] starting", review_attempt)
            pid = _start_phase(conn, f"implement_retry_{review_attempt}")
            retry_prompt = IMPLEMENT_RETRY_PROMPT.format(
                issue_number=issue_number,
                issue_body=issue_body,
                investigation_report=investigation_report,
                review_findings=review_text,
                rejected_diff=rejected_diff,
            )
            resp = _call_agent(retry_prompt, phase="implement", cwd=wt,
                              model=_resolve_model("implement", model_override, phase_models))
            resp["_prompt"] = retry_prompt
            spent += resp["cost"]
            phase_stats[f"implement_retry_{review_attempt}"] = {
                "turns": resp.get("num_turns", 0),
                "cost": resp.get("cost", 0.0),
                "duration": resp.get("duration_s", 0.0),
                "finish": resp.get("finish_reason", ""),
            }
            _finish_phase(conn, pid, resp,
                          failed=(resp.get("finish_reason") == "error"))
            _guard(spent, budget)
            log.info("[implement_retry_%d] done (%.1fs, $%.4f)",
                     review_attempt, resp["duration_s"], resp["cost"])

            # Safety-net commit for retry
            porcelain = subprocess.run(
                ["git", "status", "--porcelain"], cwd=wt,
                capture_output=True, text=True,
            ).stdout.strip()
            if porcelain:
                log.warning("implement_retry_%d left uncommitted changes -- safety-net commit",
                            review_attempt)
                subprocess.run(["git", "add", "-A"], cwd=wt, check=True)
                subprocess.run(
                    ["git", "commit", "-m",
                     f"fix: address review feedback for #{issue_number} (retry {review_attempt})"],
                    cwd=wt, check=True,
                )
                storage.log_event(conn, "safety_net_commit_retry", {
                    "attempt": review_attempt, "files": porcelain,
                })

            # Re-review the updated diff
            current_phase = f"review_retry_{review_attempt}"
            diff = workspace.get_diff(wt)[:50_000]
            log.info("[review_retry_%d] starting", review_attempt)
            pid = _start_phase(conn, f"review_retry_{review_attempt}")
            prompt = REVIEW_PROMPT.format(
                issue_number=issue_number,
                issue_body=issue_body,
                investigation_report=investigation_report,
                diff=diff,
            )
            resp = _call_agent(prompt, phase="review", cwd=wt,
                              model=_resolve_model("review", model_override, phase_models))
            resp["_prompt"] = prompt
            spent += resp["cost"]
            phase_stats[f"review_retry_{review_attempt}"] = {
                "turns": resp.get("num_turns", 0),
                "cost": resp.get("cost", 0.0),
                "duration": resp.get("duration_s", 0.0),
                "finish": resp.get("finish_reason", ""),
            }
            _finish_phase(conn, pid, resp,
                          failed=(resp.get("finish_reason") == "error"))

            review_text = _content(resp)
            log.info("[review_retry_%d] done (%.1fs, $%.4f)",
                     review_attempt, resp["duration_s"], resp["cost"])
            # Loop back to check if this review also says REQUEST_CHANGES

        # Push and create PR
        destination.push_branch(wt, branch)
        body = destination.format_pr_body(issue_number, review_text, db_path, [])
        pr = destination.create_pr(repo, branch, f"fix: resolve #{issue_number}", body)
        pr_url = pr["url"]

        # ---- Phase 4: Deep Review (Opus) — adversarial review after PR ----
        try:
            current_phase = "deep_review"
            log.info("[deep_review] starting (max_turns=%d, model=%s)",
                     PHASE_MAX_TURNS["deep_review"], PHASE_MODELS["deep_review"])
            pid = _start_phase(conn, "deep_review")
            prompt = DEEP_REVIEW_PROMPT.format(
                issue_number=issue_number,
                issue_body=issue_body,
                investigation_report=investigation_report,
                diff=diff,
                review_text=review_text,
                pr_url=pr["url"],
            )
            dr_resp = _call_agent(prompt, phase="deep_review", cwd=wt)
            dr_resp["_prompt"] = prompt
            spent += dr_resp["cost"]
            phase_stats["deep_review"] = {
                "turns": dr_resp.get("num_turns", 0),
                "cost": dr_resp.get("cost", 0.0),
                "duration": dr_resp.get("duration_s", 0.0),
                "finish": dr_resp.get("finish_reason", ""),
            }
            _finish_phase(conn, pid, dr_resp,
                          failed=(dr_resp.get("finish_reason") == "error"))

            deep_review_text = _content(dr_resp)
            storage.log_event(conn, "deep_review_text", {"text": deep_review_text})
            log.info("[deep_review] done (%.1fs, $%.4f)", dr_resp["duration_s"], dr_resp["cost"])

            # If deep review blocks, post corrections on both issue and PR
            if "BLOCK" in deep_review_text or "CONCERNS" in deep_review_text:
                dr_verdict = "BLOCK" if "BLOCK" in deep_review_text else "CONCERNS"
                log.warning("[deep_review] verdict is %s", dr_verdict)
                try:
                    comment = f"**Deep review verdict: {dr_verdict}**\n\n{deep_review_text[:3000]}"
                    source.post_comment(repo, issue_number, comment)
                    source.post_comment(repo, pr["number"], comment)
                except Exception:
                    log.warning("failed to post deep_review comment")

            _guard(spent, budget)
        except BudgetExceeded:
            log.warning("[deep_review] budget exceeded after PR creation — skipping")
            storage.log_event(conn, "deep_review_skipped", {"reason": "budget_exceeded"})
        except Exception:
            log.exception("[deep_review] deep review phase failed (non-fatal — PR already created)")
            storage.log_event(conn, "deep_review_error", {"error": "deep review phase exception"})

        pipeline_outcome = "pr_created"
        pipeline_outcome_detail = (
            f"Pipeline completed successfully. PR created at {pr_url}. "
            "All phases ran: investigate, implement, review, deep_review."
        )
        storage.finish_run(conn, "ok", total_cost=spent, branch=branch)
        pipeline_result = {
            "status": "ok",
            "pr_url": pr["url"],
            "spent_usd": spent,
            "run_id": run_id,
        }
        return pipeline_result

    except BudgetExceeded as e:
        log.error("budget exceeded: %s", e)
        if pipeline_outcome == "unknown_error":
            pipeline_outcome = "budget_exceeded"
            pipeline_outcome_detail = f"Budget exceeded during {current_phase}: {e}"
        storage.finish_run(conn, "budget_exceeded", total_cost=spent)
        _post_failure(repo, issue_number, str(e))
        pipeline_result = {"status": "error", "error": str(e), "spent_usd": spent, "run_id": run_id}
        return pipeline_result

    except Exception as e:
        log.exception("pipeline failed during phase '%s' (spent $%.2f so far)", current_phase, spent)
        error_detail = f"[{current_phase}] {e} (spent ${spent:.2f})"
        if pipeline_outcome == "unknown_error":
            pipeline_outcome = f"{current_phase}_failed"
            pipeline_outcome_detail = f"Pipeline failed during {current_phase}: {e}"
        storage.finish_run(conn, "error", total_cost=spent)
        storage.log_event(conn, "pipeline_error", {
            "error": str(e),
            "phase": current_phase,
            "spent_usd": spent,
        })
        _post_failure(repo, issue_number, error_detail)
        pipeline_result = {"status": "error", "error": error_detail, "spent_usd": spent, "run_id": run_id}
        return pipeline_result

    finally:
        # ---- Post-Run Audit (Opus) — ALWAYS runs, even on failure ----
        try:
            current_phase = "audit"
            log.info("[audit] starting (pipeline_outcome=%s)", pipeline_outcome)
            pid = _start_phase(conn, "audit")
            prompt = AUDIT_PROMPT.format(
                issue_number=issue_number,
                issue_body=issue_body,
                investigation_report=investigation_report,
                diff=diff,
                review_text=review_text,
                deep_review_text=deep_review_text,
                pr_url=pr_url,
                run_id=run_id,
                pipeline_outcome=pipeline_outcome,
                pipeline_outcome_detail=pipeline_outcome_detail,
                investigate_turns=phase_stats.get("investigate", _default_phase_stat)["turns"],
                investigate_cost=phase_stats.get("investigate", _default_phase_stat)["cost"],
                investigate_duration=phase_stats.get("investigate", _default_phase_stat)["duration"],
                investigate_finish=phase_stats.get("investigate", _default_phase_stat)["finish"],
                implement_turns=phase_stats.get("implement", _default_phase_stat)["turns"],
                implement_cost=phase_stats.get("implement", _default_phase_stat)["cost"],
                implement_duration=phase_stats.get("implement", _default_phase_stat)["duration"],
                implement_finish=phase_stats.get("implement", _default_phase_stat)["finish"],
                review_turns=phase_stats.get("review", _default_phase_stat)["turns"],
                review_cost=phase_stats.get("review", _default_phase_stat)["cost"],
                review_duration=phase_stats.get("review", _default_phase_stat)["duration"],
                review_finish=phase_stats.get("review", _default_phase_stat)["finish"],
                total_cost=spent,
            )
            audit_resp = _call_agent(prompt, phase="audit", cwd=wt, model="opus")
            audit_resp["_prompt"] = prompt
            spent += audit_resp["cost"]
            _finish_phase(conn, pid, audit_resp, failed=(audit_resp.get("finish_reason") == "error"))

            audit_raw = _content(audit_resp)
            storage.log_event(conn, "audit_text", {"text": audit_raw})

            audit_verdict = _extract_audit_verdict(audit_raw)
            storage.log_event(conn, "audit_verdict", audit_verdict)
            log.info("[audit] grade=%s golden=%s errors=%s",
                     audit_verdict.get("overall_grade"),
                     audit_verdict.get("is_golden"),
                     audit_verdict.get("error_categories"))

            # Attach audit results to the pipeline result
            pipeline_result["audit_grade"] = audit_verdict.get("overall_grade")
            pipeline_result["is_golden"] = audit_verdict.get("is_golden", False)

        except Exception:
            log.exception("[audit] audit phase failed (non-fatal)")
            storage.log_event(conn, "audit_error", {"error": "audit phase exception"})

        # Post run metadata to the GitHub issue
        try:
            status = pipeline_result.get("status", pipeline_outcome)
            comment_pr = pipeline_result.get("pr_url", pr_url)
            comment_lines = [
                "**Pipeline run completed**",
                f"- Run: `{run_id}`",
                f"- Branch: `{branch}`",
                f"- Outcome: {pipeline_outcome}",
                f"- Cost: ${spent:.2f}",
            ]
            if comment_pr and comment_pr != "(no PR created)":
                comment_lines.append(f"- PR: {comment_pr}")
            else:
                comment_lines.append("- PR: not created")
            source.post_comment(repo, issue_number, "\n".join(comment_lines))
        except Exception:
            log.warning("failed to post run metadata comment on %s#%d", repo, issue_number)

        # ---- Learning Phase — extract lessons, adversarial review, create issues ----
        try:
            current_phase = "learn"
            log.info("[learn] starting learning phase")
            learning_summary = _run_learning_phase(
                conn=conn,
                repo=repo,
                issue_number=issue_number,
                run_id=run_id,
                pipeline_outcome=pipeline_outcome,
                pipeline_outcome_detail=pipeline_outcome_detail,
                audit_text=audit_raw,
                review_text=review_text,
                deep_review_text=deep_review_text,
                total_cost=spent,
                wt=wt,
            )
            spent += learning_summary.get("learning_cost", 0.0)
            pipeline_result["learning"] = learning_summary
            log.info("[learn] complete — %s", learning_summary)
        except Exception:
            log.exception("[learn] learning phase failed (non-fatal)")
            storage.log_event(conn, "learning_error", {"error": "learning phase exception"})

        # Restore hidden CLAUDE.md
        if os.path.exists(claude_md_hidden):
            os.rename(claude_md_hidden, claude_md_path)
            log.info("[cleanup] restored CLAUDE.md.pipeline-hidden → CLAUDE.md")

        # Restore hidden .claude/
        if os.path.exists(claude_dir_hidden):
            os.rename(claude_dir_hidden, claude_dir)
            log.info("[cleanup] restored .claude.pipeline-hidden/ → .claude/")

        # Log worktree final state (preserved, not cleaned up)
        wt_exists = os.path.exists(wt)
        storage.log_event(conn, "worktree_preserved", {
            "path": wt,
            "branch": branch,
            "exists": wt_exists,
        })
        log.info("worktree preserved at: %s", wt)
        conn.close()
