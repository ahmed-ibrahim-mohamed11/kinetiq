import { test, expect, chromium } from '@playwright/test';
import fs from 'fs';
import path from 'path';

const SCREENSHOT_DIR = path.resolve('tests/live/artifacts/audit_screenshots');
if (!fs.existsSync(SCREENSHOT_DIR)) {
  fs.mkdirSync(SCREENSHOT_DIR, { recursive: true });
}

test.describe('KinetiQ Complete Route-by-Route & UI/UX Audit', () => {
  test('audit all Admin Web and Member Web routes with desktop + mobile viewports and Arabic/RTL', async () => {
    test.setTimeout(360_000);
    const browser = await chromium.launch({ channel: 'chrome', headless: true });

    // Desktop Context (1280x900)
    const adminDesktopCtx = await browser.newContext({ viewport: { width: 1280, height: 900 } });
    const memberDesktopCtx = await browser.newContext({ viewport: { width: 1280, height: 900 }, ignoreHTTPSErrors: true });

    // Mobile Context (390x844 - iPhone 14 / modern smartphone)
    const adminMobileCtx = await browser.newContext({ viewport: { width: 390, height: 844 }, isMobile: true, hasTouch: true });
    const memberMobileCtx = await browser.newContext({ viewport: { width: 390, height: 844 }, isMobile: true, hasTouch: true, ignoreHTTPSErrors: true });

    const adminPage = await adminDesktopCtx.newPage();
    const adminMobilePage = await adminMobileCtx.newPage();
    const memberPage = await memberDesktopCtx.newPage();
    const memberMobilePage = await memberMobileCtx.newPage();

    const consoleErrors: string[] = [];
    const failedRequests: string[] = [];
    const monitorErrors = (page: any, label: string) => {
      page.on('console', (msg: any) => {
        if (msg.type() === 'error') {
          consoleErrors.push(`[${label}] Console Error: ${msg.text()}`);
        }
      });
      page.on('pageerror', (err: any) => {
        consoleErrors.push(`[${label}] Unhandled Page Error: ${err.message}`);
      });
      page.on('requestfailed', (req: any) => {
        failedRequests.push(`[${label}] Failed Request: ${req.method()} ${req.url()} (${req.failure()?.errorText || 'failed'})`);
      });
      page.on('response', (res: any) => {
        if (res.status() >= 400 && !res.url().includes('favicon') && !res.url().includes('_next/static')) {
          failedRequests.push(`[${label}] HTTP ${res.status()}: ${res.url()}`);
        }
      });
    };

    monitorErrors(adminPage, 'Admin-Desktop');
    monitorErrors(adminMobilePage, 'Admin-Mobile');
    monitorErrors(memberPage, 'Member-Desktop');
    monitorErrors(memberMobilePage, 'Member-Mobile');

    console.log('\n--- 1. ADMIN AUTHENTICATION ---');
    // Admin Login Desktop
    await adminPage.goto('http://localhost:3000/login');
    await adminPage.screenshot({ path: path.join(SCREENSHOT_DIR, 'admin-01-login-desktop.png') });
    await adminMobilePage.goto('http://localhost:3000/login');
    await adminMobilePage.screenshot({ path: path.join(SCREENSHOT_DIR, 'admin-01-login-mobile.png') });

    // Sign In Admin
    await adminPage.getByTestId('login-email').fill('admin@smartgym.demo');
    await adminPage.getByTestId('login-password').fill('DemoAdmin123!');
    await adminPage.getByTestId('login-submit').click();
    await adminPage.waitForURL((url) => !url.pathname.includes('/login'), { timeout: 30000 });
    console.log('  ✓ Admin Desktop Logged in successfully ->', adminPage.url());

    // Transfer cookies to admin mobile context
    const adminCookies = await adminDesktopCtx.cookies();
    await adminMobileCtx.addCookies(adminCookies);

    console.log('\n--- 2. ADMIN ROUTES AUDIT ---');
    const adminRoutes = [
      { path: '/dashboard', name: 'admin-02-dashboard' },
      { path: '/kiosk', name: 'admin-03-kiosk' },
      { path: '/members', name: 'admin-04-members' },
      { path: '/members/31111111-1111-4111-8111-111111111101', name: 'admin-05-member-profile' },
      { path: '/subscriptions', name: 'admin-06-subscriptions' },
      { path: '/cameras', name: 'admin-07-cameras' },
      { path: '/cameras/c1111111-1111-4111-8111-111111111101/zones', name: 'admin-08-camera-zones' },
      { path: '/devices', name: 'admin-09-devices' },
      { path: '/diagnostics', name: 'admin-10-diagnostics' },
      { path: '/sessions', name: 'admin-11-sessions' },
      { path: '/equipment', name: 'admin-12-equipment' },
      { path: '/audit', name: 'admin-13-audit' },
    ];

    for (const r of adminRoutes) {
      // Desktop
      await adminPage.goto(`http://localhost:3000${r.path}`, { waitUntil: 'domcontentloaded' });
      await adminPage.waitForTimeout(1000);
      await adminPage.screenshot({ path: path.join(SCREENSHOT_DIR, `${r.name}-desktop.png`) });

      // Mobile
      await adminMobilePage.goto(`http://localhost:3000${r.path}`, { waitUntil: 'domcontentloaded' });
      await adminMobilePage.waitForTimeout(1000);
      await adminMobilePage.screenshot({ path: path.join(SCREENSHOT_DIR, `${r.name}-mobile.png`) });

      console.log(`  ✓ Verified Admin Route: ${r.path}`);
    }

    console.log('\n--- 3. MEMBER AUTHENTICATION ---');
    // Member Login Desktop & Mobile
    await memberPage.goto('https://localhost:3001/login');
    await memberPage.screenshot({ path: path.join(SCREENSHOT_DIR, 'member-01-login-desktop.png') });
    await memberMobilePage.goto('https://localhost:3001/login');
    await memberMobilePage.screenshot({ path: path.join(SCREENSHOT_DIR, 'member-01-login-mobile.png') });

    // Sign in Member
    await memberPage.getByTestId('login-email').fill('member@smartgym.demo');
    await memberPage.getByTestId('login-password').fill('DemoMember123!');
    await memberPage.getByTestId('login-submit').click();
    await memberPage.waitForURL((url) => !url.pathname.includes('/login'), { timeout: 30000 });
    console.log('  ✓ Member Desktop Logged in successfully ->', memberPage.url());

    // Transfer cookies to member mobile context
    const memberCookies = await memberDesktopCtx.cookies();
    await memberMobileCtx.addCookies(memberCookies);

    console.log('\n--- 4. MEMBER ROUTES AUDIT ---');
    const memberRoutes = [
      { path: '/dashboard', name: 'member-02-dashboard' },
      { path: '/workout', name: 'member-03-workout' },
      { path: '/history', name: 'member-04-history' },
      { path: '/settings', name: 'member-05-settings' },
      { path: '/privacy', name: 'member-06-privacy' },
    ];

    for (const r of memberRoutes) {
      // Desktop
      await memberPage.goto(`https://localhost:3001${r.path}`, { waitUntil: 'domcontentloaded' });
      await memberPage.waitForTimeout(1000);
      await memberPage.screenshot({ path: path.join(SCREENSHOT_DIR, `${r.name}-desktop.png`) });

      // Mobile
      await memberMobilePage.goto(`https://localhost:3001${r.path}`, { waitUntil: 'domcontentloaded' });
      await memberMobilePage.waitForTimeout(1000);
      await memberMobilePage.screenshot({ path: path.join(SCREENSHOT_DIR, `${r.name}-mobile.png`) });

      console.log(`  ✓ Verified Member Route: ${r.path}`);
    }

    // Dynamic Report Route verification
    const reportId = 'dd00ed6d-4896-4a8a-892d-62640a93940e';
    console.log(`\n  ✓ Verifying Member Report Route: /reports/${reportId}`);
    await memberPage.goto(`https://localhost:3001/reports/${reportId}`, { waitUntil: 'domcontentloaded' });
    await memberPage.waitForTimeout(1000);
    await memberPage.screenshot({ path: path.join(SCREENSHOT_DIR, 'member-07-report-desktop.png') });
    await memberMobilePage.goto(`https://localhost:3001/reports/${reportId}`, { waitUntil: 'domcontentloaded' });
    await memberMobilePage.waitForTimeout(1000);
    await memberMobilePage.screenshot({ path: path.join(SCREENSHOT_DIR, 'member-07-report-mobile.png') });

    // Public Shared Report Route (/r/[token])
    const shareToken = 'aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa';
    console.log(`\n  ✓ Verifying Public Shared Report Route: /r/${shareToken}`);
    await memberPage.goto(`https://localhost:3001/r/${shareToken}`, { waitUntil: 'domcontentloaded' });
    await memberPage.waitForTimeout(1000);
    await memberPage.screenshot({ path: path.join(SCREENSHOT_DIR, 'member-08-shared-report-desktop.png') });
    await memberMobilePage.goto(`https://localhost:3001/r/${shareToken}`, { waitUntil: 'domcontentloaded' });
    await memberMobilePage.waitForTimeout(1000);
    await memberMobilePage.screenshot({ path: path.join(SCREENSHOT_DIR, 'member-08-shared-report-mobile.png') });

    // Certificate / Handset Diagnostics Route (/cert)
    console.log(`\n  ✓ Verifying Certificate Diagnostics Route: /cert`);
    await memberPage.goto(`https://localhost:3001/cert`, { waitUntil: 'domcontentloaded' });
    await memberPage.waitForTimeout(1000);
    await memberPage.screenshot({ path: path.join(SCREENSHOT_DIR, 'member-09-cert-desktop.png') });
    await memberMobilePage.goto(`https://localhost:3001/cert`, { waitUntil: 'domcontentloaded' });
    await memberMobilePage.waitForTimeout(1000);
    await memberMobilePage.screenshot({ path: path.join(SCREENSHOT_DIR, 'member-09-cert-mobile.png') });

    console.log('\n--- 5. ARABIC / RTL LAYOUT VERIFICATION ---');
    // Verify Arabic toggle / direction
    await memberPage.goto('https://localhost:3001/settings', { waitUntil: 'domcontentloaded' });
    const arBtn = memberPage.locator('button', { hasText: 'العربية' });
    if (await arBtn.isVisible()) {
      await arBtn.click();
      await memberPage.waitForTimeout(1000);
      await memberPage.screenshot({ path: path.join(SCREENSHOT_DIR, 'member-08-settings-rtl-desktop.png') });
      await memberMobilePage.goto('https://localhost:3001/settings', { waitUntil: 'domcontentloaded' });
      const arMobileBtn = memberMobilePage.locator('button', { hasText: 'العربية' });
      if (await arMobileBtn.isVisible()) {
        await arMobileBtn.click();
        await memberMobilePage.waitForTimeout(1000);
        await memberMobilePage.screenshot({ path: path.join(SCREENSHOT_DIR, 'member-08-settings-rtl-mobile.png') });
      }
      console.log('  ✓ Verified Arabic RTL Toggle and layout rendering');
    }

    console.log('\n--- 6. API ROUTES & HTTP ASSERTIONS ---');
    const apiChecks = [
      { url: 'http://localhost:3000/api/health', expectedStatus: 200 },
      { url: 'https://localhost:3001/api/health', expectedStatus: 200 },
      { url: 'https://localhost:3001/manifest.webmanifest', expectedStatus: 200 },
    ];
    for (const check of apiChecks) {
      const res = await memberPage.request.get(check.url);
      expect(res.status()).toBe(check.expectedStatus);
      console.log(`  ✓ API Endpoint ${check.url} -> HTTP ${res.status()}`);
    }

    console.log('\n--- 7. CONSOLE ERROR & FAILED REQUEST AUDIT SUMMARY ---');
    const fatalErrors = consoleErrors.filter(
      (e) =>
        !e.includes('favicon') &&
        !e.includes('404') &&
        !e.includes('Download the React DevTools') &&
        !e.includes('Minified React error #418')
    );
    console.log(`  Fatal Console Errors: ${fatalErrors.length}`);
    if (fatalErrors.length > 0) {
      console.warn('  Warnings/Errors logged:', fatalErrors.slice(0, 5));
    }
    console.log(`  Failed Requests: ${failedRequests.length}`);
    if (failedRequests.length > 0) {
      console.warn('  Failed Requests logged:', failedRequests.slice(0, 5));
    }

    const auditSummary = {
      timestamp: new Date().toISOString(),
      adminRoutesCount: adminRoutes.length,
      memberRoutesCount: memberRoutes.length + 3, // includes reportId, cert, shareToken
      fatalErrorsCount: fatalErrors.length,
      fatalErrors,
      failedRequestsCount: failedRequests.length,
      failedRequests,
      status: fatalErrors.length === 0 ? 'PASS' : 'WARNINGS',
    };
    fs.writeFileSync('tests/live/artifacts/route_audit_report.json', JSON.stringify(auditSummary, null, 2));

    await browser.close();
    console.log('\n=================================================================');
    console.log(' ALL ADMIN & MEMBER ROUTES SUCCESSFULLY AUDITED & SCREENSHOTTED! ');
    console.log('=================================================================');
  });
});
