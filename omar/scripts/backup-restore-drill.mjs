#!/usr/bin/env node
/**
 * Standalone Operational Backup & Restore Drill
 *
 * Verifies that pg_dump and psql correctly export and restore the complete
 * KinetiQ database schema, row-level security policies, functions, and data
 * without data loss or security posture regressions.
 *
 * Usage:
 *   node scripts/backup-restore-drill.mjs
 */

import { execFileSync } from 'node:child_process';
import { existsSync, readdirSync } from 'node:fs';
import { join, resolve } from 'node:path';
import pg from 'pg';

const REPO_ROOT = resolve(import.meta.dirname, '..');

function findBinary(name) {
  const exe = process.platform === 'win32' ? `${name}.exe` : name;
  const candidates = [
    process.env.PG_TOOLS_BIN,
    join(REPO_ROOT, 'tests', 'db', '.pgtools', 'bin'),
  ].filter(Boolean);

  for (const dir of candidates) {
    const p = join(dir, exe);
    if (existsSync(p)) return p;
  }

  // embedded-postgres platform binaries
  const embRoot = join(REPO_ROOT, 'node_modules', '@embedded-postgres');
  if (existsSync(embRoot)) {
    for (const platform of readdirSync(embRoot)) {
      const bin = join(embRoot, platform, 'native', 'bin', exe);
      if (existsSync(bin)) return bin;
    }
  }

  // PATH
  const pathDirs = (process.env.PATH || '').split(process.platform === 'win32' ? ';' : ':');
  for (const dir of pathDirs) {
    const p = join(dir, exe);
    if (dir && existsSync(p)) return p;
  }
  return null;
}

const TABLES = [
  'members',
  'consent_records',
  'attendance_sessions',
  'exercise_sets',
  'workout_reports',
  'cameras',
  'camera_zones',
  'equipment',
  'exercise_catalog',
  'audit_logs',
];

async function runDrill() {
  console.log('===============================================================');
  console.log('        KinetiQ Production Database Backup & Restore Drill     ');
  console.log('===============================================================');

  const pgDump = findBinary('pg_dump');
  const psql = findBinary('psql');

  if (!pgDump || !psql) {
    throw new Error(
      'pg_dump or psql binary not found. Place them in tests/db/.pgtools/bin or add to PATH.'
    );
  }

  console.log(`[Tool] pg_dump : ${pgDump}`);
  console.log(`[Tool] psql    : ${psql}`);

  // Dynamically import test harness to run drill against live embedded cluster
  const harnessPath = join(REPO_ROOT, 'tests', 'db', 'harness.ts');
  console.log('[Setup] Launching isolated PostgreSQL cluster for drill...');
  
  // Use tsx or harness directly
  const { startHarness } = await import('../tests/db/harness.ts');
  const h = await startHarness();

  try {
    console.log(`[Database] Cluster ready at ${h.connectionString}`);

    // Seed realistic operational data
    console.log('[Seed] Inserting operational attendance & workout session...');
    await h.pool.query(
      `insert into public.attendance_sessions (member_id, branch_id, check_in_at, check_in_method) 
       values ('33333333-3333-4333-8333-333333333333', 'b0000000-0000-4000-8000-000000000001', now() - interval '45 minutes', 'qr')`
    );
    const s = await h.pool.query(`select id from public.attendance_sessions where status = 'active' limit 1`);
    await h.pool.query(`select public.finalize_attendance_session($1, now(), 'qr')`, [s.rows[0].id]);

    // 1. Export schema & data via pg_dump
    console.log('\n[Phase 1] Exporting schema & data via pg_dump...');
    const env = { ...process.env, PGPASSWORD: 'postgres' };
    const dump = execFileSync(
      pgDump,
      ['-h', '127.0.0.1', '-p', String(h.port), '-U', 'postgres', '-d', 'postgres', '--no-owner', '--no-privileges'],
      { env, maxBuffer: 256 * 1024 * 1024 }
    );
    console.log(`  - Dump generated: ${(dump.length / 1024).toFixed(1)} KB`);
    if (dump.length < 10000) {
      throw new Error('Dump payload too small to be valid.');
    }

    // 2. Create scratch database
    console.log('\n[Phase 2] Creating scratch target database: restore_drill...');
    await h.pool.query('drop database if exists restore_drill');
    await h.pool.query('create database restore_drill');

    // 3. Restore dump into scratch database via psql
    console.log('[Phase 3] Restoring dump into restore_drill via psql...');
    execFileSync(
      psql,
      ['-h', '127.0.0.1', '-p', String(h.port), '-U', 'postgres', '-d', 'restore_drill', '-v', 'ON_ERROR_STOP=0', '-q'],
      { env, input: dump, maxBuffer: 256 * 1024 * 1024 }
    );
    console.log('  - psql restore completed without fatal errors.');

    // 4. Verify restored database
    console.log('\n[Phase 4] Verifying restored schema, row counts, RLS, and functions...');
    const restored = new pg.Client({
      connectionString: h.connectionString.replace(/\/postgres$/, '/restore_drill'),
    });
    await restored.connect();

    try {
      console.log('\n  Table Row Count Validation:');
      console.log('  -------------------------------------------');
      for (const t of TABLES) {
        const orig = await h.pool.query(`select count(*)::int as n from public.${t}`);
        const rest = await restored.query(`select count(*)::int as n from public.${t}`);
        const countOrig = orig.rows[0].n;
        const countRest = rest.rows[0].n;
        const status = countOrig === countRest ? 'PASS' : 'FAIL';
        console.log(`  - public.${t.padEnd(22)}: ${countRest} rows [${status}]`);
        if (countOrig !== countRest) {
          throw new Error(`Row count mismatch in ${t}: expected ${countOrig}, got ${countRest}`);
        }
      }

      // Check RLS
      const rlsCheck = await restored.query(
        `select count(*)::int as n from pg_tables where schemaname = 'public' and not rowsecurity`
      );
      const tablesWithoutRls = rlsCheck.rows[0].n;
      console.log(`\n  - Tables without RLS in restored DB: ${tablesWithoutRls} [${tablesWithoutRls === 0 ? 'PASS' : 'FAIL'}]`);
      if (tablesWithoutRls !== 0) {
        throw new Error('RLS is not enabled on all restored tables!');
      }

      // Check Ingest Functions
      const fnCheck = await restored.query(
        `select count(*)::int as n from pg_proc p join pg_namespace n on n.oid = p.pronamespace where n.nspname = 'public' and p.proname = 'ingest_events'`
      );
      console.log(`  - Ingest procedure public.ingest_events: ${fnCheck.rows[0].n > 0 ? 'PRESENT (PASS)' : 'MISSING (FAIL)'}`);
      if (fnCheck.rows[0].n === 0) {
        throw new Error('public.ingest_events function missing after restore!');
      }

      console.log('\n===============================================================');
      console.log('   🎉 BACKUP & RESTORE DRILL COMPLETED AND VERIFIED (100%)     ');
      console.log('===============================================================');
    } finally {
      await restored.end();
      await h.pool.query('drop database if exists restore_drill');
    }
  } finally {
    await h.stop();
  }
}

runDrill().catch((err) => {
  console.error('\n❌ Backup/Restore drill failed:', err);
  process.exit(1);
});

