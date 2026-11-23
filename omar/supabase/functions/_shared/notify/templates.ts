/**
 * Notification templates — single source of truth for the Supabase Edge Function
 * (notification-dispatch) and the self-hosted Node worker (services/notification-worker).
 * Runtime-neutral: no Node or Deno APIs, no npm imports.
 */

const UUID_RE = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

export interface WorkoutReportReadyPayload {
  report_id: string;
  attendance_session_id: string;
}

/** Validates a workout_report_ready payload before any delivery attempt (throws on malformed input). */
export function parseWorkoutReportReadyPayload(payload: unknown): WorkoutReportReadyPayload {
  const p = (payload ?? {}) as Record<string, unknown>;
  for (const key of ['report_id', 'attendance_session_id'] as const) {
    const v = p[key];
    if (typeof v !== 'string' || !UUID_RE.test(v)) {
      throw new Error(`invalid workout_report_ready payload: ${key} must be a uuid`);
    }
  }
  return { report_id: p.report_id as string, attendance_session_id: p.attendance_session_id as string };
}

export interface RenderedNotification {
  title: string;
  body: string;
  url: string;
  tag: string;
}

export function render(template: string, payload: unknown, memberName: string, memberAppUrl: string): RenderedNotification {
  switch (template) {
    case 'workout_report_ready': {
      const p = parseWorkoutReportReadyPayload(payload);
      const firstName = memberName.trim().split(/\s+/)[0] || 'Hi';
      return {
        title: 'Your workout report is ready',
        body: `${firstName}, today's session summary is available.`,
        url: `${memberAppUrl.replace(/\/+$/, '')}/reports/${p.report_id}`,
        tag: `report-${p.report_id}`,
      };
    }
    default:
      throw new Error(`unknown notification template: ${template}`);
  }
}
