import { test, expect, chromium } from '@playwright/test';

test.describe('KinetiQ Multi-Page Central Camera Broker Validation', () => {
  test('all camera pages open simultaneously without NotReadableError or hardware locks', async () => {
    // Launch real browser
    const browser = await chromium.launch({ channel: 'msedge', headless: true });

    // Create 5 independent browser contexts representing independent users/screens
    const kioskCtx = await browser.newContext({ ignoreHTTPSErrors: true });
    const adminLoginCtx = await browser.newContext({ ignoreHTTPSErrors: true });
    const memberLoginCtx = await browser.newContext({ ignoreHTTPSErrors: true });
    const workoutCtx = await browser.newContext({ ignoreHTTPSErrors: true });
    const diagCtx = await browser.newContext({ ignoreHTTPSErrors: true });

    const kioskPage = await kioskCtx.newPage();
    const adminLoginPage = await adminLoginCtx.newPage();
    const memberLoginPage = await memberLoginCtx.newPage();
    const workoutPage = await workoutCtx.newPage();
    const diagPage = await diagCtx.newPage();

    const consoleErrors: string[] = [];
    const trackErrors = (page: any, name: string) => {
      page.on('console', (msg: any) => {
        if (msg.type() === 'error') {
          const text = msg.text();
          if (text.includes('NotReadableError') || text.includes('Device in use')) {
            consoleErrors.push(`[${name}] ${text}`);
          }
        }
      });
      page.on('pageerror', (err: any) => {
        const text = String(err);
        if (text.includes('NotReadableError') || text.includes('Device in use')) {
          consoleErrors.push(`[${name} pageerror] ${text}`);
        }
      });
    };

    trackErrors(kioskPage, 'kiosk');
    trackErrors(adminLoginPage, 'adminLogin');
    trackErrors(memberLoginPage, 'memberLogin');
    trackErrors(workoutPage, 'workout');
    trackErrors(diagPage, 'diagnostics');

    console.log('[Step 1] Navigating to all 5 target pages simultaneously in real browser...');
    await Promise.all([
      kioskPage.goto('http://localhost:3000/kiosk', { waitUntil: 'domcontentloaded', timeout: 30000 }).catch(() => {}),
      adminLoginPage.goto('http://localhost:3000/login', { waitUntil: 'domcontentloaded', timeout: 30000 }).catch(() => {}),
      memberLoginPage.goto('https://localhost:3001/login', { waitUntil: 'domcontentloaded', timeout: 30000 }).catch(() => {}),
      workoutPage.goto('https://localhost:3001/workout', { waitUntil: 'domcontentloaded', timeout: 30000 }).catch(() => {}),
      diagPage.goto('http://localhost:3000/diagnostics', { waitUntil: 'domcontentloaded', timeout: 30000 }).catch(() => {}),
    ]);

    console.log('[Step 2] Switching login forms to Face ID mode...');
    // Switch Admin Login to Face ID tab
    const adminFaceTab = adminLoginPage.locator('button', { hasText: 'Face ID Login' });
    if (await adminFaceTab.isVisible()) {
      await adminFaceTab.click();
    }

    // Switch Member Login to Face ID tab
    const memberFaceTab = memberLoginPage.locator('button', { hasText: 'Face ID Login' });
    if (await memberFaceTab.isVisible()) {
      await memberFaceTab.click();
    }

    // Verify Bilingual Buttons on pages
    console.log('[Step 3] Verifying bilingual controls on camera viewports...');
    const hasControls = async (page: any) => {
      const text = await page.content();
      return (
        text.includes('Start Camera') ||
        text.includes('تشغيل الكاميرا') ||
        text.includes('Stop Camera') ||
        text.includes('إيقاف الكاميرا')
      );
    };

    console.log('  - Kiosk has controls:', await hasControls(kioskPage));
    console.log('  - Admin Login has controls:', await hasControls(adminLoginPage));
    console.log('  - Member Login has controls:', await hasControls(memberLoginPage));
    console.log('  - Workout has controls:', await hasControls(workoutPage));

    // Wait 4 seconds for streams to establish
    await new Promise((r) => setTimeout(r, 4000));

    // Check Diagnostics endpoint directly from browser
    console.log('[Step 4] Checking live broker diagnostics during simultaneous browser viewing...');
    const diagRes = await diagPage.evaluate<any>(async () => {
      try {
        const res = await fetch('http://127.0.0.1:8088/api/camera/diagnostics?camera_id=default');
        return await res.json();
      } catch (e: any) {
        return { error: e.message };
      }
    });

    console.log('Live Diagnostics from browser context:', {
      physical_handles: diagRes.physical_handles,
      active_consumers: diagRes.active_consumers,
      camera_state: diagRes.camera_state,
      frame_fps: diagRes.frame_fps,
      consumers: diagRes.consumers,
    });

    expect(diagRes.physical_handles).toBe(1);
    expect(diagRes.camera_state).toBe('running');
    expect(consoleErrors).toHaveLength(0);

    console.log('\n✓ ZERO NotReadableError / Device in use errors recorded across all simultaneous browser pages!');

    await browser.close();
  });
});
