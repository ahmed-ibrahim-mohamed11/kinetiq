-- ============================================================================
-- AI Smart Gym — core schema (migration 0001)
-- ----------------------------------------------------------------------------
-- Conventions
--   * All tables have `id uuid primary key default gen_random_uuid()`.
--   * Multi-tenant chain: organizations → branches → everything else.
--   * Edge-sourced rows carry (edge_device_id, *_local_id) with a UNIQUE
--     constraint so re-delivery is idempotent.
--   * Raw video and biometric templates are NEVER stored in this database.
--   * RLS is enabled here and policies are defined in 0002.
-- ============================================================================

-- Extensions live in the `extensions` schema (Supabase convention). Migrations run under a
-- role whose search_path may not include it, so every extension object below is qualified.
create schema if not exists extensions;
create extension if not exists "pgcrypto" with schema extensions;
create extension if not exists "citext" with schema extensions;

-- ----------------------------------------------------------------------------
-- Enums
-- ----------------------------------------------------------------------------
create type public.staff_role as enum ('owner', 'admin', 'manager', 'trainer', 'front_desk', 'auditor');
create type public.membership_status as enum ('active', 'frozen', 'expired', 'cancelled');
create type public.consent_type as enum ('biometric_face_identification', 'cctv_workout_tracking', 'data_processing', 'marketing');
create type public.consent_status as enum ('granted', 'withdrawn', 'expired');
create type public.device_status as enum ('provisioning', 'active', 'degraded', 'offline', 'retired');
create type public.camera_protocol as enum ('rtsp', 'onvif', 'nvr_channel');
create type public.camera_purpose as enum ('entrance', 'floor', 'exit', 'mixed');
create type public.zone_type as enum ('equipment', 'entrance', 'exit', 'exclusion', 'free_weights');
create type public.equipment_category as enum ('machine', 'cable', 'free_weight_station', 'cardio', 'bodyweight_station');
create type public.movement_pattern as enum ('push_horizontal', 'push_vertical', 'pull_horizontal', 'pull_vertical', 'squat', 'hinge', 'lunge', 'curl', 'extension', 'core', 'cardio_cyclic');
create type public.model_component as enum ('detector', 'tracker', 'reid', 'pose', 'exercise_classifier', 'face');
create type public.model_runtime as enum ('onnxruntime', 'tensorrt', 'pytorch', 'deepstream');
create type public.checkin_method as enum ('face', 'qr', 'nfc', 'staff_manual');
create type public.session_status as enum ('active', 'finalizing', 'completed', 'abandoned');
create type public.notification_channel as enum ('web_push', 'email', 'sms');
create type public.notification_status as enum ('queued', 'sending', 'delivered', 'failed', 'dead');
create type public.ingest_status as enum ('applied', 'duplicate', 'rejected');

-- ----------------------------------------------------------------------------
-- Utility: updated_at trigger
-- ----------------------------------------------------------------------------
create or replace function public.set_updated_at()
returns trigger language plpgsql as $$
begin
  new.updated_at = now();
  return new;
end $$;

-- ----------------------------------------------------------------------------
-- Tenancy
-- ----------------------------------------------------------------------------
create table public.organizations (
  id            uuid primary key default gen_random_uuid(),
  name          text not null check (length(name) between 2 and 120),
  slug          extensions.citext not null unique check (slug ~ '^[a-z0-9]+(-[a-z0-9]+)*$'),
  country_code  char(2) not null default 'EG',
  created_at    timestamptz not null default now(),
  updated_at    timestamptz not null default now()
);
create trigger trg_organizations_updated before update on public.organizations for each row execute function public.set_updated_at();

