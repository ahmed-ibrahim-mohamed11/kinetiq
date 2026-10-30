import type { Metadata } from 'next';
import Link from 'next/link';
import { notFound } from 'next/navigation';
import {
  Alert,
  Badge,
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
  EmptyState,
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
  formatDateTime,
  formatDuration,
  statusVariant,
} from '@smartgym/ui';
import { createClient } from '@smartgym/supabase-client/server';
import type { Tables } from '@smartgym/supabase-client/types';
import { PageHeader } from '@/components/page-header';
import { ConsentForm, WithdrawConsentButton } from './consent-form';
import { CheckInButton } from './member-actions';

export const dynamic = 'force-dynamic';

type Member = Tables<'members'>;
type Consent = Tables<'consent_records'>;
type Enrollment = Tables<'biometric_enrollments'> & { edge_devices: { name: string } | null };
type Session = Pick<Tables<'attendance_sessions'>, 'id' | 'check_in_at' | 'check_out_at' | 'check_in_method' | 'check_out_method' | 'status'>;
interface Report {
  id: string;
  attendance_session_id: string;
  generated_at: string;
  summary: { total_reps?: number; total_sets?: number; active_exercise_seconds?: number; uncertain_segments?: unknown[] } | null;
}

export async function generateMetadata({ params }: { params: Promise<{ id: string }> }): Promise<Metadata> {
  const { id } = await params;
  const supabase = await createClient();
  const { data } = await supabase.from('members').select('full_name').eq('id', id).maybeSingle();
  return { title: (data as { full_name: string } | null)?.full_name ?? 'Member' };
}

