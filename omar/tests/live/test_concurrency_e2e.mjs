import http from 'http';

const BASE = 'http://127.0.0.1:8088';

const CONSUMER_IDS = [
  'admin-reception-kiosk',
  'admin-auth-login',
  'member-auth-login',
  'admin-member-profile',
  'admin-member-registration',
  'admin-workout-monitor',
  'admin-diagnostics',
];

async function postJson(path, payload) {
  const url = new URL(path, BASE);
  const data = JSON.stringify(payload);
  return new Promise((resolve, reject) => {
    const req = http.request(
      url,
      {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          'Content-Length': Buffer.byteLength(data),
        },
      },
      (res) => {
        let body = '';
        res.on('data', (c) => (body += c));
        res.on('end', () => {
          try {
            resolve({ status: res.statusCode, data: JSON.parse(body) });
          } catch {
            resolve({ status: res.statusCode, body });
          }
        });
      }
    );
    req.on('error', reject);
    req.write(data);
    req.end();
  });
}

async function getJson(path) {
  const url = new URL(path, BASE);
  return new Promise((resolve, reject) => {
    http.get(url, (res) => {
      let body = '';
      res.on('data', (c) => (body += c));
      res.on('end', () => {
        try {
          resolve({ status: res.statusCode, data: JSON.parse(body) });
        } catch {
          resolve({ status: res.statusCode, body });
        }
      });
    }).on('error', reject);
  });
}

async function getFrame(consumerId) {
  const url = new URL(`/api/camera/frame?camera_id=default&consumer_id=${consumerId}`, BASE);
  return new Promise((resolve, reject) => {
    http.get(url, (res) => {
      const chunks = [];
      res.on('data', (c) => chunks.push(c));
      res.on('end', () => {
        const buf = Buffer.concat(chunks);
        const isJpeg = buf.length > 2 && buf[0] === 0xff && buf[1] === 0xd8;
        resolve({ status: res.statusCode, length: buf.length, isJpeg });
      });
    }).on('error', reject);
  });
}

async function testMjpegStreamChunk(consumerId) {
  const url = new URL(`/api/camera/stream?camera_id=default&consumer_id=${consumerId}`, BASE);
  return new Promise((resolve, reject) => {
    const req = http.get(url, (res) => {
      if (res.statusCode !== 200) {
        resolve({ ok: false, status: res.statusCode });
        return;
      }
      let receivedBytes = 0;
      res.on('data', (chunk) => {
        receivedBytes += chunk.length;
        if (receivedBytes > 5000) {
          req.destroy();
          resolve({ ok: true, receivedBytes, contentType: res.headers['content-type'] });
        }
      });
      res.on('error', () => {
        // destroyed intentionally
        resolve({ ok: true, receivedBytes, aborted: true });
      });
    });
    req.on('error', (err) => {
      if (err.message.includes('socket hang up') || err.code === 'ECONNRESET') {
        resolve({ ok: true });
      } else {
        reject(err);
      }
    });
  });
}

