# Notification worker — the SELF-HOSTED alternative to the notification-dispatch Supabase Edge Function
# (production default, see docs/deployment/notification-dispatch.md). Runs anywhere with outbound HTTPS
# (cloud VM, container platform, or the edge host). The job logic and templates are shared with the
# Edge Function from supabase/functions/_shared/notify, which is copied into the image below.
# Build from the repo root:
#   docker build -f infrastructure/docker/notification-worker.Dockerfile -t smartgym/notification-worker:0.1.0 .
FROM node:22-alpine AS deps
WORKDIR /repo
COPY package.json package-lock.json* ./
COPY packages ./packages
COPY apps/admin-web/package.json ./apps/admin-web/package.json
COPY apps/member-web/package.json ./apps/member-web/package.json
COPY services/notification-worker/package.json ./services/notification-worker/package.json
COPY tests/security/package.json ./tests/security/package.json
RUN npm ci --no-audit --no-fund --omit=dev --workspace services/notification-worker --include-workspace-root=false || npm install --no-audit --no-fund --omit=dev

FROM node:22-alpine
ENV NODE_ENV=production
RUN addgroup -S app && adduser -S app -G app
WORKDIR /repo
COPY --from=deps /repo/node_modules ./node_modules
COPY --from=deps /repo/packages ./packages
COPY services/notification-worker ./services/notification-worker
COPY supabase/functions/_shared/notify ./supabase/functions/_shared/notify
USER app
WORKDIR /repo/services/notification-worker
CMD ["node", "--import", "tsx", "src/index.ts"]
