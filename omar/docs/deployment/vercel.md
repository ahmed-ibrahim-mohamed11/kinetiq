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

## Projects (created 2026-10-30 with the Vercel CLI, team scope `ahmed-1ff6`)

| Project | ID | Root Directory | Production env vars |
| --- | --- | --- | --- |
| `smartgym-admin` | `prj_TXWAxnu0O67WlpprVKcXFOxJIVDN` | `apps/admin-web` | `NEXT_PUBLIC_SUPABASE_URL`, `NEXT_PUBLIC_SUPABASE_ANON_KEY`, `NEXT_PUBLIC_APP_ENV=production` |
| `smartgym-member` | `prj_LnJrA0DVLdH16cYpZrdqjS7zbFfD` | `apps/member-web` | the same + `NEXT_PUBLIC_VAPID_PUBLIC_KEY` |

Production URLs (first deployed 2026-10-30 from the CLI): **https://smartgym-admin.vercel.app** and **https://smartgym-member.vercel.app** (also reachable as `smartgym-<app>-ahmed-1ff6.vercel.app`). `GET /api/health` returned `{"status":"ok"}` on both and `/login` renders.

How they were configured (repeatable from the repo root; `.vercel/` link files are gitignored):

```bash
npx vercel project create smartgym-admin
npx vercel link --yes --project smartgym-admin --cwd apps/admin-web
npx vercel api /v9/projects/smartgym-admin -X PATCH -F rootDirectory=apps/admin-web -F framework=nextjs -F sourceFilesOutsideRootDirectory=true
npx vercel api /v9/projects/smartgym-admin -X PATCH --input infrastructure/vercel-protection.json   # Vercel Authentication on previews only
npx vercel env add NEXT_PUBLIC_SUPABASE_URL --environment production --value https://vpwudyxfsbyrbpwqdmew.supabase.co --yes --no-sensitive --cwd apps/admin-web
```

Deployment Protection is limited to previews so production URLs are public; the apps authenticate users with Supabase Auth themselves. Preview environments deliberately have **no** Supabase variables until a staging project exists (see the rules above).

## Git integration (GitHub → Vercel)

Status 2026-11-02: **connected and verified** — the first push of `main` produced `source=git` production deployments for both projects (READY, health checks 200). Every push to `main` now deploys both apps; pull requests get preview deployments (without Supabase variables, see above).

`npx vercel git connect --yes --cwd apps/<app>` links a project to the `origin` remote (`github.com/omarahmedvibe210-droid/ai-smart-gym`). It requires, once, in the browser:

1. GitHub connected under **Vercel → Account Settings → Login Connections**.
2. The Vercel GitHub App installed for the `omarahmedvibe210-droid` account with access to `ai-smart-gym`: https://github.com/apps/vercel/installations/new

Until that is done, production is deployed from the CLI. The upload must contain the whole repository (Root Directory is resolved inside it), so link the **repo root** to the project and deploy from there — deploying with `--cwd apps/<app>` fails with "Root Directory does not exist":

```bash
npx vercel link --yes --project smartgym-admin && npx vercel deploy --prod --yes
npx vercel link --yes --project smartgym-member && npx vercel deploy --prod --yes
```

After the first production deployment, add both URLs to Supabase Auth: `site_url` = member app, `additional_redirect_urls` = both (declared in `supabase/config.toml`, applied with `npx supabase config push`).