export default async function MemberDetailPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  const supabase = await createClient();
  const { data: memberData, error } = await supabase.from('members').select('*').eq('id', id).maybeSingle();
  if (error) {
    return (
      <Alert variant="error" title="Could not load member">
        {error.message}
      </Alert>
    );
  }
  const member = memberData as Member | null;
  if (!member) notFound();

  const [consentsRes, enrollmentsRes, sessionsRes, reportsRes, branchRes] = await Promise.all([
    supabase.from('consent_records').select('*').eq('member_id', id).order('granted_at', { ascending: false }).returns<Consent[]>(),
    supabase.from('biometric_enrollments').select('*, edge_devices(name)').eq('member_id', id).order('enrolled_at', { ascending: false }).returns<Enrollment[]>(),
    supabase
      .from('attendance_sessions')
      .select('id, check_in_at, check_out_at, check_in_method, check_out_method, status')
      .eq('member_id', id)
      .order('check_in_at', { ascending: false })
      .limit(50)
      .returns<Session[]>(),
    supabase.from('workout_reports').select('id, attendance_session_id, generated_at, summary').eq('member_id', id).returns<Report[]>(),
    supabase.from('branches').select('name').eq('id', member.branch_id).maybeSingle(),
  ]);

  const consents = consentsRes.data ?? [];
  const enrollments = enrollmentsRes.data ?? [];
  const sessions = sessionsRes.data ?? [];
  const reportBySession = new Map((reportsRes.data ?? []).map((r) => [r.attendance_session_id, r]));
  const activeSession = sessions.find((s) => s.status === 'active') ?? null;
  const branchName = (branchRes.data as { name: string } | null)?.name ?? '—';

  const grantedTypes = new Set(consents.filter((c) => c.status === 'granted').map((c) => c.consent_type));
  const canTrack = grantedTypes.has('cctv_workout_tracking');
  const canFace = grantedTypes.has('biometric_face_identification');

  return (
    <>
      <PageHeader
        title={member.full_name}
        description={`${member.member_code} · ${branchName}`}
        actions={
          <>
            <Badge variant={statusVariant(member.status)} className="self-center">
              {member.status}
            </Badge>
            <CheckInButton memberId={member.id} activeSessionId={activeSession?.id ?? null} disabled={member.status !== 'active'} />
          </>
        }
      />

      {!canTrack && (
        <div className="mb-4">
          <Alert variant="warning" title="No CCTV workout-tracking consent">
            The edge devices will refuse to track this member&apos;s workouts until &quot;CCTV workout tracking&quot; consent is recorded below. Check-ins by face
            additionally require biometric consent.
          </Alert>
        </div>
      )}

      <div className="grid gap-6 lg:grid-cols-2">
        <Card>
          <CardHeader>
            <CardTitle>Contact</CardTitle>
          </CardHeader>
          <CardContent className="grid grid-cols-2 gap-3 text-sm">
            <div>
              <p className="text-muted-foreground">Phone</p>
              <p>{member.phone ?? '—'}</p>
            </div>
            <div>
              <p className="text-muted-foreground">Email</p>
              <p className="truncate">{member.email ?? '—'}</p>
            </div>
            <div>
              <p className="text-muted-foreground">Account</p>
              <p>{member.user_id ? 'Activated' : 'Not activated yet'}</p>
            </div>
            <div>
              <p className="text-muted-foreground">Member since</p>
              <p>{formatDateTime(member.created_at)}</p>
            </div>
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle>Biometric enrollment</CardTitle>
            <CardDescription>
              Reference records only. Face templates are encrypted and stored on the edge device; this database never holds template data.
            </CardDescription>
          </CardHeader>
          <CardContent>
            {enrollments.length === 0 ? (
              <p className="text-sm text-muted-foreground">
                {canFace ? 'Consent granted — enrollment is performed at the entrance kiosk by the edge device.' : 'No enrollment. Requires biometric consent first.'}
              </p>
            ) : (
              <ul className="space-y-2 text-sm">
                {enrollments.map((e) => (
                  <li key={e.id} className="flex items-center justify-between rounded-md border p-2">
                    <div>
                      <p className="font-mono text-xs">{e.template_ref}</p>
                      <p className="text-xs text-muted-foreground">
                        {e.edge_devices?.name ?? 'device'} · key {e.key_id} · {formatDateTime(e.enrolled_at)}
                      </p>
                    </div>
                    <Badge variant={e.revoked_at ? 'destructive' : 'success'}>{e.revoked_at ? 'revoked' : 'active'}</Badge>
                  </li>
                ))}
              </ul>
            )}
          </CardContent>
        </Card>
      </div>

      <Card className="mt-6">
        <CardHeader>
          <CardTitle>Consent</CardTitle>
          <CardDescription>Every record is immutable; withdrawal creates an audit trail and revokes biometric references immediately.</CardDescription>
        </CardHeader>
        <CardContent className="grid gap-6 lg:grid-cols-[2fr_1fr]">
          <div>
            {consents.length === 0 ? (
              <EmptyState title="No consent recorded" description="Record the member's informed consent before any camera-based tracking." />
            ) : (
              <Table>
                <TableHeader>
                  <TableRow>
                    <TableHead>Type</TableHead>
                    <TableHead>Policy</TableHead>
                    <TableHead>Granted</TableHead>
                    <TableHead>Status</TableHead>
                    <TableHead />
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {consents.map((c) => (
                    <TableRow key={c.id}>
                      <TableCell className="font-medium">{c.consent_type.replaceAll('_', ' ')}</TableCell>
                      <TableCell className="text-sm text-muted-foreground">{c.policy_version}</TableCell>
                      <TableCell className="text-sm">{formatDateTime(c.granted_at)}</TableCell>
                      <TableCell>
                        <Badge variant={statusVariant(c.status)}>{c.status}</Badge>
                        {c.withdrawn_at && <div className="text-xs text-muted-foreground">{formatDateTime(c.withdrawn_at)}</div>}
                      </TableCell>
                      <TableCell className="text-right">{c.status === 'granted' && <WithdrawConsentButton consentId={c.id} memberId={member.id} />}</TableCell>
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
            )}
          </div>
          <ConsentForm memberId={member.id} />
        </CardContent>
      </Card>

      <Card className="mt-6">
        <CardHeader>
          <CardTitle>Attendance &amp; reports</CardTitle>
          <CardDescription>Most recent 50 visits. Reports are generated automatically at checkout.</CardDescription>
        </CardHeader>
        <CardContent>
          {sessions.length === 0 ? (
            <EmptyState title="No visits yet" />
          ) : (
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>Check-in</TableHead>
                  <TableHead>Check-out</TableHead>
                  <TableHead>Duration</TableHead>
                  <TableHead>Method</TableHead>
                  <TableHead>Status</TableHead>
                  <TableHead>Report</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {sessions.map((s) => {
                  const report = reportBySession.get(s.id);
                  const duration = s.check_out_at ? (new Date(s.check_out_at).getTime() - new Date(s.check_in_at).getTime()) / 1000 : null;
                  return (
                    <TableRow key={s.id}>
                      <TableCell>{formatDateTime(s.check_in_at)}</TableCell>
                      <TableCell>{formatDateTime(s.check_out_at)}</TableCell>
                      <TableCell>{formatDuration(duration)}</TableCell>
                      <TableCell>
                        <Badge variant="outline">{s.check_in_method}</Badge>
                      </TableCell>
                      <TableCell>
                        <Badge variant={statusVariant(s.status)}>{s.status}</Badge>
                      </TableCell>
                      <TableCell className="text-sm">
                        {report ? (
                          <span>
                            {report.summary?.total_sets ?? 0} sets · {report.summary?.total_reps ?? 0} reps
                            {(report.summary?.uncertain_segments?.length ?? 0) > 0 && (
                              <span className="ml-1 text-xs text-warning">· {report.summary?.uncertain_segments?.length} uncertain</span>
                            )}
                            <span className="ml-2 text-xs text-muted-foreground">{formatDateTime(report.generated_at)}</span>
                          </span>
                        ) : s.status === 'completed' ? (
                          <span className="text-muted-foreground">Pending</span>
                        ) : (
                          '—'
                        )}
                      </TableCell>
                    </TableRow>
                  );
                })}
              </TableBody>
            </Table>
          )}
          <p className="mt-4 text-xs text-muted-foreground">
            <Link href="/members" className="hover:underline">
              ← Back to members
            </Link>
          </p>
        </CardContent>
      </Card>
    </>
  );
}
