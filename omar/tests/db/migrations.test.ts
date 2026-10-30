/**
 * Executes every migration + seed on a real PostgreSQL (embedded) and verifies
 * the schema that the application code depends on.
 */
import { afterAll, beforeAll, describe, expect, it } from 'vitest';
import { startHarness, type Harness } from './harness';

let h: Harness;

beforeAll(async () => {
  h = await startHarness({ log: (m) => process.stdout.write(`[db] ${m}\n`) });
});
afterAll(async () => {
  await h?.stop();
});

describe('migrations apply on a real PostgreSQL', () => {
  it('created all expected tables with RLS enabled', async () => {
    const { rows } = await h.pool.query<{ tablename: string; rowsecurity: boolean }>(
      `select tablename, rowsecurity from pg_tables where schemaname = 'public' order by tablename`,
    );
    const names = rows.map((r) => r.tablename);
    for (const t of [
      'organizations', 'branches', 'profiles', 'staff_roles', 'members', 'consent_records', 'biometric_enrollments',
      'edge_devices', 'edge_device_secrets', 'cameras', 'camera_zones', 'equipment', 'exercise_catalog', 'model_versions',
      'attendance_sessions', 'person_tracks', 'tracking_uncertain_segments', 'equipment_usage_sessions',
      'exercise_activity_sessions', 'exercise_sets', 'repetition_events', 'workout_reports', 'push_subscriptions',
      'notification_jobs', 'event_ingest_log', 'ingest_batches', 'device_health_logs', 'camera_status_logs', 'audit_logs',
      'retention_policies',
    ]) {
      expect(names, `missing table ${t}`).toContain(t);
    }
    const withoutRls = rows.filter((r) => !r.rowsecurity).map((r) => r.tablename);
    expect(withoutRls).toEqual([]);
  });

  it('seeded the exercise catalogue and the DEMO tenant', async () => {
    const cat = await h.pool.query(`select count(*)::int as n from public.exercise_catalog`);
    expect(cat.rows[0].n).toBe(13);
    const demo = await h.pool.query(`select member_code::text as code from public.members where id = '33333333-3333-4333-8333-333333333333'`);
    expect(demo.rows[0].code).toBe('GYM-TEST-M2A7');
    const secrets = await h.pool.query(`select count(*)::int as n from public.edge_device_secrets`);
    expect(secrets.rows[0].n).toBe(1);
  });

  it('exposes the privileged functions only to the service role', async () => {
    const fns = ['ingest_events', 'finalize_attendance_session', 'get_device_secrets', 'apply_retention', 'claim_notification_jobs'];
    for (const fn of fns) {
      const { rows } = await h.pool.query(
        `select has_function_privilege('authenticated', p.oid, 'execute') as auth_can, has_function_privilege('anon', p.oid, 'execute') as anon_can
           from pg_proc p join pg_namespace n on n.oid = p.pronamespace where n.nspname = 'public' and p.proname = $1`,
        [fn],
      );
      expect(rows.length, `function ${fn} exists`).toBeGreaterThan(0);
      for (const r of rows) {
        expect(r.auth_can, `${fn} must not be executable by authenticated`).toBe(false);
        expect(r.anon_can, `${fn} must not be executable by anon`).toBe(false);
      }
    }
  });

  it('share tokens are 48 hex chars and the quality_label constraint exists', async () => {
    const def = await h.pool.query(
      `select column_default from information_schema.columns where table_name = 'workout_reports' and column_name = 'share_token'`,
    );
    expect(def.rows[0].column_default).toMatch(/gen_random_bytes\(24\), 'hex'/);
    const chk = await h.pool.query(
      `select pg_get_constraintdef(oid) as def from pg_constraint where conrelid = 'public.repetition_events'::regclass and contype = 'c'`,
    );
    expect(chk.rows.map((r) => r.def).join(' ')).toMatch(/quality_label IS NULL/);
  });

  it('audit trigger records member changes with the acting role', async () => {
    await h.pool.query(`update public.members set phone = '+201000000001' where id = '33333333-3333-4333-8333-333333333333'`);
    const { rows } = await h.pool.query(
      `select action, table_name, new_data->>'phone' as phone from public.audit_logs where table_name = 'members' order by id desc limit 1`,
    );
    expect(rows[0]).toMatchObject({ action: 'UPDATE', table_name: 'members', phone: '+201000000001' });
  });
});