create table public.branches (
  id               uuid primary key default gen_random_uuid(),
  organization_id  uuid not null references public.organizations(id) on delete cascade,
  name             text not null check (length(name) between 2 and 120),
  timezone         text not null default 'Africa/Cairo',
  address          text,
  floor_plan       jsonb, -- { width_m, height_m, image_path }
  created_at       timestamptz not null default now(),
  updated_at       timestamptz not null default now(),
  unique (organization_id, name)
);
create index idx_branches_org on public.branches(organization_id);
create trigger trg_branches_updated before update on public.branches for each row execute function public.set_updated_at();

-- ----------------------------------------------------------------------------
-- People
-- ----------------------------------------------------------------------------
create table public.profiles (
  user_id     uuid primary key references auth.users(id) on delete cascade,
  full_name   text,
  avatar_url  text,
  created_at  timestamptz not null default now(),
  updated_at  timestamptz not null default now()
);
create trigger trg_profiles_updated before update on public.profiles for each row execute function public.set_updated_at();

-- Auto-create a profile row when a user signs up / is invited.
create or replace function public.handle_new_auth_user()
returns trigger language plpgsql security definer set search_path = public as $$
begin
  insert into public.profiles (user_id, full_name)
  values (new.id, coalesce(new.raw_user_meta_data->>'full_name', null))
  on conflict (user_id) do nothing;
  return new;
end $$;
create trigger on_auth_user_created after insert on auth.users for each row execute function public.handle_new_auth_user();

create table public.staff_roles (
  id               uuid primary key default gen_random_uuid(),
  user_id          uuid not null references auth.users(id) on delete cascade,
  organization_id  uuid not null references public.organizations(id) on delete cascade,
  -- NULL branch = role applies to every branch of the organization.
  branch_id        uuid references public.branches(id) on delete cascade,
  role             public.staff_role not null,
  created_at       timestamptz not null default now(),
  created_by       uuid references auth.users(id),
  unique (user_id, organization_id, branch_id, role)
);
create index idx_staff_roles_user on public.staff_roles(user_id);
create index idx_staff_roles_org_branch on public.staff_roles(organization_id, branch_id);

create table public.members (
  id               uuid primary key default gen_random_uuid(),
  organization_id  uuid not null references public.organizations(id) on delete cascade,
  branch_id        uuid not null references public.branches(id) on delete restrict,
  user_id          uuid unique references auth.users(id) on delete set null,
  member_code      extensions.citext not null check (member_code ~ '^[A-HJ-NP-Z2-9-]{4,32}$'),
  full_name        text not null check (length(full_name) between 2 and 120),
  phone            text check (phone is null or phone ~ '^\+[1-9][0-9]{7,14}$'),
  email            extensions.citext,
  status           public.membership_status not null default 'active',
  created_at       timestamptz not null default now(),
  updated_at       timestamptz not null default now(),
  unique (organization_id, member_code)
);
create index idx_members_branch on public.members(branch_id);
create index idx_members_user on public.members(user_id);
create trigger trg_members_updated before update on public.members for each row execute function public.set_updated_at();

create table public.consent_records (
  id                    uuid primary key default gen_random_uuid(),
  member_id             uuid not null references public.members(id) on delete cascade,
  consent_type          public.consent_type not null,
  status                public.consent_status not null default 'granted',
  policy_version        text not null,
  granted_at            timestamptz not null default now(),
  withdrawn_at          timestamptz,
  expires_at            timestamptz,
  captured_by_staff_id  uuid references auth.users(id),
  ip_address            inet,
  created_at            timestamptz not null default now(),
  check ((status = 'withdrawn') = (withdrawn_at is not null))
);
create index idx_consent_member_type on public.consent_records(member_id, consent_type, status);