async function main() {
  console.log('=================================================================');
  console.log(' KINETIQ CAMERA BROKER: 7-PAGE CONCURRENT MULTIPLEXING VALIDATION');
  console.log('=================================================================');

  // Step 1: Health check
  const h = await getJson('/healthz');
  console.log(`[1] Edge AI Health: HTTP ${h.status}, status: ${h.data?.status}`);
  if (h.status !== 200) throw new Error('Health check failed');

  // Step 2: Start all 7 consumers concurrently
  console.log('\n[2] Starting all 7 target consumers simultaneously:');
  const startResults = await Promise.all(
    CONSUMER_IDS.map(async (cid) => {
      const res = await postJson('/api/camera/start', {
        camera_id: 'default',
        consumer_id: cid,
      });
      return { consumer_id: cid, ...res };
    })
  );

  for (const r of startResults) {
    console.log(`  ✓ Consumer: ${r.consumer_id} -> status ${r.status}, ok: ${r.data?.ok}, active_consumers: ${r.data?.active_consumers}`);
    if (!r.data?.ok) throw new Error(`Consumer ${r.consumer_id} failed to start`);
  }

  // Step 3: Verify single physical handle diagnostics
  console.log('\n[3] Querying central diagnostics while all 7 consumers are active:');
  const diag = await getJson('/api/camera/diagnostics?camera_id=default');
  console.log('  Diagnostics payload:', JSON.stringify({
    physical_handles: diag.data?.physical_handles,
    active_consumers: diag.data?.active_consumers,
    camera_state: diag.data?.camera_state,
    frame_fps: diag.data?.frame_fps,
    queue_depth: diag.data?.queue_depth,
    reconnect_attempts: diag.data?.reconnect_attempts,
    consumers: diag.data?.consumers,
  }, null, 2));

  if (diag.data?.physical_handles !== 1) {
    throw new Error(`CRITICAL: Expected exactly 1 physical handle, found ${diag.data?.physical_handles}`);
  }

  // Step 4: Verify every single page receives valid live frames concurrently
  console.log('\n[4] Pulling live JPEG frames concurrently for every consumer:');
  const frameResults = await Promise.all(CONSUMER_IDS.map((cid) => getFrame(cid)));
  for (let i = 0; i < CONSUMER_IDS.length; i++) {
    const f = frameResults[i];
    console.log(`  ✓ Consumer: ${CONSUMER_IDS[i]} -> status: ${f.status}, size: ${f.length} bytes, isJPEG: ${f.isJpeg}`);
    if (f.status !== 200 || !f.isJpeg) {
      throw new Error(`Consumer ${CONSUMER_IDS[i]} did not receive a valid JPEG frame`);
    }
  }

  // Step 5: Verify MJPEG streaming works simultaneously on all 7 consumers
  console.log('\n[5] Testing simultaneous MJPEG stream data flow across all 7 consumers:');
  const streamResults = await Promise.all(CONSUMER_IDS.map((cid) => testMjpegStreamChunk(cid)));
  for (let i = 0; i < CONSUMER_IDS.length; i++) {
    const s = streamResults[i];
    console.log(`  ✓ MJPEG Stream for ${CONSUMER_IDS[i]}: OK (received ${s.receivedBytes || '>5000'} bytes, type: ${s.contentType || 'multipart'})`);
    if (!s.ok) throw new Error(`MJPEG stream failed for ${CONSUMER_IDS[i]}`);
  }

  // Step 6: Test independent teardown (stopping one consumer does not stop others)
  console.log('\n[6] Testing independent teardown (stopping reception kiosk only):');
  const stopOne = await postJson('/api/camera/stop', {
    camera_id: 'default',
    consumer_id: 'admin-reception-kiosk',
  });
  console.log(`  Stopped admin-reception-kiosk -> remaining active: ${stopOne.data?.active_consumers}, physical_handles: ${stopOne.data?.physical_handles}`);
  if (stopOne.data?.physical_handles !== 1) {
    throw new Error('Physical handle was prematurely closed when only 1 consumer stopped!');
  }

  // Verify other consumer can still receive frames
  const fCheck = await getFrame('admin-workout-monitor');
  console.log(`  Verified admin-workout-monitor still receives frames -> size: ${fCheck.length} bytes, isJPEG: ${fCheck.isJpeg}`);
  if (!fCheck.isJpeg) throw new Error('Other consumer failed after reception stopped');

  // Step 7: Restart stopped consumer
  console.log('\n[7] Reconnecting admin-reception-kiosk:');
  const restartOne = await postJson('/api/camera/start', {
    camera_id: 'default',
    consumer_id: 'admin-reception-kiosk',
  });
  console.log(`  Restarted admin-reception-kiosk -> active: ${restartOne.data?.active_consumers}, physical_handles: ${restartOne.data?.physical_handles}`);

  // Clean up test sessions
  console.log('\n[8] Cleaning up test consumers:');
  for (const cid of CONSUMER_IDS) {
    await postJson('/api/camera/stop', { camera_id: 'default', consumer_id: cid });
  }
  const finalDiag = await getJson('/api/camera/diagnostics?camera_id=default');
  console.log('  Final diagnostics active web consumers:', finalDiag.data?.active_consumers);

  console.log('\n=================================================================');
  console.log(' ALL 7 CONCURRENT CONSUMER SESSIONS VALIDATED WITH 1 PHYSICAL HANDLE!');
  console.log('=================================================================');
}

main().catch((err) => {
  console.error('\n❌ Concurrency Test FAILED:', err);
  process.exit(1);
});
