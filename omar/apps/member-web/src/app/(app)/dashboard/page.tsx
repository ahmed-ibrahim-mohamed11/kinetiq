import type { Metadata } from 'next';
import Link from 'next/link';
import { redirect } from 'next/navigation';
import { ArrowRight, Camera, Sparkles, ShieldCheck } from 'lucide-react';
import type { WorkoutReportSummary } from '@smartgym/shared-types';
import { createClient, getCurrentMember } from '@smartgym/supabase-client/server';
import {
  Badge,
  Button,
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
  LiveDot,
  StatTile,
  formatDateTime,
  formatDuration,
  formatTime,
} from '@smartgym/ui';
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

  const [
    { data: branch },
    { data: activeSession },
    { data: lastReport },
    { data: subData },
  ] = await Promise.all([
    supabase.from('branches').select('timezone').eq('id', member.branch_id).maybeSingle(),
    supabase.from('attendance_sessions').select('*').eq('member_id', member.id).eq('status', 'active').maybeSingle(),
    supabase.from('workout_reports').select('id, generated_at, summary').eq('member_id', member.id).order('generated_at', { ascending: false }).limit(1).maybeSingle(),
    supabase
      .from('member_subscriptions')
      .select('*, membership_plans(*)')
      .eq('member_id', member.id)
      .order('created_at', { ascending: false })
      .limit(1)
      .maybeSingle(),
  ]);

  const tz = branch?.timezone ?? 'Africa/Cairo';
  const lastSummary: WorkoutReportSummary | null = lastReport && isWorkoutReportSummary(lastReport.summary) ? lastReport.summary : null;

  const nowMs = Date.now();
  const sub = subData as (typeof subData & { membership_plans: { name: string; ai_training_included: boolean } | null }) | null;
  const isSubActive = sub && (sub.status === 'active' || sub.status === 'frozen');
  const remainingDays = sub ? Math.max(0, Math.ceil((new Date(sub.expires_at).getTime() - nowMs) / (1000 * 60 * 60 * 24))) : 0;
  const aiEntitled = Boolean(member.status === 'active' || sub?.ai_addon_active || sub?.membership_plans?.ai_training_included);

  return (
    <div className="space-y-5">
      {/* Prominent AI Trainer Camera Hero Launch Card */}
      <Card className="border-primary/40 bg-gradient-to-r from-primary/15 via-primary/5 to-card shadow-md">
        <CardContent className="p-4 sm:p-5 flex flex-col sm:flex-row sm:items-center justify-between gap-4">
          <div className="space-y-1">
            <div className="flex items-center gap-2">
              <Sparkles className="h-5 w-5 text-primary" />
              <h2 className="font-bold text-base text-foreground">AI Personal Trainer Camera (Form Analyzer)</h2>
              <Badge variant="default" className="text-[10px]">LIVE POSE AI</Badge>
            </div>
            <p className="text-xs text-muted-foreground">
              Open your camera to track squats in real time, measure knee &amp; back angles, and see whether each repetition is <strong>Right Form</strong> or <strong>Wrong Form</strong>.
            </p>
          </div>
          <Link href="/workout" className="shrink-0">
            <Button size="lg" className="w-full sm:w-auto gap-2 bg-primary hover:bg-primary/90 font-semibold">
              <Camera className="h-4 w-4" /> Start AI Trainer Camera
            </Button>
          </Link>
        </CardContent>
      </Card>

      {/* Membership & Subscription Status Banner */}
      <div id="membership" className="rounded-xl border border-border bg-gradient-to-r from-card to-muted/30 p-4 shadow-sm scroll-mt-20">
        <div className="flex items-start justify-between gap-3">
          <div>
            <div className="flex items-center gap-2">
              <h2 className="font-semibold text-base">{sub?.membership_plans?.name ?? 'Standard Gym Access'}</h2>
              <Badge variant={isSubActive ? 'default' : 'outline'}>
                {sub?.status ? sub.status.toUpperCase() : 'NO PLAN'}
              </Badge>
              {aiEntitled && (
                <Badge variant="outline" className="bg-primary/20 text-primary border-primary/30">
                  <Sparkles className="h-3 w-3 mr-1 inline" /> AI Training
                </Badge>
              )}
            </div>
            <p className="text-xs text-muted-foreground mt-1">
              {sub ? (
                <>
                  {sub.status === 'frozen'
                    ? 'Subscription is currently frozen'
                    : `${remainingDays} days remaining · Expires ${new Date(sub.expires_at).toLocaleDateString()}`}
                </>
              ) : (
                'Visit the reception desk to choose a membership package.'
              )}
            </p>
          </div>
          {aiEntitled && (
            <Link href="/workout">
              <Button size="sm" className="gap-1.5 shrink-0 bg-primary hover:bg-primary/90">
                <Camera className="h-4 w-4" /> Personal AI
              </Button>
            </Link>
          )}
        </div>
      </div>

      {activeSession ? (
        <Card data-testid="active-session">
          <CardHeader>
            <div className="flex items-center gap-2">
              <LiveDot active />
              <CardTitle>You&apos;re checked in</CardTitle>
            </div>
            <CardDescription>
              Since {formatTime(activeSession.check_in_at, tz)} via {CHECKIN_METHOD_LABELS[activeSession.check_in_method] ?? activeSession.check_in_method} ·{' '}
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

      {/* AI Personal Training Mode Quick Access */}
      {aiEntitled && (
        <Card className="border-primary/20 bg-primary/5">
          <CardHeader className="pb-3">
            <div className="flex items-center justify-between">
              <div className="flex items-center gap-2">
                <Sparkles className="h-5 w-5 text-primary" />
                <CardTitle className="text-base">Phone Camera Personal Trainer</CardTitle>
              </div>
              <Badge variant="outline" className="text-xs border-primary/40 text-primary">
                AI Add-on
              </Badge>
            </div>
            <CardDescription>
              Set up your phone to track squats with real-time repetition counting, depth analysis, and voice audio feedback.
            </CardDescription>
          </CardHeader>
          <CardContent>
            <Link href="/workout">
              <Button className="w-full gap-2">
                <Camera className="h-4 w-4" /> Start Camera Workout Session
              </Button>
            </Link>
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