-- ----------------------------------------------------------------------------
-- Devices, cameras, zones, equipment, catalog
-- ----------------------------------------------------------------------------
create table public.edge_devices (
  id                uuid primary key default gen_random_uuid(),
  branch_id         uuid not null references public.branches(id) on delete restrict,
  name              text not null,
  status            public.device_status not null default 'provisioning',
  public_key        text,              -- Ed25519 public key (PEM) for future asymmetric auth
  software_version  text,
  last_seen_at      timestamptz,
  last_sequence     bigint not null default -1,   -- highest event sequence applied
  created_at        timestamptz not null default now(),
  updated_at        timestamptz not null default now(),
  unique (branch_id, name)
);
create index idx_edge_devices_branch on public.edge_devices(branch_id);
create trigger trg_edge_devices_updated before update on public.edge_devices for each row execute function public.set_updated_at();

-- Shared HMAC secrets. NO RLS policies are ever granted to anon/authenticated:
-- only the service role (Edge Function) reads this table. Rotate by inserting a
-- new row and setting `retired_at` on the old one; both are valid during overlap.
create table public.edge_device_secrets (
  id              uuid primary key default gen_random_uuid(),
  edge_device_id  uuid not null references public.edge_devices(id) on delete cascade,
  secret_hash     text not null,              -- sha256(secret) for lookup/audit
  secret          text not null,              -- raw shared secret; move to Vault before production
  created_at      timestamptz not null default now(),
  retired_at      timestamptz
);
create index idx_device_secrets_device on public.edge_device_secrets(edge_device_id) where retired_at is null;

create table public.model_versions (
  id               uuid primary key default gen_random_uuid(),
  component        public.model_component not null,
  name             text not null,
  version          text not null,
  runtime          public.model_runtime not null,
  license          text not null,
  checksum_sha256  text check (checksum_sha256 is null or checksum_sha256 ~ '^[0-9a-f]{64}$'),
  benchmark        jsonb,  -- measured: { fps, latency_ms_p50, latency_ms_p95, hardware, dataset, metrics }
  created_at       timestamptz not null default now(),
  unique (component, name, version)
);

create table public.cameras (
  id              uuid primary key default gen_random_uuid(),
  branch_id       uuid not null references public.branches(id) on delete restrict,
  edge_device_id  uuid references public.edge_devices(id) on delete set null,
  name            text not null,
  protocol        public.camera_protocol not null default 'rtsp',
  purpose         public.camera_purpose not null default 'floor',
  -- Credentials are forbidden here; the edge reads them from its secrets file.
  stream_url      text not null check (stream_url !~ '^[a-zA-Z]+://[^/@]+@'),
  resolution_w    int check (resolution_w > 0),
  resolution_h    int check (resolution_h > 0),
  fps             numeric(6,2) check (fps > 0),
  homography      double precision[] check (homography is null or array_length(homography, 1) = 9),
  enabled         boolean not null default true,
  last_status     text,
  last_status_at  timestamptz,
  created_at      timestamptz not null default now(),
  updated_at      timestamptz not null default now(),
  unique (branch_id, name)
);
create index idx_cameras_branch on public.cameras(branch_id);
create index idx_cameras_device on public.cameras(edge_device_id);
create trigger trg_cameras_updated before update on public.cameras for each row execute function public.set_updated_at();

create table public.exercise_catalog (
  id                uuid primary key default gen_random_uuid(),
  slug              extensions.citext not null unique,
  name              text not null,
  movement_pattern  public.movement_pattern not null,
  primary_signal    text not null,   -- e.g. 'left_elbow_angle'
  rep_profile       jsonb not null,  -- thresholds for the edge state machine, versioned
  form_rules        jsonb,           -- reserved for Phase 9; null in this release
  created_at        timestamptz not null default now()
);

create table public.equipment (
  id                   uuid primary key default gen_random_uuid(),
  branch_id            uuid not null references public.branches(id) on delete restrict,
  name                 text not null,
  category             public.equipment_category not null,
  position_x_m         numeric(7,3) check (position_x_m >= 0),
  position_y_m        numeric(7,3) check (position_y_m >= 0),
  default_exercise_id  uuid references public.exercise_catalog(id),
  enabled              boolean not null default true,
  created_at           timestamptz not null default now(),
  updated_at           timestamptz not null default now(),
  unique (branch_id, name)
);
create index idx_equipment_branch on public.equipment(branch_id);
create trigger trg_equipment_updated before update on public.equipment for each row execute function public.set_updated_at();

