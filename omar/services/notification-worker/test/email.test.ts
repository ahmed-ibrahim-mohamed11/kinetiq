import { describe, expect, it } from 'vitest';
import { ResendEmailSender, renderEmailHtml } from '../src/email';

describe('email sender', () => {
  it('escapes HTML in the rendered template', () => {
    const html = renderEmailHtml('<b>Title</b>', 'Body & more', 'https://m.example/r?x=1&y=2');
    expect(html).toContain('&lt;b&gt;Title&lt;/b&gt;');
    expect(html).toContain('Body &amp; more');
    expect(html).toContain('href="https://m.example/r?x=1&amp;y=2"');
  });

  it('posts to the provider and fails explicitly without an address', async () => {
    const calls: Array<{ url: string; body: any }> = [];
    const fetchImpl = (async (url: string, init: RequestInit) => {
      calls.push({ url, body: JSON.parse(String(init.body)) });
      return new Response('{"id":"1"}', { status: 200 });
    }) as unknown as typeof fetch;
    const sender = new ResendEmailSender({ async memberEmail(id) { return id === 'm1' ? 'member@example.test' : null; } }, { apiKey: 'k', from: 'Gym <r@example.test>', fetchImpl });
    await sender.send('m1', 'Your workout report', 'Body', 'https://m.example/reports/1');
    expect(calls[0]!.url).toBe('https://api.resend.com/emails');
    expect(calls[0]!.body).toMatchObject({ to: ['member@example.test'], subject: 'Your workout report' });
    await expect(sender.send('m2', 's', 'b', 'u')).rejects.toThrow(/no email address/);
  });

  it('surfaces provider errors with the status code', async () => {
    const fetchImpl = (async () => new Response('rate limited', { status: 429 })) as unknown as typeof fetch;
    const sender = new ResendEmailSender({ async memberEmail() { return 'x@example.test'; } }, { apiKey: 'k', from: 'f', fetchImpl });
    await expect(sender.send('m1', 's', 'b', 'u')).rejects.toMatchObject({ statusCode: 429 });
  });
});
