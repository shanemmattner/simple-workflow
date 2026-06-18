# Build-prompt lessons

Injected into the build-prompt stage as context. Each lesson was learned
from a walkthrough where the auto-generated prompt needed hand-editing.
Add new lessons at the bottom; never delete — they accumulate.

Format: `- [source] lesson`  
Source = the issue walkthrough that surfaced it.

---

- [shftty#734] When a field is REQUIRED for the feature to make sense (e.g. email for an invite), state that explicitly as a validation constraint — don't assume the builder will infer it from context.
- [shftty#734] Security-sensitive actions (user creation, invite, password reset) must specify: enumeration-safe response (don't leak whether an account exists), rate limiting with concrete numbers, and audit log writes.
- [shftty#734] When the issue was split from a parent, name the sibling issues by number and list the exact pages/components/actions that are OUT OF SCOPE — not just "don't build UI" but "don't build /auth/claim (that's #735), don't build the invite modal (that's #735)."
- [shftty#734] When the test/harness phase will be extended by a sibling issue later, say so explicitly: "structure the phase so #NNN can append items N–M later."