create table public.camera_zones (
  id            uuid primary key default gen_random_uuid(),
  camera_id     uuid not null references public.cameras(id) on delete cascade,
  equipment_id  uuid references public.equipment(id) on delete set null,
  name          text not null,
  zone_type     public.zone_type not null default 'equipment',
  polygon       jsonb not null,  -- [[x,y], ...] in image pixels, >= 3 points
  created_at    timestamptz not null default now(),
  updated_at    timestamptz not null default now(),
  unique (camera_id, name),
  check (jsonb_typeof(polygon) = 'array' and jsonb_array_length(polygon) >= 3),
  check (zone_type <> 'equipment' or equipment_id is not null)
);
create index idx_zones_camera on public.camera_zones(camera_id);
create index idx_zones_equipment on public.camera_zones(equipment_id);
create trigger trg_zones_updated before update on public.camera_zones for each row execute function public.set_updated_at();

-- Biometric enrollment REFERENCE only. The template lives encrypted on the edge.
create table public.biometric_enrollments (
  id                uuid primary key default gen_random_uuid(),
  member_id         uuid not null references public.members(id) on delete cascade,
  edge_device_id    uuid not null references public.edge_devices(id) on delete restrict,
  consent_record_id uuid not null references public.consent_records(id) on delete restrict,
  template_ref      text not null,     -- opaque handle on the device
  key_id            text not null,     -- encryption key identifier for rotation audits
  model_version_id  uuid references public.model_versions(id),
  enrolled_at       timestamptz not null default now(),
  revoked_at        timestamptz,
  unique (edge_device_id, template_ref)
);
create index idx_biometric_member on public.biometric_enrollments(member_id) where revoked_at is null;

-- ----------------------------------------------------------------------------
-- Sessions and workout facts
-- ----------------------------------------------------------------------------
create table public.attendance_sessions (
  id                   uuid primary key default gen_random_uuid(),
  member_id            uuid not null references public.members(id) on delete cascade,
  branch_id            uuid not null references public.branches(id) on delete restrict,
  check_in_at          timestamptz not null,
  check_in_method      public.checkin_method not null,
  check_in_confidence  numeric(5,4) check (check_in_confidence between 0 and 1),
  check_out_at         timestamptz,
  check_out_method     public.checkin_method,
  status               public.session_status not null default 'active',
  created_at           timestamptz not null default now(),
  updated_at           timestamptz not null default now(),
  check (check_out_at is null or check_out_at >= check_in_at)
);
-- Only one active session per member at a time.
create unique index uq_attendance_active_member on public.attendance_sessions(member_id) where status = 'active';
create index idx_attendance_member_time on public.attendance_sessions(member_id, check_in_at desc);
create index idx_attendance_branch_time on public.attendance_sessions(branch_id, check_in_at desc);
create trigger trg_attendance_updated before update on public.attendance_sessions for each row execute function public.set_updated_at();

create table public.person_tracks (
  id                     uuid primary key default gen_random_uuid(),
  edge_device_id         uuid not null references public.edge_devices(id) on delete cascade,
  global_track_id        text not null,
  attendance_session_id  uuid references public.attendance_sessions(id) on delete set null,
  member_id              uuid references public.members(id) on delete set null,
  started_at             timestamptz not null,
  ended_at               timestamptz,
  end_reason             text,
  identity_confidence    numeric(5,4) not null check (identity_confidence between 0 and 1),
  tracking_confidence    numeric(5,4) not null check (tracking_confidence between 0 and 1),
  first_camera_id        uuid references public.cameras(id),
  last_camera_id         uuid references public.cameras(id),
  unique (edge_device_id, global_track_id)
);
create index idx_tracks_session on public.person_tracks(attendance_session_id);

