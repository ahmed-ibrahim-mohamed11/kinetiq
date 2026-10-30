import type { Metadata } from 'next';
import { Alert } from '@smartgym/ui';
import { PageHeader } from '@/components/page-header';
import { loadLiveSessions } from '@/lib/sessions-data';
import { LiveSessions } from './live-sessions';

export const metadata: Metadata = { title: 'Live Sessions' };
export const dynamic = 'force-dynamic';

export default async function SessionsPage() {
  const { rows, error } = await loadLiveSessions();
  return (
    <>
      <PageHeader
        title="Live sessions"
        description="Members currently checked in. Set and rep counts update in real time as the edge devices report them."
      />
      {error && (
        <div className="mb-4">
          <Alert variant="error" title="Could not load sessions">
            {error}
          </Alert>
        </div>
      )}
      <LiveSessions initial={rows} loadedAt={new Date().toISOString()} />
    </>
  );
}
