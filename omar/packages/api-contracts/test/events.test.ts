import { describe, expect, it } from 'vitest';
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, join } from 'node:path';
import { createHash } from 'node:crypto';
import { EdgeEvent, EventBatch, EDGE_EVENT_TYPES, canonicalBatchString, StreamUrlWithoutCredentials } from '../src/index';

const here = dirname(fileURLToPath(import.meta.url));
const fixtures = JSON.parse(readFileSync(join(here, '..', 'fixtures', 'events_v1_valid.json'), 'utf8')) as unknown[];

describe('events.v1 contract', () => {
  it('accepts every shared fixture (same fixtures are validated by the Python mirror)', () => {
    for (const ev of fixtures) {
      const result = EdgeEvent.safeParse(ev);
      expect(result.success, JSON.stringify(result.success ? null : result.error.format())).toBe(true);
    }
  });

  it('covers every declared event type at least once in fixtures', () => {
    const seen = new Set(fixtures.map((e) => (e as { event_type: string }).event_type));
    const missing = EDGE_EVENT_TYPES.filter((t) => !seen.has(t));
    // The remaining lifecycle events are exercised explicitly below.
    expect(missing).toEqual(['camera.status', 'track.ended', 'equipment.usage.ended', 'exercise.activity.ended', 'biometric.revoked']);
  });

  it('rejects an unknown schema_version', () => {
    const ev = { ...(fixtures[0] as object), schema_version: '2' };
    expect(EdgeEvent.safeParse(ev).success).toBe(false);
  });

  it('rejects rep.counted with a non-null quality_label (Phase 9 feature not yet allowed)', () => {
    const rep = fixtures.find((e) => (e as { event_type: string }).event_type === 'rep.counted') as Record<string, unknown>;
    const bad = { ...rep, payload: { ...(rep.payload as object), quality_label: 'correct' } };
    expect(EdgeEvent.safeParse(bad).success).toBe(false);
  });

  it('rejects confidence outside [0,1]', () => {
    const checkin = fixtures.find((e) => (e as { event_type: string }).event_type === 'identity.checkin') as Record<string, unknown>;
    const bad = { ...checkin, payload: { ...(checkin.payload as object), confidence: 1.2 } };
    expect(EdgeEvent.safeParse(bad).success).toBe(false);
  });

  it('accepts the remaining lifecycle events', () => {
    const base = fixtures[0] as Record<string, unknown>;
    const events = [
      { ...base, event_type: 'camera.status', payload: { camera_id: '44444444-4444-4444-8444-444444444444', status: 'reconnecting', fps_measured: null, resolution_w: null, resolution_h: null, error: 'timeout' } },
      { ...base, event_type: 'track.ended', payload: { global_track_id: 'g-1', reason: 'left_view', last_camera_id: null } },
      { ...base, event_type: 'equipment.usage.ended', payload: { usage_local_id: 'u-1', reason: 'left_zone' } },
      { ...base, event_type: 'exercise.activity.ended', payload: { activity_local_id: 'a-1', active_seconds: 42.5 } },
      { ...base, event_type: 'biometric.revoked', payload: { member_id: '33333333-3333-4333-8333-333333333333', template_ref: 'tpl-0123456789abcdef0123', reason: 'consent_withdrawn' } },
    ];
    for (const ev of events) expect(EdgeEvent.safeParse(ev).success, JSON.stringify(ev)).toBe(true);
  });

  it('validates a full batch and bounds its size', () => {
    const batch = {
      schema_version: '1',
      batch_id: '99999999-9999-4999-8999-999999999999',
      device_id: '11111111-1111-4111-8111-111111111111',
      sent_at: '2026-10-27T09:00:02.000Z',
      events: fixtures,
    };
    expect(EventBatch.safeParse(batch).success).toBe(true);
    expect(EventBatch.safeParse({ ...batch, events: [] }).success).toBe(false);
  });

  it('builds a deterministic canonical signing string', () => {
    const eventsHash = createHash('sha256').update(JSON.stringify(fixtures)).digest('hex');
    const s = canonicalBatchString(
      { device_id: 'd', batch_id: 'b', sent_at: 't' },
      eventsHash,
    );
    expect(s).toBe(`d\nb\nt\n${eventsHash}`);
  });
});

describe('StreamUrlWithoutCredentials', () => {
  it('accepts rtsp URLs without credentials', () => {
    expect(StreamUrlWithoutCredentials.safeParse('rtsp://192.168.10.21:554/Streaming/Channels/101').success).toBe(true);
  });
  it('rejects embedded credentials', () => {
    expect(StreamUrlWithoutCredentials.safeParse('rtsp://admin:secret@192.168.10.21:554/live').success).toBe(false);
  });
  it('rejects non-stream protocols', () => {
    expect(StreamUrlWithoutCredentials.safeParse('ftp://192.168.10.21/live').success).toBe(false);
  });
});
