import { z } from 'zod';

/** Payload shapes per template. Validated before any delivery attempt. */
export const WorkoutReportReadyPayload = z.object({
  report_id: z.string().uuid(),
  attendance_session_id: z.string().uuid(),
});

export interface RenderedNotification {
  title: string;
  body: string;
  url: string;
  tag: string;
}

export function render(template: string, payload: unknown, memberName: string, memberAppUrl: string): RenderedNotification {
  switch (template) {
    case 'workout_report_ready': {
      const p = WorkoutReportReadyPayload.parse(payload);
      return {
        title: 'Your workout report is ready',
        body: `${memberName.split(' ')[0] ?? 'Hi'}, today's session summary is available.`,
        url: `${memberAppUrl.replace(/\/$/, '')}/reports/${p.report_id}`,
        tag: `report-${p.report_id}`,
      };
    }
    default:
      throw new Error(`unknown notification template: ${template}`);
  }
}
