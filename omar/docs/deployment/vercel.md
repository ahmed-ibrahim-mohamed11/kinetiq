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

> Status: no Vercel account is connected in this environment, so no deployment has been performed. The configuration above is complete; the first deployment is a manual step for the account owner and is tracked as a blocker in `PROGRESS.md`.