create table public.tracking_uncertain_segments (
  id                     uuid primary key default gen_random_uuid(),
  edge_device_id         uuid not null references public.edge_devices(id) on delete cascade,
  global_track_id        text not null,
  attendance_session_id  uuid references public.attendance_sessions(id) on delete cascade,
  from_at                timestamptz not null,
  to_at                  timestamptz not null,
  reason                 text not null,
  check (to_at >= from_at)
);
create index idx_uncertain_session on public.tracking_uncertain_segments(attendance_session_id);

create table public.equipment_usage_sessions (
  id                      uuid primary key default gen_random_uuid(),
  edge_device_id          uuid not null references public.edge_devices(id) on delete cascade,
  usage_local_id          text not null,
  attendance_session_id   uuid references public.attendance_sessions(id) on delete cascade,
  person_track_id         uuid references public.person_tracks(id) on delete set null,
  equipment_id            uuid not null references public.equipment(id) on delete restrict,
  zone_id                 uuid references public.camera_zones(id) on delete set null,
  camera_id               uuid references public.cameras(id) on delete set null,
  started_at              timestamptz not null,
  ended_at                timestamptz,
  end_reason              text,
  association_confidence  numeric(5,4) not null check (association_confidence between 0 and 1),
  unique (edge_device_id, usage_local_id),
  check (ended_at is null or ended_at >= started_at)
);
create index idx_usage_session on public.equipment_usage_sessions(attendance_session_id);
create index idx_usage_equipment_time on public.equipment_usage_sessions(equipment_id, started_at desc);

create table public.exercise_activity_sessions (
  id                          uuid primary key default gen_random_uuid(),
  edge_device_id              uuid not null references public.edge_devices(id) on delete cascade,
  activity_local_id           text not null,
  equipment_usage_session_id  uuid not null references public.equipment_usage_sessions(id) on delete cascade,
  exercise_id                 uuid not null references public.exercise_catalog(id),
  started_at                  timestamptz not null,
  ended_at                    timestamptz,
  classification_confidence   numeric(5,4) not null check (classification_confidence between 0 and 1),
  classifier_model_version_id uuid references public.model_versions(id),
  active_seconds              numeric(10,2) not null default 0 check (active_seconds >= 0),
  unique (edge_device_id, activity_local_id)
);
create index idx_activity_usage on public.exercise_activity_sessions(equipment_usage_session_id);

create table public.exercise_sets (
  id                            uuid primary key default gen_random_uuid(),
  edge_device_id                uuid not null references public.edge_devices(id) on delete cascade,
  set_local_id                  text not null,
  exercise_activity_session_id  uuid not null references public.exercise_activity_sessions(id) on delete cascade,
  set_index                     int not null check (set_index > 0),
  started_at                    timestamptz not null,
  ended_at                      timestamptz,
  rep_count                     int not null default 0 check (rep_count >= 0),
  rest_before_seconds           numeric(8,2) check (rest_before_seconds >= 0),
  confidence                    numeric(5,4) not null default 0 check (confidence between 0 and 1),
  verified                      boolean not null default false,
  unique (edge_device_id, set_local_id),
  unique (exercise_activity_session_id, set_index)
);
create index idx_sets_activity on public.exercise_sets(exercise_activity_session_id);

create table public.repetition_events (
  id               uuid primary key,   -- = event_id from the edge (idempotency key)
  exercise_set_id  uuid not null references public.exercise_sets(id) on delete cascade,
  rep_index        int not null check (rep_index > 0),
  occurred_at      timestamptz not null,
  camera_id        uuid references public.cameras(id) on delete set null,
  confidence       numeric(5,4) not null check (confidence between 0 and 1),
  quality_label    text,  -- reserved for Phase 9; must be null in this release
  unique (exercise_set_id, rep_index),
  check (quality_label is null)
);
create index idx_reps_set_time on public.repetition_events(exercise_set_id, occurred_at);

