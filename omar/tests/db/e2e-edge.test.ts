/**
 * END-TO-END: real Python edge simulation → HMAC-signed HTTP ingest → real PostgreSQL.
 *
 * A small Node HTTP server implements the ingest-events contract exactly like the
 * Deno Edge Function (device lookup, HMAC over the canonical string, clock skew,
 * `ingest_events` RPC). Then `python -m edge_ai simulate` runs the actual pipeline
 * (two cameras, one member, two sets) and syncs its queue here. We assert that the
 * database ends up with the completed session, the sets/reps, and the report.
 *
 * Skips (loudly) when the edge venv is not present.
 */
import { createHash, createHmac, timingSafeEqual } from 'node:crypto';
import { spawn } from 'node:child_process';
import { existsSync, mkdtempSync, rmSync } from 'node:fs';
import { createServer, type Server } from 'node:http';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { afterAll, beforeAll, describe, expect, it } from 'vitest';
import { startHarness, type Harness } from './harness';

const ROOT = join(import.meta.dirname ?? __dirname, '..', '..');
const EDGE_DIR = join(ROOT, 'services', 'edge-ai');
const PYTHON = process.platform === 'win32' ? join(EDGE_DIR, '.venv', 'Scripts', 'python.exe') : join(EDGE_DIR, '.venv', 'bin', 'python');
const DEVICE = 'd0000000-0000-4000-8000-000000000001';
const MEMBER = '33333333-3333-4333-8333-333333333333';

let h: Harness;
let server: Server;
let port: number;
const seen: { batches: number; events: number; rejected: unknown[] } = { batches: 0, events: 0, rejected: [] };

function readBody(req: import('node:http').IncomingMessage): Promise<string> {
  return new Promise((resolve, reject) => {
    let data = '';
    req.on('data', (c) => (data += c));
    req.on('end', () => resolve(data));
    req.on('error', reject);
  });
}

beforeAll(async () => {
  h = await startHarness();
  server = createServer(async (req, res) => {
    const send = (status: number, body: unknown) => {
      res.writeHead(status, { 'content-type': 'application/json' });
      res.end(JSON.stringify(body));
    };
    try {
      if (req.method !== 'POST' || !req.url?.endsWith('/ingest-events')) return send(404, { code: 'not_found' });
      const deviceId = String(req.headers['x-device-id'] ?? '');
      const signature = String(req.headers['x-device-signature'] ?? '');
      const raw = await readBody(req);
      const batch = JSON.parse(raw);
      if (batch.device_id !== deviceId) return send(401, { code: 'unauthorized' });
      if (Math.abs(Date.now() - Date.parse(batch.sent_at)) / 1000 > 300) return send(401, { code: 'unauthorized', message: 'skew' });
      const { rows: secrets } = await h.pool.query<{ secret: string }>('select secret from public.get_device_secrets($1)', [deviceId]);
      const eventsHash = createHash('sha256').update(JSON.stringify(batch.events)).digest('hex');
      const canonical = `${batch.device_id}\n${batch.batch_id}\n${batch.sent_at}\n${eventsHash}`;
      const ok = secrets.some((s) => {
        const expected = createHmac('sha256', s.secret).update(canonical).digest('hex');
        return expected.length === signature.length && timingSafeEqual(Buffer.from(expected), Buffer.from(signature));
      });
      if (!ok) return send(401, { code: 'unauthorized' });
      const { rows } = await h.pool.query('select public.ingest_events($1::uuid, $2::uuid, $3::timestamptz, $4::jsonb) as r', [
        deviceId,
        batch.batch_id,
        batch.sent_at,
        JSON.stringify(batch.events),
      ]);
      seen.batches++;
      seen.events += batch.events.length;
      seen.rejected.push(...rows[0].r.rejected);
      send(200, rows[0].r);
    } catch (err) {
      const msg = (err as Error).message;
      if (/already processed/.test(msg)) return send(200, { accepted: 0, duplicates: 0, rejected: [], replayed: true });
      console.error('ingest shim error', msg);
      send(500, { code: 'internal', message: msg });
    }
  });
  await new Promise<void>((resolve) => server.listen(0, '127.0.0.1', resolve));
  const addr = server.address();
  port = typeof addr === 'object' && addr ? addr.port : 0;
});

afterAll(async () => {
  server?.close();
  await h?.stop();
});

