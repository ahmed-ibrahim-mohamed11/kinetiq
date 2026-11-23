import type { Metadata } from 'next';
import Link from 'next/link';
import { redirect } from 'next/navigation';
import { ChevronRight } from 'lucide-react';
import { createClient, getCurrentMember } from '@smartgym/supabase-client/server';
import { Badge, EmptyState, formatDateTime, formatDuration, statusVariant } from '@smartgym/ui';
import { CHECKIN_METHOD_LABELS } from '@/lib/labels';

export const metadata: Metadata = { title: 'History' };
export const dynamic = 'force-dynamic';

export default async function HistoryPage() {
  const member = await getCurrentMember();
  if (!member) redirect('/login');
  const supabase = await createClient();

  const [{ data: branch }, { data: sessions }, { data: reports }] = await Promise.all([
    supabase.from('branches').select('timezone').eq('id', member.branch_id).maybeSingle(),
    supabase.from('attendance_sessions').select('*').eq('member_id', member.id).neq('status', 'active').order('check_in_at', { ascending: false }).limit(100),
    supabase.from('workout_reports').select('id, attendance_session_id').eq('member_id', member.id),
  ]);
  const tz = branch?.timezone ?? 'Africa/Cairo';
  const reportBySession = new Map((reports ?? []).map((r) => [r.attendance_session_id, r.id]));
  const rows = sessions ?? [];

  return (
    <div className="space-y-4">
      <div>
        <h1 className="text-xl font-semibold tracking-tight">Visit history</h1>
        <p className="text-sm text-muted-foreground">Completed sessions and their reports. Active sessions appear on Home.</p>
      </div>

      {rows.length === 0 ? (
        <EmptyState title="No visits yet" description="Your sessions appear here after you check out for the first time." />
      ) : (
        <ul className="divide-y rounded-lg border">
          {rows.map((s) => {
            const reportId = reportBySession.get(s.id);
            const duration = s.check_out_at ? (new Date(s.check_out_at).getTime() - new Date(s.check_in_at).getTime()) / 1000 : null;
            const content = (
              <>
                <div className="min-w-0">
                  <p className="truncate font-medium">{formatDateTime(s.check_in_at, tz)}</p>
                  <p className="text-xs text-muted-foreground">
                    {formatDuration(duration)} Â· {CHECKIN_METHOD_LABELS[s.check_in_method] ?? s.check_in_method}
                  </p>
                </div>
                <div className="flex items-center gap-2">
                  <Badge variant={statusVariant(s.status)}>{s.status}</Badge>
                  {reportId && <ChevronRight className="h-4 w-4 text-muted-foreground" aria-hidden />}
                </div>
              </>
            );
            return (
              <li key={s.id}>
                {reportId ? (
                  <Link href={`/reports/${reportId}`} className="flex items-center justify-between gap-3 px-4 py-3 hover:bg-muted/50">
                    {content}
                  </Link>
                ) : (
                  <div className="flex items-center justify-between gap-3 px-4 py-3 opacity-80">{content}</div>
                )}
              </li>
            );
          })}
        </ul>
      )}
    </div>
  );
}