create table public.workout_reports (
  id                     uuid primary key default gen_random_uuid(),
  attendance_session_id  uuid not null unique references public.attendance_sessions(id) on delete cascade,
  member_id              uuid not null references public.members(id) on delete cascade,
  generated_at           timestamptz not null default now(),
  summary                jsonb not null,
  share_token            text not null unique default encode(extensions.gen_random_bytes(24), 'hex'),
  share_expires_at       timestamptz not null default now() + interval '30 days',
  viewed_at              timestamptz
);
create index idx_reports_member_time on public.workout_reports(member_id, generated_at desc);

-- ----------------------------------------------------------------------------
-- Notifications
-- ----------------------------------------------------------------------------
create table public.push_subscriptions (
  id          uuid primary key default gen_random_uuid(),
  user_id     uuid not null references auth.users(id) on delete cascade,
  endpoint    text not null unique,
  p256dh      text not null,
  auth        text not null,
  user_agent  text,
  created_at  timestamptz not null default now(),
  last_used_at timestamptz,
  disabled_at timestamptz
);
create index idx_push_user on public.push_subscriptions(user_id) where disabled_at is null;

create table public.notification_jobs (
  id               uuid primary key default gen_random_uuid(),
  member_id        uuid not null references public.members(id) on delete cascade,
  channel          public.notification_channel not null,
  template         text not null,
  payload          jsonb not null default '{}'::jsonb,
  dedupe_key       text unique,
  status           public.notification_status not null default 'queued',
  attempts         int not null default 0,
  max_attempts     int not null default 6,
  next_attempt_at  timestamptz not null default now(),
  locked_at        timestamptz,
  locked_by        text,
  last_error       text,
  delivered_at     timestamptz,
  created_at       timestamptz not null default now(),
  updated_at       timestamptz not null default now()
);
create index idx_notification_due on public.notification_jobs(next_attempt_at) where status in ('queued', 'failed');
create trigger trg_notification_updated before update on public.notification_jobs for each row execute function public.set_updated_at();

-- ----------------------------------------------------------------------------
-- Ingest bookkeeping, health, audit
-- ----------------------------------------------------------------------------
create table public.event_ingest_log (
  event_id        uuid primary key,
  edge_device_id  uuid not null references public.edge_devices(id) on delete cascade,
  batch_id        uuid not null,
  sequence        bigint not null,
  event_type      text not null,
  occurred_at     timestamptz not null,
  received_at     timestamptz not null default now(),
  status          public.ingest_status not null,
  error           text
);
create index idx_ingest_device_seq on public.event_ingest_log(edge_device_id, sequence desc);
create index idx_ingest_received on public.event_ingest_log(received_at);

create table public.ingest_batches (
  batch_id        uuid primary key,
  edge_device_id  uuid not null references public.edge_devices(id) on delete cascade,
  sent_at         timestamptz not null,
  received_at     timestamptz not null default now(),
  event_count     int not null,
  accepted        int not null,
  duplicates      int not null,
  rejected        int not null
);

create table public.device_health_logs (
  id                        bigint generated always as identity primary key,
  edge_device_id            uuid not null references public.edge_devices(id) on delete cascade,
  recorded_at               timestamptz not null,
  software_version          text,
  uptime_seconds            bigint,
  gpu_utilization           numeric(5,4),
  gpu_memory_used_mb        numeric(10,2),
  cpu_utilization           numeric(5,4),
  queue_depth               int,
  cameras_online            int,
  cameras_total             int,
  avg_pipeline_latency_ms   numeric(10,2)
);
create index idx_health_device_time on public.device_health_logs(edge_device_id, recorded_at desc);

