# Deploying the web apps to Vercel

Two Vercel projects point at the same GitHub repository, each with a different **Root Directory**:

| Project | Root directory | Env vars |
| --- | --- | --- |
| smartgym-admin | `apps/admin-web` | `NEXT_PUBLIC_SUPABASE_URL`, `NEXT_PUBLIC_SUPABASE_ANON_KEY`, `NEXT_PUBLIC_APP_ENV=production` |
| smartgym-member | `apps/member-web` | the same + `NEXT_PUBLIC_VAPID_PUBLIC_KEY` |

`apps/*/vercel.json` already sets install/build commands for the npm-workspaces monorepo (`cd ../.. && npm ci`, `npm run build -w apps/<app>`) and pins region `fra1`. Enable "Include source files outside of the Root Directory" in project settings so workspace packages are available.

Rules:
- Never add `SUPABASE_SERVICE_ROLE_KEY` to a Vercel project that serves a Next.js app. Nothing in `apps/` needs it.
- Preview deployments use a **staging** Supabase project, not production; set env vars per environment.
- Production deploys only from `main` after CI is green (enable "Only build production on green checks" via the GitHub integration).
- Custom domains + HTTPS are automatic; HSTS and the other security headers are set in `next.config.ts`.

Smoke test after deploy: `GET https://<domain>/api/health` returns `{status:"ok"}`; sign in as a staff user and confirm `/dashboard` renders with the branch's cameras.

## Import flow (GitHub → Vercel)

Repository: `github.com/omarahmedvibe210-droid/ai-smart-gym` (private). In the Vercel dashboard, **Add New → Project → Import** that repository **twice**:

1. Project `smartgym-admin`: Root Directory `apps/admin-web`, Framework Next.js, enable *Include source files outside of the Root Directory*. Environment variables (Production + Preview): `NEXT_PUBLIC_SUPABASE_URL=https://vpwudyxfsbyrbpwqdmew.supabase.co`, `NEXT_PUBLIC_SUPABASE_ANON_KEY=<anon key from `supabase projects api-keys`>`, `NEXT_PUBLIC_APP_ENV=production`.
2. Project `smartgym-member`: Root Directory `apps/member-web`, same variables plus `NEXT_PUBLIC_VAPID_PUBLIC_KEY` (generate with `npx web-push generate-vapid-keys`; the private half goes to the notification worker only).

After the first deployment add both Vercel URLs to Supabase **Auth → URL Configuration** (Site URL = member app; redirect allow-list = both), replacing the localhost entries declared in `supabase/config.toml`.

> Status 2026-10-30: Supabase project exists and is linked; the GitHub repository and Vercel projects are the next steps (tracked in `PROGRESS.md`).
