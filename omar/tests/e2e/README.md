# End-to-end browser tests (Phase 7/8)

Playwright suites against a **staging** deployment (Vercel preview + staging Supabase project). They are not implemented yet because they need Supabase Auth and PostgREST running, which this environment cannot provide; the equivalent data-layer guarantees are covered today by `tests/db` (migrations, ingest, RLS, edge→cloud).

Planned scenarios (one spec per bullet):

1. **Staff onboarding**: admin signs in → creates a member → records CCTV + biometric consent → the member row and consents appear; audit log shows the actor.
2. **Kiosk QR check-in**: member app issues a QR (`qr-token` GET) → kiosk verifies it (`qr-token` POST) → live sessions shows the member; expired token is rejected.
3. **Live workout**: `tests/db`-style ingest shim pushes a scripted workout → the member dashboard shows sets appearing via Realtime; unverified sets carry the warning badge.
4. **Checkout and report**: member checks out → report page shows totals and uncertain segments → share link opens anonymously and hides identifiers; expired link 404s.
5. **Consent withdrawal**: member withdraws biometric consent → `biometric_enrollments.revoked_at` set → next device sync lists the template ref as revoked.
6. **Authorization**: member URL-hops to admin routes and other members' reports → redirected/404; staff of org A opens org B ids → empty.

Tooling: `@playwright/test`, seeded staging data via the service role, auth via Supabase admin-created users with password sign-in. Add this workspace when the staging project exists.
