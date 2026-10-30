import type { Metadata } from 'next';
import { notFound } from 'next/navigation';
import { createClient } from '@smartgym/supabase-client/server';
import { ReportView, isWorkoutReportSummary } from '@/components/report-view';

export const metadata: Metadata = {
  title: 'Shared workout report',
  robots: { index: false, follow: false, nocache: true, googleBot: { index: false, follow: false } },
};
export const dynamic = 'force-dynamic';

interface SharedReport {
  member_name: string;
  generated_at: string;
  summary: unknown;
}

/**
 * Public, unguessable share link. `get_shared_report` runs as SECURITY DEFINER and
 * returns only the member's display name and the summary — no identifiers.
 */
export default async function SharedReportPage({ params }: { params: Promise<{ token: string }> }) {
  const { token } = await params;
  if (!/^[A-Za-z0-9_-]{16,64}$/.test(token)) notFound();

  const supabase = await createClient();
  const { data, error } = await supabase.rpc('get_shared_report', { p_token: token });
  if (error || !data) notFound();
  const report = data as unknown as SharedReport;
  if (!isWorkoutReportSummary(report.summary)) notFound();

  return (
    <main className="mx-auto w-full max-w-3xl px-4 py-6">
      <div className="mb-5 flex items-center gap-2 text-sm text-muted-foreground print:hidden">
        <img src="/icon.svg" alt="" width={24} height={24} className="rounded" />
        Shared from Smart Gym · read-only
      </div>
      <ReportView summary={report.summary} memberName={report.member_name} generatedAt={report.generated_at} />
    </main>
  );
}
