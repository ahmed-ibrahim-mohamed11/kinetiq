import { readFileSync } from 'node:fs';
import { afterAll, beforeAll, describe, expect, it } from 'vitest';
import { startHarness, type Harness } from '../../db/harness';
import type pg from 'pg';

const SQL = readFileSync(
  'C:/Users/mamdo/AppData/Local/Temp/claude/c--Users-mamdo-OneDrive-Desktop-omar/3d7dafdc-6ad2-4302-9493-183dfe8a7b70/scratchpad/m0012/20261102000012_zone_camera_branch_consistency.sql',
  'utf8',
);

let h: Harness;

async function expectCode(c: pg.PoolClient, sql: string, params: unknown[], code: string): Promise<void> {
  await c.query('savepoint sp');
  let caught: { code?: string } | null = null;
  try {
    await c.query(sql, params);
  } catch (err) {
    caught = err as { code?: string };
  }
  await c.query('rollback to savepoint sp');
  expect(caught, `expected SQLSTATE ${code} for: ${sql}`).not.toBeNull();
  expect(caught?.code).toBe(code);
}

beforeAll(async () => {
  // Only the applied baseline (0001-0007) plus the candidate migration under test.
  process.env.TEST_DB_SKIP_MIGRATIONS = '20261102000008,20261102000009,20261102000010,20261102000011,20261102000012';
  h = await startHarness({ seed: false });
  await h.asService(async (c) => {
    await c.query(SQL);
  });
}, 300_000);

afterAll(async () => {
  await h?.stop();
});

describe('migration 0012: branch consistency for zones and camera devices', () => {
  it('enforces the rules for every caller and stays idempotent', async () => {
    await h.asService(async (c) => {
      const org = (await c.query(`insert into public.organizations (name, slug) values ('Org 0012', 'org-0012') returning id`)).rows[0].id;
      const branchA = (await c.query(`insert into public.branches (organization_id, name) values ($1, 'Branch A') returning id`, [org])).rows[0].id;
      const branchB = (await c.query(`insert into public.branches (organization_id, name) values ($1, 'Branch B') returning id`, [org])).rows[0].id;
      const camA = (await c.query(`insert into public.cameras (branch_id, name, stream_url) values ($1, 'cam A', 'rtsp://192.0.2.1/x') returning id`, [branchA])).rows[0].id;
      const eqA = (await c.query(`insert into public.equipment (branch_id, name, category) values ($1, 'eq A', 'machine') returning id`, [branchA])).rows[0].id;
      const eqB = (await c.query(`insert into public.equipment (branch_id, name, category) values ($1, 'eq B', 'machine') returning id`, [branchB])).rows[0].id;
      const devA = (await c.query(`insert into public.edge_devices (branch_id, name) values ($1, 'dev A') returning id`, [branchA])).rows[0].id;
      const devB = (await c.query(`insert into public.edge_devices (branch_id, name) values ($1, 'dev B') returning id`, [branchB])).rows[0].id;

      // Zones: cross-branch equipment is refused; same-branch and unlinked zones are accepted.
      await expectCode(c, `insert into public.camera_zones (camera_id, equipment_id, name, zone_type, polygon) values ($1, $2, 'bad', 'equipment', '[[0,0],[10,0],[10,10]]')`, [camA, eqB], '23514');
      const zone = (await c.query(`insert into public.camera_zones (camera_id, equipment_id, name, zone_type, polygon) values ($1, $2, 'ok', 'equipment', '[[0,0],[10,0],[10,10]]') returning id`, [camA, eqA])).rows[0].id;
      await c.query(`insert into public.camera_zones (camera_id, equipment_id, name, zone_type, polygon) values ($1, null, 'door', 'entrance', '[[0,0],[10,0],[10,10]]')`, [camA]);
      await expectCode(c, `update public.camera_zones set equipment_id = $2 where id = $1`, [zone, eqB], '23514');

      // Cameras: cross-branch device is refused on insert and update; same-branch device is accepted.
      await expectCode(c, `insert into public.cameras (branch_id, name, stream_url, edge_device_id) values ($1, 'cam bad', 'rtsp://192.0.2.2/x', $2)`, [branchA, devB], '23514');
      await c.query(`update public.cameras set edge_device_id = $2 where id = $1`, [camA, devA]);
      await expectCode(c, `update public.cameras set edge_device_id = $2 where id = $1`, [camA, devB], '23514');

      // Moving linked equipment or an assigned device to another branch is refused.
      await expectCode(c, `update public.equipment set branch_id = $2 where id = $1`, [eqA, branchB], '23514');
      await expectCode(c, `update public.edge_devices set branch_id = $2 where id = $1`, [devA, branchB], '23514');
      // Unlinked equipment can move.
      await c.query(`update public.equipment set branch_id = $2 where id = $1`, [eqB, branchA]);

      // Idempotent re-application with data present.
      await c.query(SQL);
      const triggers = await c.query(
        `select count(*)::int as n from pg_trigger where tgname in ('trg_zone_equipment_branch','trg_camera_device_branch','trg_equipment_branch_unlinked','trg_device_branch_unlinked')`,
      );
      expect(triggers.rows[0].n).toBe(4);
    });
  });

  it('keeps the helper functions away from anon and authenticated', async () => {
    await h.asService(async (c) => {
      const r = await c.query(
        `select proname, has_function_privilege('anon', oid, 'execute') as anon_exec, has_function_privilege('authenticated', oid, 'execute') as auth_exec
           from pg_proc where proname in ('_assert_zone_equipment_branch','_assert_camera_device_branch','_assert_equipment_branch_unlinked','_assert_device_branch_unlinked') order by 1`,
      );
      expect(r.rows.length).toBe(4);
      for (const row of r.rows) {
        expect(row.anon_exec, row.proname).toBe(false);
        expect(row.auth_exec, row.proname).toBe(false);
      }
    });
  });
});
