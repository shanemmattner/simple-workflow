# shftty repo facts card

Verified facts injected into pipeline agents (extractor, size-gate,
verifiers, builders). MAINTENANCE LOOP: every pipeline run's verified
discoveries get appended here with the date; stale entries die when a run
refutes them. One source of truth — do not duplicate into prompts.

## Stack (verified 2026-06-10)

- Next.js 16 Turbopack monorepo: apps/web, apps/api, apps/expo;
  packages/db (Drizzle + Neon Postgres), packages/auth (better-auth 1.6.14),
  packages/shared (XState). Email: Resend. Push: Expo/APNs. SMS: stubbed
  (Twilio 10DLC pending). Pre-commit: lefthook.
- Local test DB: docker-compose Postgres on port 5433. 12-phase E2E harness
  at harness/ (modes local/preview/prod), deterministic SEED_IDS in
  packages/db/src/seed-data.ts.

## better-auth 1.6.14 (verified 2026-06-10 against node_modules dist)

- Password reset API is `auth.api.requestPasswordReset` — `forgetPassword`
  DOES NOT EXIST in this version.
- Plugin-provided APIs do NOT appear in base typings. The admin plugin
  (dist/plugins/admin/) provides `auth.api.createUser` once added to the
  plugins array; its implementation calls `internalAdapter.createUser`
  directly and is NOT gated by `emailAndPassword.disableSignUp`.
- Config (packages/auth/src/server.ts): `disableSignUp: true` (deliberate);
  `resetPasswordTokenExpiresIn: 3600`; `revokeSessionsOnPasswordReset: true`;
  password complexity via hooks.before on PASSWORD_SET_PATHS; rate limits
  /reset-password 3/300s, /sign-in/email 20/min prod; in-memory lockout
  5 fail/15min; user additionalFields role/tenantId/facilityId. DO NOT
  return anything from hooks.after (commits e6d7e35, 323b1e8). createAuth is
  a factory — no module-level singleton.

## Schema facts (verified 2026-06-10; re-check with dump-schema.sh)

- `user.role` is plain text, values `admin | facility | worker` — there is
  NO `facility_user` role. facilityId lives on the auth user, NOT on workers
  (workers has no facility column).
- workers: firstName/lastName/phone NOT NULL; email NULLABLE; userId
  NULLABLE FK->user.id; soft delete via deletedAt (never DELETE FROM).
  `InsertWorkerInput` Omits userId; NO update path sets userId — to link an
  auth user, create the auth user FIRST, then insert the worker row.
- workers is a PHI table: every mutation writes audit_log. Workers are W-2
  ("workers", not "contractors" — CA AB5).
- positions enum: CNA | LVN | RN. shift status enum in shifts.ts:21-29.

## Conventions

- Email templates: pure functions returning {subject, html, text} in
  apps/web/lib/notifications/templates/ (pattern: password-reset.ts). Brand
  in packages/config/brand.ts; admin emails BCC'd on notifications.
- Server actions in apps/web/app/actions/*.ts with 'use server'.
- Cron route pattern: apps/web/app/api/cron/complete-past-shifts.
- ~50 E2E specs; multiple axe rules disabled per-spec (color-contrast
  universal + spec-specific ones) — "color-contrast only" is FALSE.
- Playwright retries: 1 in CI (changed 2026-05-20; docs may still say 2).

## Tools (PA repo, workflows/shared/)

- `dump-schema.sh <repo-root> <table>` — columns + nullability + FKs from
  Drizzle source. Use INSTEAD of reading schema files by hand.
- `pkg-api.sh <repo-root> <package> <symbol>` — searches installed dist
  typings INCLUDING plugin paths; says whether an API is base or
  plugin-provided. Use for any "API X exists" claim.
- `submit-output.py <verdict|claims|sizegate> -` — validate your final JSON
  before emitting it. If INVALID, fix and re-validate.
