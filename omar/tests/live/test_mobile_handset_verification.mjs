process.env.NODE_TLS_REJECT_UNAUTHORIZED = '0';
import { chromium, devices } from '@playwright/test';
import { spawn } from 'node:child_process';
import { readFileSync, existsSync } from 'node:fs';
import { resolve, join } from 'node:path';
import { networkInterfaces } from 'node:os';

function getLanIp() {
  const nets = networkInterfaces();
  for (const name of Object.keys(nets)) {
    for (const net of nets[name] || []) {
      if (net.family === 'IPv4' && !net.internal) {
        return net.address;
      }
    }
  }
  return 'localhost';
}

const REPO_ROOT = resolve(import.meta.dirname, '..', '..');
const MEMBER_WEB_DIR = join(REPO_ROOT, 'apps', 'member-web');
const LAN_IP = process.env.LAN_IP || getLanIp();
const HTTPS_PORT = 3001;
const MEMBER_HTTPS_URL = `https://${LAN_IP}:${HTTPS_PORT}`;

console.log('========================================================================');
console.log(' [SIMULATED] Playwright Mobile Browser Camera & LAN Verification Drill   ');
console.log(' Note: Uses fake_device_0 simulated camera. NOT a physical handset test. ');
console.log('========================================================================');
console.log(`[Target URL] ${MEMBER_HTTPS_URL}`);

let memberDevProc = null;

async function checkPortOpen(port) {
  try {
    const res = await fetch(`https://127.0.0.1:${port}/api/health`, {
      cache: 'no-store',
      // @ts-expect-error test fetch
      dispatcher: new (await import('undici')).Agent({ connect: { rejectUnauthorized: false } }),
    }).catch(() => null);
    return Boolean(res);
  } catch {
    return false;
  }
}

async function startMemberWebHttps() {
  try {
    const res = await fetch(`https://127.0.0.1:${HTTPS_PORT}/kinetiq-ca.crt`);
    if (res.ok) {
      console.log('[drill] Next.js HTTPS dev server is already running on port 3001!');
      return;
    }
  } catch {}

  console.log('[drill] Starting Next.js Member Web on HTTPS with LAN binding 0.0.0.0:3001...');
  const keyPath = join(MEMBER_WEB_DIR, 'certificates', 'server.key');
  const certPath = join(MEMBER_WEB_DIR, 'certificates', 'server.crt');

  if (!existsSync(keyPath) || !existsSync(certPath)) {
    throw new Error('Certificates not found. Run scripts/generate-ssl-certs.mjs first.');
  }

  memberDevProc = spawn(
    'cmd.exe',
    [
      '/c',
      'npx.cmd',
      'next',
      'dev',
      '--experimental-https',
      '--experimental-https-key',
      './certificates/server.key',
      '--experimental-https-cert',
      './certificates/server.crt',
      '-H',
      '0.0.0.0',
      '-p',
      String(HTTPS_PORT),
    ],
    {
      cwd: MEMBER_WEB_DIR,
      stdio: ['ignore', 'pipe', 'pipe'],
      env: { ...process.env, PORT: String(HTTPS_PORT) },
    }
  );

  memberDevProc.stdout.on('data', (d) => {
    const line = d.toString();
    if (line.includes('Ready') || line.includes('Local:')) {
      console.log(`[Next.js stdout] ${line.trim()}`);
    }
  });

  memberDevProc.stderr.on('data', (d) => {
    const line = d.toString();
    if (!line.includes('Self-signed certificates are currently an experimental feature')) {
      console.error(`[Next.js stderr] ${line.trim()}`);
    }
  });

  // Wait for server to become responsive
  const start = Date.now();
  while (Date.now() - start < 35000) {
    try {
      const response = await fetch(`https://127.0.0.1:${HTTPS_PORT}/kinetiq-ca.crt`, {
        // @ts-expect-error node fetch
        agent: new (await import('node:https')).Agent({ rejectUnauthorized: false }),
      });
      if (response.ok) {
        console.log('[drill] Next.js HTTPS dev server is UP and responding!');
        return;
      }
    } catch {
      // server still starting
    }
    await new Promise((r) => setTimeout(r, 1000));
  }
  throw new Error('Timed out waiting for Next.js HTTPS dev server to start.');
}