describe('edge → cloud end-to-end', () => {
  it('python simulation syncs a full workout into the real database', async (ctx) => {
    if (!existsSync(PYTHON)) {
      console.warn(`edge venv not found at ${PYTHON}; e2e SKIPPED`);
      ctx.skip();
      return;
    }
    const dataDir = mkdtempSync(join(tmpdir(), 'smartgym-edge-'));
    try {
      const { rows: ex } = await h.pool.query(`select slug, id from public.exercise_catalog`);
      // The example cameras.yaml carries placeholder exercise ids; point the simulation at the seeded catalogue.
      const camerasYaml = join(dataDir, 'cameras.yaml');
      const example = (await import('node:fs')).readFileSync(join(EDGE_DIR, 'config', 'cameras.example.yaml'), 'utf8');
      const ids = Object.fromEntries(ex.map((r) => [r.slug, r.id]));
      const patched = example.replace(/exercise_ids:[\s\S]*$/, `exercise_ids:\n  lat-pulldown: ${ids['lat-pulldown']}\n  leg-press: ${ids['leg-press']}\n  chest-press-machine: ${ids['chest-press-machine']}\n`);
      (await import('node:fs')).writeFileSync(camerasYaml, patched);

      const env = {
        ...process.env,
        EDGE_DEVICE_ID: DEVICE,
        EDGE_BRANCH_ID: 'b0000000-0000-4000-8000-000000000001',
        EDGE_DEVICE_SECRET: 'dev-only-device-secret-change-me',
        EDGE_INGEST_URL: `http://127.0.0.1:${port}/functions/v1/ingest-events`,
        EDGE_DATA_DIR: dataDir,
        EDGE_CAMERAS_FILE: camerasYaml,
        EDGE_LOG_LEVEL: 'WARNING',
        PYTHONIOENCODING: 'utf-8',
      };
      // Must be async: the ingest shim server lives in this process, so a blocking spawnSync would starve it.
      const proc = await new Promise<{ status: number | null; stdout: string; stderr: string }>((resolve, reject) => {
        const child = spawn(PYTHON, ['-m', 'edge_ai', 'simulate', '--seconds', '60'], { cwd: EDGE_DIR, env });
        let stdout = '';
        let stderr = '';
        child.stdout.on('data', (d) => (stdout += d));
        child.stderr.on('data', (d) => (stderr += d));
        const timer = setTimeout(() => child.kill(), 170_000);
        child.on('error', reject);
        child.on('close', (status) => {
          clearTimeout(timer);
          resolve({ status, stdout, stderr });
        });
      });
      const out = proc.stdout;
      console.log(out.trim().split('\n').slice(-2).join('\n'));
      expect(proc.status, `python exited ${proc.status}\nSTDOUT:\n${out}\nSTDERR:\n${proc.stderr}`).toBe(0);
      expect(out).toMatch(/SYNC → batches=\d+ ok=True remaining=0/);
      expect(seen.rejected, JSON.stringify(seen.rejected)).toEqual([]);
      expect(seen.events).toBeGreaterThan(20);

      const session = await h.pool.query(`select id, status, check_in_method, check_out_method from public.attendance_sessions where member_id = $1 order by check_in_at desc limit 1`, [MEMBER]);
      expect(session.rows[0]).toMatchObject({ status: 'completed', check_in_method: 'qr', check_out_method: 'qr' });
      const sets = await h.pool.query(
        `select s.set_index, s.rep_count, s.verified from public.exercise_sets s
           join public.exercise_activity_sessions a on a.id = s.exercise_activity_session_id
           join public.equipment_usage_sessions u on u.id = a.equipment_usage_session_id
          where u.attendance_session_id = $1 order by s.set_index`,
        [session.rows[0].id],
      );
      expect(sets.rows.length).toBe(2);
      for (const s of sets.rows) expect(s.rep_count).toBeGreaterThanOrEqual(7);
      const reps = await h.pool.query(`select count(*)::int as n from public.repetition_events`);
      expect(reps.rows[0].n).toBe(sets.rows.reduce((a, s) => a + s.rep_count, 0));
      const report = await h.pool.query(`select summary from public.workout_reports where attendance_session_id = $1`, [session.rows[0].id]);
      expect(report.rows[0].summary.total_sets).toBe(2);
      expect(report.rows[0].summary.exercises[0].exercise_slug).toBe('leg-press');
      expect(report.rows[0].summary.uncertain_segments.length).toBeGreaterThan(0); // track lost at the end → no_camera_coverage
      const health = await h.pool.query(`select cameras_total, queue_depth from public.device_health_logs order by id desc limit 1`);
      expect(health.rows[0].cameras_total).toBe(0); // simulation has no camera workers
      const ingestLog = await h.pool.query(`select status, count(*)::int as n from public.event_ingest_log group by status`);
      expect(ingestLog.rows.find((r) => r.status === 'rejected')).toBeUndefined();
    } finally {
      rmSync(dataDir, { recursive: true, force: true });
    }
  });
});
