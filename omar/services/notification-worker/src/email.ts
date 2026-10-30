/**
 * Email delivery for members without a push subscription.
 *
 * Implementation: Resend HTTP API (no SDK dependency). Any provider with a
 * JSON "send" endpoint can be swapped in behind `EmailSender`. The member's
 * address comes from `members.email`; members without an address fail the job
 * explicitly so the front desk can see it.
 */
import type { EmailSender } from './worker';

export interface EmailLookup {
  memberEmail(memberId: string): Promise<string | null>;
}

export interface ResendOptions {
  apiKey: string;
  from: string; // e.g. "Smart Gym <reports@gym.example>"
  endpoint?: string;
  fetchImpl?: typeof fetch;
}

export function renderEmailHtml(title: string, body: string, url: string): string {
  const esc = (s: string) => s.replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' })[c]!);
  return `<!doctype html><html><body style="font-family:system-ui,Segoe UI,Roboto,sans-serif;background:#f6f7f9;padding:24px">
<div style="max-width:520px;margin:0 auto;background:#fff;border-radius:12px;padding:28px;border:1px solid #e5e7eb">
<h1 style="font-size:20px;margin:0 0 12px">${esc(title)}</h1>
<p style="color:#374151;line-height:1.5">${esc(body)}</p>
<p style="margin:24px 0"><a href="${esc(url)}" style="background:#2563eb;color:#fff;text-decoration:none;padding:12px 18px;border-radius:8px;display:inline-block">Open your report</a></p>
<p style="color:#6b7280;font-size:12px">If the button does not work, copy this link: ${esc(url)}</p>
</div></body></html>`;
}

export class ResendEmailSender implements EmailSender {
  constructor(
    private readonly lookup: EmailLookup,
    private readonly opts: ResendOptions,
  ) {}

  async send(memberId: string, subject: string, body: string, url: string): Promise<void> {
    const to = await this.lookup.memberEmail(memberId);
    if (!to) throw new Error('member has no email address on file');
    const f = this.opts.fetchImpl ?? fetch;
    const res = await f(this.opts.endpoint ?? 'https://api.resend.com/emails', {
      method: 'POST',
      headers: { authorization: `Bearer ${this.opts.apiKey}`, 'content-type': 'application/json' },
      body: JSON.stringify({ from: this.opts.from, to: [to], subject, html: renderEmailHtml(subject, body, url), text: `${body}\n\n${url}` }),
    });
    if (!res.ok) {
      const text = await res.text().catch(() => '');
      throw Object.assign(new Error(`email provider ${res.status}: ${text.slice(0, 200)}`), { statusCode: res.status });
    }
  }
}