create table public.camera_status_logs (
  id            bigint generated always as identity primary key,
  camera_id     uuid not null references public.cameras(id) on delete cascade,
  recorded_at   timestamptz not null,
  status        text not null,
  fps_measured  numeric(6,2),
  resolution_w  int,
  resolution_h  int,
  error         text
);
create index idx_camera_status_time on public.camera_status_logs(camera_id, recorded_at desc);

create table public.audit_logs (
  id            bigint generated always as identity primary key,
  occurred_at   timestamptz not null default now(),
  actor_user_id uuid,
  actor_role    text,            -- 'authenticated' | 'service_role' | 'anon'
  action        text not null,   -- INSERT | UPDATE | DELETE | custom
  table_name    text not null,
  row_id        text,
  organization_id uuid,
  old_data      jsonb,
  new_data      jsonb,
  ip_address    inet
);
create index idx_audit_time on public.audit_logs(occurred_at desc);
create index idx_audit_table_row on public.audit_logs(table_name, row_id);

-- Generic audit trigger for sensitive tables. Strips nothing: biometric table
-- stores only references, so logging full rows leaks no template data.
create or replace function public.audit_row_change()
returns trigger language plpgsql security definer set search_path = public as $$
declare
  v_row_id text;
  v_org uuid;
begin
  v_row_id := coalesce((to_jsonb(new)->>'id'), (to_jsonb(old)->>'id'));
  v_org := coalesce((to_jsonb(new)->>'organization_id')::uuid, (to_jsonb(old)->>'organization_id')::uuid);
  insert into public.audit_logs (actor_user_id, actor_role, action, table_name, row_id, organization_id, old_data, new_data)
  values (
    auth.uid(),
    coalesce(current_setting('request.jwt.claim.role', true), current_user),
    tg_op,
    tg_table_name,
    v_row_id,
    v_org,
    case when tg_op in ('UPDATE', 'DELETE') then to_jsonb(old) end,
    case when tg_op in ('INSERT', 'UPDATE') then to_jsonb(new) end
  );
  return coalesce(new, old);
end $$;

create trigger audit_members after insert or update or delete on public.members for each row execute function public.audit_row_change();
create trigger audit_consent after insert or update or delete on public.consent_records for each row execute function public.audit_row_change();
create trigger audit_biometric after insert or update or delete on public.biometric_enrollments for each row execute function public.audit_row_change();
create trigger audit_staff_roles after insert or update or delete on public.staff_roles for each row execute function public.audit_row_change();
create trigger audit_cameras after insert or update or delete on public.cameras for each row execute function public.audit_row_change();
create trigger audit_edge_devices after insert or update or delete on public.edge_devices for each row execute function public.audit_row_change();
create trigger audit_device_secrets after insert or update or delete on public.edge_device_secrets for each row execute function public.audit_row_change();

-- ----------------------------------------------------------------------------
-- Realtime: member live view subscribes to their own sets/reps via RLS.
-- ----------------------------------------------------------------------------
alter publication supabase_realtime add table public.attendance_sessions;
alter publication supabase_realtime add table public.exercise_sets;
alter publication supabase_realtime add table public.repetition_events;
alter publication supabase_realtime add table public.cameras;
alter publication supabase_realtime add table public.edge_devices;

-- ----------------------------------------------------------------------------
-- Enable RLS everywhere (policies in 0002). Default deny.
-- ----------------------------------------------------------------------------
do $$
declare t text;
begin
  for t in
    select tablename from pg_tables where schemaname = 'public'
  loop
    execute format('alter table public.%I enable row level security', t);
  end loop;
end $$;

-- Secrets table: RLS enabled with NO policies (default deny for anon/authenticated) and all
-- grants revoked. Not FORCEd: the security-definer functions get_device_secrets() and
-- issue_device_secret() run as the table owner and must still be able to read/write it.
revoke all on public.edge_device_secrets from anon, authenticated;
revoke all on public.event_ingest_log from anon;
revoke all on public.ingest_batches from anon;
