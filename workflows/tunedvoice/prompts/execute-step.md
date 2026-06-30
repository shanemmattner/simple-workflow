You are implementing step {step_number} of {total_steps} for issue #{issue_number}.

**This step:** {step_title}
**Target files:** {step_files}
**What to change:** {step_changes}
**How to verify:** {step_verify}

---

## Context from prior steps

{prior_steps}

---

## Rules

1. Focus ONLY on this step. Do not implement future steps.
2. Do not refactor code unrelated to this step.
3. Follow the conventions in the target files — read them first.
4. If a file listed above does not exist, create it following the nearest sibling pattern.
5. Run the verification command when done.
6. Do NOT commit — the orchestrator handles commits.

## Repo context

{repo_context}

## Issue context

{issue_body}
