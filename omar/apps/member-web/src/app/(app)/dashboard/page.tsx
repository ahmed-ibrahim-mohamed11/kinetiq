import type { Metadata } from 'next';
import Link from 'next/link';
import { redirect } from 'next/navigation';
import { ArrowRight } from 'lucide-react';
import type { WorkoutReportSummary } from '@smartgym/shared-types';
import { createClient, getCurrentMember } from '@smartgym/supabase-client/server';
import { Card, CardContent, CardDescription, CardHeader, CardTitle, LiveDot, StatTile, formatDateTime, formatDuration, formatTime } from '@smartgym/ui';
import { CheckInQr } from '@/components/check-in-qr';
import { CheckOutButton } from '@/components/check-out-button';
import { Elapsed } from '@/components/elapsed';
import { LiveWorkout } from '@/components/live-workout';
import { isWorkoutReportSummary } from '@/components/report-view';
import { CHECKIN_METHOD_LABELS } from '@/lib/labels';

export const metadata: Metadata = { title: 'Home' };
export const dynamic = 'force-dynamic';

export default async function DashboardPage() {
  const member = await getCurrentMember();
  if (!member) redirect('/login');
  const supabase = await createClient();

  const [{ data: branch }, { data: activeSession }, { data: lastReport }] = await Promise.all([
    supabase.from('branches').select('timezone').eq('id', member.branch_id).maybeSingle(),
    supabase.from('attendance_sessions').select('*').eq('member_id', member.id).eq('status', 'active').maybeSingle(),
    supabase.from('workout_reports').select('id, generated_at, summary').eq('member_id', member.id).order('generated_at', { ascending: false }).limit(1).maybeSingle(),
  ]);
  const tz = branch?.timezone ?? 'Africa/Cairo';
  const lastSummary: WorkoutReportSummary | null = lastReport && isWorkoutReportSummary(lastReport.summary) ? lastReport.summary : null;

  return (
    <div className="space-y-5">
      {activeSession ? (
        <Card>
          <CardHeader>
            <div className="flex items-center gap-2">
              <LiveDot active />
              <CardTitle>You&apos;re checked in</CardTitle>
            </div>
            <CardDescription>
              Since {formatTime(activeSession.check_in_at, tz)} via {CHECKIN_METHOD_LABELS[activeSession.check_in_method] ?? activeSession.check_in_method} Â·{' '}
              <Elapsed sinceIso={activeSession.check_in_at} className="tabular-nums font-medium text-foreground" /> in the gym
            </CardDescription>
          </CardHeader>
          <CardContent className="space-y-5">
            <LiveWorkout sessionId={activeSession.id} />
            <CheckOutButton />
            <p className="text-xs text-muted-foreground">
              Leaving through the exit camera or scanning at the desk also checks you out. Your report is generated when the session ends.
            </p>
          </CardContent>
        </Card>
      ) : (
        <Card>
          <CardHeader>
            <CardTitle>Check in</CardTitle>
            <CardDescription>Not checked in right now. Scan this code at the entrance to start a session without face identification.</CardDescription>
          </CardHeader>
          <CardContent>
            <CheckInQr memberCode={member.member_code} />
          </CardContent>
        </Card>
      )}

      <Card>
        <CardHeader className="pb-3">
          <div className="flex items-center justify-between">
            <CardTitle className="text-base">Last workout</CardTitle>
            {lastReport && (
              <Link href={`/reports/${lastReport.id}`} className="flex items-center gap-1 text-sm font-medium text-primary underline-offset-4 hover:underline">
                Open report <ArrowRight className="h-4 w-4" aria-hidden />
              </Link>
            )}
          </div>
          {lastReport && <CardDescription>{formatDateTime(lastReport.generated_at, tz)}</CardDescription>}
        </CardHeader>
        <CardContent>
          {lastSummary ? (
            <div className="grid grid-cols-2 gap-3">
              <StatTile label="Total reps" value={lastSummary.total_reps} />
              <StatTile label="Sets" value={lastSummary.total_sets} />
              <StatTile label="Active time" value={formatDuration(lastSummary.active_exercise_seconds)} />
              <StatTile label="Visit" value={formatDuration(lastSummary.visit_seconds)} />
            </div>
          ) : (
            <p className="text-sm text-muted-foreground">No reports yet. Your first report is generated when you check out of a tracked session.</p>
          )}
        </CardContent>
      </Card>

      <p className="text-center text-xs text-muted-foreground">
        <Link href="/history" className="underline-offset-4 hover:underline">
          See all visits
        </Link>
      </p>
    </div>
  );
}
