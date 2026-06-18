# Size-gate (split detector) rules — v1

Decided by Shane 2026-06-10. Runs after claim extraction, before the verify
fan-out. Sonnet stage (judgment work).

## Hard rules

1. **Claim count**: extracted verifiable claims > 5 → FLAG for split review;
   > 10 → MUST SPLIT before verification or build. No exceptions for
   "coherent" tickets — coherence is what the child-issue dependency order
   expresses.
2. **Workstream-type limit = 1**: a ticket contains exactly ONE type of work
   (backend / frontend-UI / schema / infra / docs / test-harness counts as
   the type of its parent feature). Two or more types → MUST SPLIT,
   regardless of size.
3. **Acceptance gates**: more than one independently shippable done-condition
   → SPLIT (one gate per ticket).
4. **Next.js server actions are frontend-UI, not backend.** Files under
   apps/*/app/actions/ using 'use server' are part of the frontend delivery
   even though they run on the server. They are invoked by React components
   and cannot be tested or shipped independently. Only count them as
   "backend" if they expose a standalone API endpoint (e.g. route.ts).
5. **Epic detection**: "Phase N" headers, checkbox lists spanning
   subsystems, tracking language → classify EPIC: never verify or build it;
   children link back to it.
6. **Machine-checkable gates required**: every child's acceptance gate MUST
   include at least one machine-checkable condition — a passing E2E spec, a
   passing unit test, a CI check, or a successful build. "Manual QA confirms"
   is permitted only alongside a machine-checkable condition, never as the
   sole gate. A child whose drafted gate contains only manual-QA language
   must have a spec or CI assertion added, or the parent escalates to EPIC.
7. **Named-bug accountability**: every explicitly named bug in the issue body
   (tagged P0/P1/P2, or described with observed symptoms as "broken",
   "failing", or "stuck") MUST appear as either (a) an extracted claim, or
   (b) a gate condition in a child issue. Relegating a named bug to a
   parenthetical note ("check and fold in…") is not sufficient. If deferred,
   it requires a dedicated follow-on child with its own hard gate before the
   parent is closed.
8. **Conditional work_type**: if a child's scope contains conditional
   language implying backend or database work that may or may not be needed
   ("if the action already returns X", "if the data is not available",
   "lightweight addition to the server action", "may require a new field"),
   the child MUST either (a) be retyped to the broader work_type that covers
   both sides of the conditional, or (b) produce a separate prerequisite
   child for the conditional backend work. A child may not carry a
   conditional that silently changes its own work type.
9. **Max split depth = 1**: children of a split do not re-split. If a child
   issue was created by a prior split (indicated by "Part of #NNN" in the
   issue body), sizegate MUST return PROCEED regardless of claim count or
   gate count. If the child genuinely exceeds hard thresholds (claims > 10
   or 2+ work types), escalate the PARENT to EPIC instead of producing
   grandchildren.

## Split sizing — balance focus against PR count (Shane, 2026-06-10)

Splitting lowers per-PR risk but multiplies review overhead. Apply IN ORDER:

1. **A child must be independently valuable and revertible.** A migration
   alone is not a shippable unit — SCHEMA FOLDS INTO the backend ticket
   that consumes it. Same for a config flag, a type, an email template:
   they ride with their consumer.
2. **Default split for a full-stack feature is 2 children**: backend
   (schema + config + actions + its test gate) and frontend-UI (pages +
   components + its test gate). UI depends on backend.
3. **Cap at 3 children per parent.** If the rules above would produce more,
   the PARENT is mis-scoped — send it back as EPIC instead of emitting a
   4-way split.
4. Only split further when a child STILL violates the hard thresholds
   (claims > 10 or two work types) after rules 1–2.

## Output

PROCEED | SPLIT | EPIC. On SPLIT: drafted child issues (title, one-line
scope, single acceptance gate, work type, dependency order) in terse
engineering voice. The pipeline AUTO-POSTS children and a linking comment
on the parent (Shane, 2026-06-10) — bad splits are corrected after the
fact via record-feedback.sh, not gated up front. Issue text never mentions
pipelines/stages/runs/agents; Claude attribution itself is fine.

## Calibration set

Provenance matters: only Shane-confirmed verdicts are ground truth. LLM-
proposed verdicts measure CONSISTENCY (does the gate apply the rules the
same way twice), not correctness, until Shane confirms or corrects them
via record-feedback.sh.

- shftty#733 as filed: SPLIT, 2 children — backend (schema folded in) +
  frontend-UI. **Shane-confirmed** (2026-06-10 feedback: 3-way split
  over-fragments; schema folds into backend).
- shftty#638: EPIC. *LLM-proposed, unconfirmed.*
- shftty#714: SPLIT (investigation / issue surgery / breach procedure —
  three gates). *LLM-proposed, unconfirmed.*
- shftty#535 as filed: PROCEED (one gate, one work type). *LLM-proposed,
  unconfirmed.*
