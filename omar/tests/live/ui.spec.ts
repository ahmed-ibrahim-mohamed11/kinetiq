/**
 * Deployed apps, real browser (installed Chrome), QA tenant from ui.global-setup.ts:
 *   staff signs in on the admin app → /kiosk
 *   member signs in on the member app → reads the text code under the QR (qr-token-text)
 *   staff types it into #manual-token → kiosk-result shows success
 *   member dashboard shows active-session → check-out-button → report page shows report-total-reps
 * The report notification of this session is pre-empted with a terminal job (see the kiosk step),
 * so the dispatcher never e-mails the reserved qa.smartgym.test address.
 * A screenshot is saved at every step under tests/live/artifacts/ui/steps.
 */
import { expect, test, type Page } from '@playwright/test';
import { artifactPath } from './lib/artifacts.ts';
import { liveEnv } from './lib/env.ts';
import { poll, QR_TOKEN_RE } from './lib/functions.ts';
import { serviceClient, type QaTenant } from './lib/qa-tenant.ts';
import { UI_TENANT_ENV } from './ui.global-setup.ts';

function tenantFromEnv(): QaTenant {
  const raw = process.env[UI_TENANT_ENV];
  if (!raw) throw new Error(`${UI_TENANT_ENV} is not set: run through playwright.config.ts (global setup creates the QA tenant)`);
  return JSON.parse(raw) as QaTenant;
}

let shot = 0;
async function snap(page: Page, name: string): Promise<void> {
  shot += 1;
  await page.screenshot({ path: artifactPath('ui', 'steps', `${String(shot).padStart(2, '0')}-${name}.png`), fullPage: true });
}

async function signIn(page: Page, baseUrl: string, path: string, email: string, password: string): Promise<void> {
  await page.goto(`${baseUrl}/login?next=${encodeURIComponent(path)}`);
  const emailInput = page.getByTestId('login-email').or(page.locator('input[type="email"], #email'));
  await emailInput.first().fill(email);
  const passInput = page.getByTestId('login-password').or(page.locator('input[type="password"], #password'));
  await passInput.first().fill(password);
  const submitBtn = page.getByTestId('login-submit').or(page.locator('button[type="submit"]'));
  await submitBtn.first().click();
  await page.waitForURL((url) => url.pathname.startsWith(path), { timeout: 60_000 });
}

test('kiosk QR check-in, live session, app check-out and report on the deployed apps', async ({ browser }) => {
  const env = liveEnv();
  const tenant = tenantFromEnv();
  const db = serviceClient();

  const staffCtx = await browser.newContext();
  const memberCtx = await browser.newContext({ viewport: { width: 420, height: 900 }, ignoreHTTPSErrors: true });
  const staffPage = await staffCtx.newPage();
  const memberPage = await memberCtx.newPage();

  try {
    await test.step('staff signs in on the admin app and opens /kiosk', async () => {
      await signIn(staffPage, env.adminAppUrl, '/kiosk', tenant.staff.email, tenant.staff.password);
      const qrTab = staffPage.locator('button', { hasText: 'QR / Token' });
      await qrTab.click();
      await expect(staffPage.locator('#manual-token')).toBeVisible();
      await snap(staffPage, 'admin-kiosk');
    });

    const token = await test.step('member signs in and reads the text code under the QR', async () => {
      await signIn(memberPage, env.memberAppUrl, '/dashboard', tenant.member.email, tenant.member.password);
      const text = memberPage.getByTestId('qr-token-text');
      const tokenInText = new RegExp(QR_TOKEN_RE.source.replace(/^\^|\$$/g, ''));
      await expect(text).toHaveText(tokenInText, { timeout: 60_000 });
      await snap(memberPage, 'member-qr');
      return tokenInText.exec(await text.innerText())?.[0] ?? '';
    });
    expect(QR_TOKEN_RE.exec(token)?.[1]).toBe(tenant.member.code.toUpperCase());

    await test.step('staff types the code into the kiosk and submits', async () => {
      await staffPage.locator('#manual-token').fill(token);
      await staffPage.locator('#manual-token').press('Enter');
      const result = staffPage.getByTestId('kiosk-result');
      await expect(result).toBeVisible({ timeout: 60_000 });
      await expect(result).toContainText(/checked in/i);
      await expect(result).not.toContainText(/expired|invalid|malformed|not authori[sz]ed|failed/i);
      await snap(staffPage, 'kiosk-result');
      const { data } = await db
        .from('attendance_sessions')
        .select('id, status, check_in_method, checked_in_by')
        .eq('member_id', tenant.member.id)
        .eq('status', 'active')
        .maybeSingle();
      expect(data).toMatchObject({ status: 'active', check_in_method: 'qr', checked_in_by: tenant.staff.userId });

      // The UI member has no push subscription, so finalize_attendance_session would queue an
      // 'email' workout_report_ready job that the every-minute dispatcher could send (and bounce)
      // to the reserved qa.smartgym.test address. Occupy its dedupe key with a terminal ('dead')
      // job first: finalize's `on conflict (dedupe_key) do nothing` then queues nothing claimable.
      const sessionId = String(data?.id ?? '');
      expect(sessionId).toMatch(/^[0-9a-f-]{36}$/);
      const { error: suppressError } = await db.from('notification_jobs').insert({
        member_id: tenant.member.id,
        channel: 'email',
        template: 'workout_report_ready',
        payload: { attendance_session_id: sessionId, qa_suppressed: true },
        dedupe_key: `workout_report_ready:${sessionId}`,
        status: 'dead',
        last_error: 'QA UI run: e-mail to the reserved .test domain suppressed',
      });
      expect(suppressError?.message ?? null).toBeNull();
    });

    await test.step('member dashboard shows the active session', async () => {
      await expect(async () => {
        await memberPage.reload();
        await expect(memberPage.getByTestId('active-session')).toBeVisible({ timeout: 5_000 });
      }).toPass({ timeout: 60_000, intervals: [2_000, 3_000, 5_000] });
      await snap(memberPage, 'member-active-session');
    });

    const reportId = await test.step('member checks out', async () => {
      memberPage.once('dialog', (d) => void d.accept());
      await memberPage.getByTestId('check-out-button').click();
      const report = await poll(
        'workout report after check-out',
        async () => {
          const { data } = await db.from('workout_reports').select('id').eq('member_id', tenant.member.id).maybeSingle();
          return data?.id ? String(data.id) : null;
        },
        90_000,
      );
      await memberPage.waitForURL(/\/reports\/[0-9a-f-]{36}/, { timeout: 60_000 }).catch(() => undefined);
      await snap(memberPage, 'member-after-checkout');
      return report;
    });

    await test.step('member opens the report', async () => {
      if (!memberPage.url().includes(`/reports/${reportId}`)) await memberPage.goto(`${env.memberAppUrl}/reports/${reportId}`);
      await expect(memberPage.getByTestId('report-total-reps')).toBeVisible({ timeout: 60_000 });
      await snap(memberPage, 'member-report');
      const { data: s } = await db.from('attendance_sessions').select('status').eq('member_id', tenant.member.id).order('check_in_at', { ascending: false }).limit(1).single();
      expect(s?.status).toBe('completed');
    });
  } finally {
    await staffCtx.close();
    await memberCtx.close();
  }
});