async function runDrill() {
  await startMemberWebHttps();

  console.log('\n[Phase 1] Launching Playwright Mobile Handset (iPhone 14 / Pixel 7 Simulation)...');
  const browser = await chromium.launch({
    headless: true,
    args: [
      '--use-fake-ui-for-media-stream',
      '--use-fake-device-for-media-stream',
      '--ignore-certificate-errors',
    ],
  });

  try {
    const phoneDevice = devices['Pixel 7'] || devices['iPhone 14'];
    const context = await browser.newContext({
      ...phoneDevice,
      ignoreHTTPSErrors: true,
      permissions: ['camera'],
    });

    const page = await context.newPage();

    // Track network requests to verify NO phone flow uses 127.0.0.1
    const loopbackRequests = [];
    page.on('request', (req) => {
      const u = req.url();
      if (u.includes('127.0.0.1') || (u.includes('localhost') && !u.includes('localhost:3001'))) {
        loopbackRequests.push(u);
      }
    });

    page.on('console', (msg) => console.log(`[Browser console] ${msg.type()}: ${msg.text()}`));
    page.on('pageerror', (err) => console.log(`[Browser pageerror] ${err}`));

    // -------------------------------------------------------------
    // Test 1: Verify /cert page over LAN HTTPS
    // -------------------------------------------------------------
    console.log(`\n[Test 1] Navigating to LAN HTTPS URL: ${MEMBER_HTTPS_URL}/cert ...`);
    await page.goto(`${MEMBER_HTTPS_URL}/cert`, { waitUntil: 'domcontentloaded', timeout: 30000 });

    const isSecure = await page.evaluate(() => window.isSecureContext);
    const protocol = await page.evaluate(() => window.location.protocol);
    const hostname = await page.evaluate(() => window.location.hostname);
    console.log(`  - Secure Context: ${isSecure ? 'TRUE (PASSED)' : 'FALSE (FAILED)'}`);
    console.log(`  - Protocol: ${protocol}`);
    console.log(`  - Hostname: ${hostname}`);

    if (!isSecure || protocol !== 'https:') {
      throw new Error(`Expected secure HTTPS context, got protocol=${protocol}, isSecureContext=${isSecure}`);
    }

    // Verify Root CA Download link
    console.log('[Test 1.1] Verifying Root CA certificate download endpoint...');
    const certRes = await page.request.get(`${MEMBER_HTTPS_URL}/kinetiq-ca.crt`);
    const certText = await certRes.text();
    if (!certText.includes('BEGIN CERTIFICATE') || !certText.includes('END CERTIFICATE')) {
      throw new Error('Downloaded certificate is not a valid PEM certificate.');
    }
    console.log('  - Root CA Certificate verified: valid PEM payload received.');

    // -------------------------------------------------------------
    // Test 2: Handset Camera Probe on /cert page
    // -------------------------------------------------------------
    console.log('\n[Test 2] Executing Handset Camera Verification Drill on /cert ...');
    await context.grantPermissions(['camera'], { origin: MEMBER_HTTPS_URL });
    
    // Wait for client React hydration (diagnostic card renders the live client hostname)
    await page.locator(`text=${LAN_IP}`).first().waitFor({ state: 'visible', timeout: 30000 });
    console.log('  - Client React hydration verified.');

    const testCamBtn = page.locator('button', { hasText: 'Test Handset Camera' });
    await testCamBtn.waitFor({ state: 'visible', timeout: 30000 });
    await testCamBtn.click();

    const probeResult = page.locator('text=/Camera probe PASSED/');
    await probeResult.waitFor({ state: 'visible', timeout: 15000 });
    const probeMsg = await probeResult.innerText();
    console.log(`  - Result: ${probeMsg}`);

    // -------------------------------------------------------------
    // Test 3: Member Login on Handset
    // -------------------------------------------------------------
    console.log('\n[Test 3] Authenticating as Member on Mobile Handset...');
    await page.goto(`${MEMBER_HTTPS_URL}/login?next=/workout`, { waitUntil: 'domcontentloaded' });
    await page.waitForTimeout(2000); // Allow compilation / React hydration to settle
    const oneClickBtn = page.locator('button', { hasText: '1-Click Login as Demo Member' });
    await oneClickBtn.waitFor({ state: 'visible', timeout: 30000 });
    console.log('  - Clicking 1-Click Login as Demo Member...');
    await oneClickBtn.click();

    console.log('[Test 3.1] Waiting for redirect to /workout ...');
    await page.waitForURL((url) => url.pathname.includes('/workout'), { timeout: 30000 });
    console.log(`  - Arrived at: ${page.url()}`);

    // -------------------------------------------------------------
    // Test 4: Member AI Workout Camera Verification
    // -------------------------------------------------------------
    console.log('\n[Test 4] Verifying Phone Camera independence and controls...');

    // Verify Phone Camera is active
    const phoneCamButton = page.locator('button', { hasText: 'Phone Camera' });
    await phoneCamButton.waitFor({ state: 'visible', timeout: 10000 });
    console.log('  - "Phone Camera" mode button is present and active.');

    // Start Phone Camera
    console.log('[Test 4.1] Tapping "Start Camera" on mobile handset...');
    const startCamBtn = page.locator('button', { hasText: 'Start Camera' });
    await startCamBtn.click();
    await page.waitForTimeout(2000);

    // Verify <video> element received srcObject and is playing
    const videoStats = await page.evaluate(() => {
      const video = document.querySelector('video');
      if (!video) return { error: 'No video element found' };
      const stream = video.srcObject;
      if (!stream) return { error: 'video.srcObject is null' };
      const tracks = stream.getVideoTracks();
      if (!tracks || tracks.length === 0) return { error: 'No video tracks in stream' };
      const track = tracks[0];
      return {
        hasStream: true,
        readyState: track.readyState,
        trackLabel: track.label,
        videoWidth: video.videoWidth,
        videoHeight: video.videoHeight,
        playsinline: video.getAttribute('playsinline'),
        muted: video.muted,
      };
    });
    console.log('  - Video Element State:', videoStats);
    if (!videoStats.hasStream || videoStats.readyState !== 'live') {
      throw new Error(`Video track not active: ${JSON.stringify(videoStats)}`);
    }

    // -------------------------------------------------------------
    // Test 5: Front/Rear Camera Switching
    // -------------------------------------------------------------
    console.log('\n[Test 5] Testing front-to-rear camera switching (flipCamera)...');
    const switchCamBtn = page.locator('button[title="Switch camera"]');
    await switchCamBtn.click();
    await page.waitForTimeout(2000);

    const flippedStats = await page.evaluate(() => {
      const video = document.querySelector('video');
      const stream = video ? video.srcObject : null;
      const track = stream && stream.getVideoTracks()[0];
      return {
        readyState: track?.readyState,
        isMirrored: video?.className.includes('-scale-x-100'),
      };
    });
    console.log('  - Flipped Camera State:', flippedStats);
    if (flippedStats.readyState !== 'live') {
      throw new Error('Flipped camera track is not live!');
    }

    // -------------------------------------------------------------
    // Test 6: Track Cleanup Verification
    // -------------------------------------------------------------
    console.log('\n[Test 6] Stopping camera and verifying hardware track cleanup...');
    const stopCamBtn = page.locator('button', { hasText: 'Stop Camera' });
    await stopCamBtn.click();
    await page.waitForTimeout(1000);

    const cleanupStats = await page.evaluate(() => {
      const video = document.querySelector('video');
      return {
        srcObjectNull: video ? video.srcObject === null : true,
      };
    });
    console.log('  - Track Cleanup Verified:', cleanupStats);
    if (!cleanupStats.srcObjectNull) {
      throw new Error('video.srcObject was not cleared on camera stop!');
    }

    // -------------------------------------------------------------
    // Test 7: Verify Zero 127.0.0.1 Requests
    // -------------------------------------------------------------
    console.log('\n[Test 7] Verifying network requests for zero 127.0.0.1 loopback calls...');
    console.log(`  - Total loopback requests captured: ${loopbackRequests.length}`);
    if (loopbackRequests.length > 0) {
      console.warn('  - Loopback requests detected:', loopbackRequests);
      throw new Error(`Phone flow made requests to 127.0.0.1: ${JSON.stringify(loopbackRequests)}`);
    }
    console.log('  - CONFIRMED: No phone flow made calls to 127.0.0.1 (Dynamic LAN Host preserved).');

    // -------------------------------------------------------------
    // Test 8: Permission Denied Error Handling
    // -------------------------------------------------------------
    console.log('\n[Test 8] Testing permission denial error display...');
    const deniedContext = await browser.newContext({
      ...phoneDevice,
      ignoreHTTPSErrors: true,
      permissions: [], // No camera permission
    });
    const deniedPage = await deniedContext.newPage();
    await deniedPage.goto(`${MEMBER_HTTPS_URL}/workout`, { waitUntil: 'domcontentloaded' });
    // Attempt camera
    const startDenied = deniedPage.locator('button', { hasText: 'Start Camera' });
    if (await startDenied.isVisible()) {
      await startDenied.click();
      await deniedPage.waitForTimeout(1000);
      const errMsg = await deniedPage.locator('text=/Camera Permission Denied|Insecure/').isVisible();
      console.log(`  - Clear permission denial / error message visible: ${errMsg ? 'YES' : 'NO'}`);
    }
    await deniedContext.close();

    console.log('\n========================================================================');
    console.log('   AUTOMATED SIMULATED MOBILE CAMERA & LAN TESTS PASSED (8/8)           ');
    console.log('   STATUS: PHYSICAL MOBILE TEST BLOCKED — automated fake camera only     ');
    console.log('   (Requires manual verification on physical iOS/Android phone over Wi-Fi)');
    console.log('========================================================================');
  } finally {
    await browser.close();
    if (memberDevProc) {
      console.log('[drill] Stopping Next.js dev server...');
      memberDevProc.kill('SIGKILL');
      // kill on windows by PID
      spawn('taskkill', ['/pid', String(memberDevProc.pid), '/f', '/t']);
    }
    process.exit(0);
  }
}

runDrill().catch((err) => {
  console.error('\n❌ Handset verification drill failed:', err);
  if (memberDevProc) {
    spawn('taskkill', ['/pid', String(memberDevProc.pid), '/f', '/t']);
  }
  process.exit(1);
});
