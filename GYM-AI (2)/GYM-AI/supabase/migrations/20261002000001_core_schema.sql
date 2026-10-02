-- GYM-AI core schema (MASTER_PROMPT section 13). Phase 3.
-- All user-facing tables are organization-scoped and RLS-protected (see 20261002000002_rls.sql).

create extension if not exists "pgcrypto";

-- ---------------------------------------------------------------- organizations / people
create table organizations (
    id          uuid primary key default gen_random_uuid(),
    name        text not null,
    created_at  timestamptz not null default now()
);

create table profiles (
    user_id         uuid primary key references auth.users (id) on delete cascade,
    organization_id uuid not null references organizations (id) on delete cascade,
    display_name    text not null default '',
    role            text not null default 'coach' check (role in ('admin', 'coach', 'viewer')),
    created_at      timestamptz not null default now()
);
create index profiles_org_idx on profiles (organization_id);

-- ---------------------------------------------------------------- physical hierarchy
create table gyms (
    id              uuid primary key default gen_random_uuid(),
    organization_id uuid not null references organizations (id) on delete cascade,
    name            text not null,
    timezone        text not null default 'UTC',
    created_at      timestamptz not null default now()
);
create index gyms_org_idx on gyms (organization_id);

create table machines (
    id             uuid primary key default gen_random_uuid(),
    gym_id         uuid not null references gyms (id) on delete cascade,
    name           text not null,
    exercise_type  text not null default 'seated_row',
    location_label text,
    created_at     timestamptz not null default now()
);
create index machines_gym_idx on machines (gym_id);

create table model_versions (
    id               uuid primary key default gen_random_uuid(),
    name             text not null,
    semantic_version text not null,
    task             text not null,          -- 'rep_segmentation' | 'pose_2d' | 'lifting_3d' | 'form'
    git_commit       text,
    metrics_json     jsonb not null default '{}'::jsonb,
    artifact_uri     text,
    active           boolean not null default false,
    created_at       timestamptz not null default now(),
    unique (name, semantic_version)
);

create table devices (
    id               uuid primary key default gen_random_uuid(),
    gym_id           uuid not null references gyms (id) on delete cascade,
    name             text not null,
    status           text not null default 'offline' check (status in ('online', 'offline', 'error')),
    last_seen_at     timestamptz,
    agent_version    text,
    model_version_id uuid references model_versions (id),
    -- the edge device authenticates with this secret's HASH; never store the raw token
    api_key_hash     text,
    created_at       timestamptz not null default now()
);
create index devices_gym_idx on devices (gym_id);

create table cameras (
    id            uuid primary key default gen_random_uuid(),
    device_id     uuid not null references devices (id) on delete cascade,
    machine_id    uuid references machines (id) on delete set null,
    source_type   text not null default 'usb' check (source_type in ('usb', 'file', 'rtsp')),
    source_label  text not null default '',   -- USB index or file name; NEVER an URL with credentials
    enabled       boolean not null default true,
    metadata_json jsonb not null default '{}'::jsonb,
    created_at    timestamptz not null default now()
);
create index cameras_device_idx on cameras (device_id);

-- ---------------------------------------------------------------- training activity
create table sessions (
    id          uuid primary key default gen_random_uuid(),
    machine_id  uuid not null references machines (id) on delete cascade,
    device_id   uuid not null references devices (id) on delete cascade,
    athlete_ref text,                        -- opaque reference; no personal data in the MVP
    started_at  timestamptz not null default now(),
    ended_at    timestamptz,
    total_reps  integer not null default 0,
    status      text not null default 'active' check (status in ('active', 'ended', 'aborted')),
    created_at  timestamptz not null default now()
);
create index sessions_machine_idx on sessions (machine_id, started_at desc);
create index sessions_device_idx on sessions (device_id, started_at desc);

create table reps (
    id                 uuid primary key default gen_random_uuid(),
    event_uuid         uuid not null unique,     -- idempotency key from the edge outbox
    session_id         uuid not null references sessions (id) on delete cascade,
    rep_index          integer not null,
    start_frame        integer not null,
    pull_frame         integer not null,
    end_frame          integer not null,
    started_at         timestamptz,
    pull_at            timestamptz,
    ended_at           timestamptz,
    duration_ms        integer,
    pull_duration_ms   integer,
    return_duration_ms integer,
    peak               double precision,
    peak_z             double precision,
    pull_left_frame    integer,
    pull_right_frame   integer,
    lag_pull_ms        double precision,
    lag_onset_ms       double precision,
    amp_ratio          double precision,
    model_version_id   uuid references model_versions (id),
    created_at         timestamptz not null default now(),
    unique (session_id, rep_index)
);
create index reps_session_idx on reps (session_id, rep_index);

create table form_results (
    id               uuid primary key default gen_random_uuid(),
    rep_id           uuid not null references reps (id) on delete cascade,
    model_version_id uuid references model_versions (id),
    class            text not null,           -- 'ok' | 'potential_deviation'  (no diagnosis wording)
    score            double precision,
    feature_json     jsonb not null default '{}'::jsonb,
    coach_review     text check (coach_review in ('pending', 'confirmed', 'dismissed')),
    reviewed_by      uuid references auth.users (id),
    reviewed_at      timestamptz,
    created_at       timestamptz not null default now()
);
create index form_results_rep_idx on form_results (rep_id);

create table alerts (
    id          uuid primary key default gen_random_uuid(),
    session_id  uuid not null references sessions (id) on delete cascade,
    rep_id      uuid references reps (id) on delete set null,
    alert_type  text not null,
    severity    text not null default 'info' check (severity in ('info', 'warning', 'critical')),
    message     text not null,
    status      text not null default 'open' check (status in ('open', 'reviewed', 'dismissed')),
    created_at  timestamptz not null default now(),
    reviewed_at timestamptz
);
create index alerts_session_idx on alerts (session_id, created_at desc);
create index alerts_status_idx on alerts (status) where status = 'open';

create table device_heartbeats (
    id            bigint generated always as identity primary key,
    device_id     uuid not null references devices (id) on delete cascade,
    ts            timestamptz not null default now(),
    camera_ok     boolean not null,
    capture_fps   double precision,
    inference_fps double precision,
    latency_ms    double precision,
    queue_depth   integer,
    error_code    text
);
-- retention: keep heartbeats 14 days (pruned by the retention function below, called from a cron)
create index heartbeats_device_ts_idx on device_heartbeats (device_id, ts desc);

create table evidence_assets (
    id              uuid primary key default gen_random_uuid(),
    rep_id          uuid references reps (id) on delete cascade,
    alert_id        uuid references alerts (id) on delete cascade,
    storage_path    text not null,            -- private bucket path; access via signed URLs only
    kind            text not null default 'clip',
    created_at      timestamptz not null default now(),
    retention_until timestamptz,
    check (rep_id is not null or alert_id is not null)
);

-- ---------------------------------------------------------------- helpers
create or replace function prune_device_heartbeats(keep interval default interval '14 days')
returns bigint language sql security definer set search_path = public as $$
    with deleted as (
        delete from device_heartbeats where ts < now() - keep returning 1
    ) select count(*) from deleted;
$$;
