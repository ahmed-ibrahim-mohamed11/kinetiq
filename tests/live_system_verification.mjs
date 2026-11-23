import { spawn } from 'child_process';
import { readFileSync, existsSync } from 'fs';
import path from 'path';

const rootDir = process.cwd();
const omarDir = path.join(rootDir, 'omar');

console.log('=============================================================');
console.log('  KINETIQ PHASE 06 — COMPREHENSIVE LIVE SYSTEM VERIFICATION  ');
console.log('=============================================================');

// 1. Start Services
console.log('\n[1/5] Launching KinetiQ Services...');
const adminProc = spawn('node.exe', [
  path.join(omarDir, 'node_modules/next/dist/bin/next'),
  'start',
  path.join(omarDir, 'apps/admin-web'),
  '-H', '127.0.0.1',
  '-p', '3000'
], { cwd: path.join(omarDir, 'apps/admin-web'), stdio: 'pipe' });

const memberProc = spawn('node.exe', [
  path.join(omarDir, 'node_modules/next/dist/bin/next'),
  'start',
  path.join(omarDir, 'apps/member-web'),
  '-H', '127.0.0.1',
  '-p', '3001'
], { cwd: path.join(omarDir, 'apps/member-web'), stdio: 'pipe' });

const pythonExe = path.join(omarDir, 'services/edge-ai/.venv/Scripts/python.exe');
const edgeProc = spawn(pythonExe, ['-m', 'edge_ai', 'run'], {
  cwd: path.join(omarDir, 'services/edge-ai'),
  stdio: 'pipe'
});

edgeProc.stdout.on('data', (d) => {
  const line = d.toString().trim();
  if (line.includes('edge-ai.started') || line.includes('HTTP Request') || line.includes('sync.event_rejected')) {
    console.log('  [Edge AI Log]', line.slice(0, 150));
  }
});

function wait(ms) {
  return new Promise(resolve => setTimeout(resolve, ms));
}

async function probeHttp(url, timeoutMs = 25000) {
  const start = Date.now();
  while (Date.now() - start < timeoutMs) {
    try {
      const t0 = performance.now();
      const res = await fetch(url);
      const latency = Math.round(performance.now() - t0);
      if (res.ok) {
        return { ok: true, status: res.status, latency, body: await res.text() };
      }
    } catch (e) {
      // wait and retry
    }
    await wait(600);
  }
  return { ok: false };
}

async function run() {
  try {
    // 2. Wait for readiness
    console.log('\n[2/5] Waiting for service readiness...');
    const adminReady = await probeHttp('http://127.0.0.1:3000/api/health');
    console.log('  Admin Web Health   :', adminReady.ok ? `ONLINE (${adminReady.latency}ms)` : 'TIMEOUT');

    const memberReady = await probeHttp('http://127.0.0.1:3001/api/health');
    console.log('  Member Web Health  :', memberReady.ok ? `ONLINE (${memberReady.latency}ms)` : 'TIMEOUT');

    const edgeReady = await probeHttp('http://127.0.0.1:8088/healthz');
    console.log('  Edge AI Health     :', edgeReady.ok ? `ONLINE (${edgeReady.latency}ms)` : 'TIMEOUT');

    // 3. Telemetry details
    console.log('\n[3/5] Inspecting Edge AI Telemetry...');
    if (edgeReady.ok) {
      try {
        const edgeJson = JSON.parse(edgeReady.body);
        console.log('  - Uptime (s)        :', edgeJson.uptime_seconds);
        console.log('  - Backend Connected :', edgeJson.backend_connected);
        console.log('  - Ingest URL        :', edgeJson.ingest_url);
        console.log('  - Queue Depth       :', edgeJson.queue_depth);
        console.log('  - Active Provider   :', edgeJson.active_provider);
        console.log('  - Detector Stage    :', edgeJson.stages?.detector);
        console.log('  - Pose Stage        :', edgeJson.stages?.pose);
        console.log('  - Face Stage        :', edgeJson.stages?.face);
        console.log('  - Reps Admitted     :', edgeJson.reps_admitted);
        console.log('  - Cameras           :', Object.keys(edgeJson.cameras || {}).map(k => `${k}: ${edgeJson.cameras[k].status} (${edgeJson.cameras[k].fps} FPS)`));
      } catch (e) {
        console.log('  Raw Edge body:', edgeReady.body);
      }
    }

    // 4. Test Route Performance
    console.log('\n[4/5] Testing Route Navigation & Response Times...');
    const routes = [
      { name: 'Admin Dashboard', url: 'http://127.0.0.1:3000/' },
      { name: 'Admin Members', url: 'http://127.0.0.1:3000/members' },
      { name: 'Admin Reception', url: 'http://127.0.0.1:3000/reception' },
      { name: 'Admin Subscriptions', url: 'http://127.0.0.1:3000/subscriptions' },
      { name: 'Admin Vision Inspector', url: 'http://127.0.0.1:3000/vision' },
      { name: 'Admin Diagnostics', url: 'http://127.0.0.1:3000/diagnostics' },
      { name: 'Member Home', url: 'http://127.0.0.1:3001/' },
      { name: 'Member Workout', url: 'http://127.0.0.1:3001/workout' },
      { name: 'Member Profile', url: 'http://127.0.0.1:3001/profile' }
    ];

    for (const r of routes) {
      const t0 = performance.now();
      try {
        const res = await fetch(r.url);
        const lat = Math.round(performance.now() - t0);
        console.log(`  - [${res.status}] ${r.name.padEnd(24)}: ${lat}ms`);
      } catch (err) {
        console.log(`  - [ERR] ${r.name.padEnd(24)}: ${err.message}`);
      }
    }

    // Let Edge AI run for another 5 seconds to capture frames & sync
    console.log('\n[5/5] Letting Edge AI process frames and sync with Cloud...');
    await wait(5000);

    // Final health probe to check queue depth and sync
    const finalEdge = await probeHttp('http://127.0.0.1:8088/healthz');
    if (finalEdge.ok) {
      const finalJson = JSON.parse(finalEdge.body);
      console.log('  Final Edge AI State:');
      console.log('  - Queue Depth       :', finalJson.queue_depth);
      console.log('  - Sync Failures     :', finalJson.sync_failures);
      console.log('  - Last Sync Error   :', finalJson.sync_last_error);
      console.log('  - Total Processed   :', finalJson.total_frames_processed ?? 'N/A');
    }

    console.log('\nVerification run complete.');
  } finally {
    console.log('\nShutting down processes gracefully...');
    try { adminProc.kill(); } catch (e) {}
    try { memberProc.kill(); } catch (e) {}
    try { edgeProc.kill(); } catch (e) {}
  }
}

run();

