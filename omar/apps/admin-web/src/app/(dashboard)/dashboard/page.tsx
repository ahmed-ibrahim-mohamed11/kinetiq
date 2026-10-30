import type { Metadata } from 'next';
import Link from 'next/link';
import { Activity, Camera, Cpu, Users } from 'lucide-react';
import {
  Badge,
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
  EmptyState,
  StatTile,
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
  formatDateTime,
  statusVariant,
} from '@smartgym/ui';
import { createClient } from '@smartgym/supabase-client/server';
import { PageHeader } from '@/components/page-header';

export const metadata: Metadata = { title: 'Dashboard' };
export const dynamic = 'force-dynamic';

interface RecentSession {
  id: string;
  member_id: string;
  check_in_at: string;
  check_out_at: string | null;
  check_in_method: string;
  status: string;
  members: { full_name: string; member_code: string } | null;
}

export default async function DashboardPage() {
  const supabase = await createClient();
  const twoMinutesAgo = new Date(Date.now() - 2 * 60_000).toISOString();

  const [activeSessions, members, camerasTotal, camerasOnline, devicesTotal, devicesSeen, recent] = await Promise.all([
    supabase.from('attendance_sessions').select('id', { count: 'exact', head: true }).eq('status', 'active'),
    supabase.from('members').select('id', { count: 'exact', head: true }),
    supabase.from('cameras').select('id', { count: 'exact', head: true }).eq('enabled', true),
    supabase.from('cameras').select('id', { count: 'exact', head: true }).eq('enabled', true).eq('last_status', 'online'),
    supabase.from('edge_devices').select('id', { count: 'exact', head: true }).neq('status', 'retired'),
    supabase.from('edge_devices').select('id', { count: 'exact', head: true }).gte('last_seen_at', twoMinutesAgo),
    supabase
      .from('attendance_sessions')
      .select('id, member_id, check_in_at, check_out_at, check_in_method, status, members(full_name, member_code)')
      .order('check_in_at', { ascending: false })
      .limit(10)
      .returns<RecentSession[]>(),
  ]);

  const rows = recent.data ?? [];

  return (
    <>
      <PageHeader title="Dashboard" description="Live overview of this branch: who is training, and whether the camera and edge infrastructure is healthy." />

      <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-4">
        <StatTile label="Active sessions" value={activeSessions.count ?? 0} hint="Members currently checked in" icon={<Activity className="h-4 w-4" />} />
        <StatTile label="Members" value={members.count ?? 0} hint="All membership statuses" icon={<Users className="h-4 w-4" />} />
        <StatTile
          label="Cameras online"
          value={`${camerasOnline.count ?? 0} / ${camerasTotal.count ?? 0}`}
          hint="Reported by edge devices (enabled cameras)"
          icon={<Camera className="h-4 w-4" />}
        />
        <StatTile
          label="Edge devices live"
          value={`${devicesSeen.count ?? 0} / ${devicesTotal.count ?? 0}`}
          hint="Heartbeat received in the last 2 minutes"
          icon={<Cpu className="h-4 w-4" />}
        />
      </div>

      <Card className="mt-6">
        <CardHeader>
          <CardTitle>Recent attendance</CardTitle>
          <CardDescription>The ten most recent check-ins across your branches.</CardDescription>
        </CardHeader>
        <CardContent>
          {recent.error ? (
            <p className="text-sm text-destructive">Could not load sessions: {recent.error.message}</p>
          ) : rows.length === 0 ? (
            <EmptyState title="No attendance yet" description="Sessions appear here as soon as a member checks in at the entrance or via QR." />
          ) : (
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>Member</TableHead>
                  <TableHead>Check-in</TableHead>
                  <TableHead>Check-out</TableHead>
                  <TableHead>Method</TableHead>
                  <TableHead>Status</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {rows.map((s) => (
                  <TableRow key={s.id}>
                    <TableCell>
                      <Link href={`/members/${s.member_id}`} className="font-medium hover:underline">
                        {s.members?.full_name ?? 'Unknown member'}
                      </Link>
                      {s.members?.member_code && <span className="ml-2 text-xs text-muted-foreground">{s.members.member_code}</span>}
                    </TableCell>
                    <TableCell>{formatDateTime(s.check_in_at)}</TableCell>
                    <TableCell>{formatDateTime(s.check_out_at)}</TableCell>
                    <TableCell>
                      <Badge variant="outline">{s.check_in_method}</Badge>
                    </TableCell>
                    <TableCell>
                      <Badge variant={statusVariant(s.status)}>{s.status}</Badge>
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          )}
        </CardContent>
      </Card>
    </>
  );
}
