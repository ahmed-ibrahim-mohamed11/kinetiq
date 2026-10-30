import type { Metadata } from 'next';
import Link from 'next/link';
import { notFound, redirect } from 'next/navigation';
import { ArrowLeft } from 'lucide-react';
import { createClient, getCurrentMember } from '@smartgym/supabase-client/server';
import { Alert } from '@smartgym/ui';
import { ReportView, isWorkoutReportSummary } from '@/components/report-view';
import { ShareButton } from '@/components/share-button';
import { markReportViewed } from '@/lib/actions';

export const metadata: Metadata = { title: 'Workout report' };
export const dynamic = 'force-dynamic';

export default async function ReportPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  if (!/^[0-9a-f-]{36}$/i.test(id)) notFound();

  const member = await getCurrentMember();
  if (!member) redirect('/login');
  const supabase = await createClient();

  const [{ data: report }, { data: branch }] = await Promise.all([
    supabase.from('workout_reports').select('*').eq('id', id).maybeSingle(), // RLS: own reports only
    supabase.from('branches').select('timezone').eq('id', member.branch_id).maybeSingle(),
  ]);
  if (!report) notFound();

  if (!report.viewed_at) {
    // Fire-and-forget bookkeeping; never block rendering on it.
    void markReportViewed({ reportId: report.id });
  }

  return (
    <div className="space-y-5">
      <div className="flex items-center justify-between print:hidden">
        <Link href="/history" className="flex items-center gap-1 text-sm text-muted-foreground hover:text-foreground">
          <ArrowLeft className="h-4 w-4" aria-hidden /> History
        </Link>
        <ShareButton shareToken={report.share_token} expiresAt={report.share_expires_at} />
      </div>

      {isWorkoutReportSummary(report.summary) ? (
        <ReportView summary={report.summary} memberName={member.full_name} generatedAt={report.generated_at} timeZone={branch?.timezone} />
      ) : (
        <Alert variant="error" title="This report cannot be displayed">
          The report data uses a format this app does not understand yet. Please contact the front desk.
        </Alert>
      )}
    </div>
  );
}
