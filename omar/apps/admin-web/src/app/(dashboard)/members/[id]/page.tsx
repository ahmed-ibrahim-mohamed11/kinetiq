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
import { CheckInButton, FaceEnrollmentWidget, RevokeEnrollmentButton } from './member-actions';
import { FreezeSubscriptionModal, UnfreezeSubscriptionButton, RenewSubscriptionModal } from './subscription-actions';

export const dynamic = 'force-dynamic';

type Member = Tables<'members'>;
type Consent = Tables<'consent_records'>;
type Enrollment = Tables<'biometric_enrollments'> & { edge_devices: { name: string } | null };
type Session = Pick<Tables<'attendance_sessions'>, 'id' | 'check_in_at' | 'check_out_at' | 'check_in_method' | 'check_out_method' | 'status'>;
type SubscriptionWithPlan = Tables<'member_subscriptions'> & { membership_plans: Tables<'membership_plans'> | null };
type Payment = Tables<'payments'>;
type Invoice = Tables<'invoices'>;
type PlanOption = Pick<Tables<'membership_plans'>, 'id' | 'name' | 'price' | 'currency' | 'duration_days' | 'ai_training_included'>;

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

  const [
    consentsRes,
    enrollmentsRes,
    sessionsRes,
    reportsRes,
    branchRes,
    subsRes,
    paymentsRes,
    invoicesRes,
    plansRes,
  ] = await Promise.all([
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
    supabase
      .from('member_subscriptions')
      .select('*, membership_plans(*)')
      .eq('member_id', id)
      .order('created_at', { ascending: false })
      .returns<SubscriptionWithPlan[]>(),
    supabase.from('payments').select('*').eq('member_id', id).order('created_at', { ascending: false }).limit(20).returns<Payment[]>(),
    supabase.from('invoices').select('*').eq('member_id', id).order('issued_at', { ascending: false }).limit(20).returns<Invoice[]>(),
    supabase
      .from('membership_plans')
      .select('id, name, price, currency, duration_days, ai_training_included')
      .eq('is_active', true)
      .order('duration_days')
      .returns<PlanOption[]>(),
  ]);

  const consents = consentsRes.data ?? [];
  const enrollments = enrollmentsRes.data ?? [];
  const sessions = sessionsRes.data ?? [];
  const subscriptions = subsRes.data ?? [];
  const payments = paymentsRes.data ?? [];
  const invoices = invoicesRes.data ?? [];
  const availablePlans = plansRes.data ?? [];

  const reportBySession = new Map((reportsRes.data ?? []).map((r) => [r.attendance_session_id, r]));
  const activeSession = sessions.find((s) => s.status === 'active') ?? null;
  const branchName = (branchRes.data as { name: string } | null)?.name ?? '—';

  const nowMs = Date.now();
  const isValid = (c: Consent) => c.status === 'granted' && (!c.expires_at || new Date(c.expires_at).getTime() > nowMs);
  const grantedTypes = new Set(consents.filter(isValid).map((c) => c.consent_type));
  const canTrack = grantedTypes.has('cctv_workout_tracking');
  const canFace = grantedTypes.has('biometric_face_identification');

  // Subscription calculation
  const activeSub = subscriptions.find((s) => s.status === 'active' || s.status === 'frozen') ?? subscriptions[0] ?? null;
  const isSubActive = activeSub && (activeSub.status === 'active' || activeSub.status === 'frozen');
  const remainingDays = activeSub ? Math.max(0, Math.ceil((new Date(activeSub.expires_at).getTime() - nowMs) / (1000 * 60 * 60 * 24))) : 0;
  const maxFreezeRemaining = activeSub?.membership_plans
    ? Math.max(0, activeSub.membership_plans.freeze_allowance_days - activeSub.total_freeze_days_used)
    : 0;

  return (
    <>
      <PageHeader
        title={member.full_name}
        description={`${member.member_code} · ${branchName}`}
        breadcrumbs={[{ label: 'Members', href: '/members' }, { label: member.full_name }]}
        actions={
          <>
            <Badge variant={statusVariant(member.status)} className="self-center">
              {member.status}
            </Badge>
            <CheckInButton memberId={member.id} activeSessionId={activeSession?.id ?? null} disabled={member.status !== 'active'} />
          </>
        }
      />

      {/* Subscription & Entitlements Banner */}
      <div className="mb-6 rounded-xl border border-border bg-card p-5 shadow-sm">
        <div className="flex flex-col sm:flex-row sm:items-center sm:justify-between gap-4 border-b border-border pb-4">
          <div>
            <div className="flex items-center gap-2">
              <h3 className="text-base font-semibold">
                {activeSub?.membership_plans?.name ?? 'No active package'}
              </h3>
              {activeSub && (
                <Badge
                  variant={
                    activeSub.status === 'active'
                      ? 'default'
                      : activeSub.status === 'frozen'
                      ? 'outline'
                      : 'destructive'
                  }
                >
                  {activeSub.status.toUpperCase()}
                </Badge>
              )}
              {activeSub?.ai_addon_active && (
                <Badge variant="outline" className="bg-primary/20 text-primary border-primary/30">
                  ⚡ AI Workout Tracking Active
                </Badge>
              )}
            </div>
            <p className="text-xs text-muted-foreground mt-1">
              {activeSub ? (
                <>
                  Valid: {new Date(activeSub.starts_at).toLocaleDateString()} — {new Date(activeSub.expires_at).toLocaleDateString()}
                  {activeSub.total_freeze_days_used > 0 && ` (${activeSub.total_freeze_days_used}d frozen)`}
                </>
              ) : (
                'Member has no registered subscription package.'
              )}
            </p>
          </div>

          <div className="flex items-center gap-2 flex-wrap">
            {activeSub?.status === 'active' && maxFreezeRemaining > 0 && (
              <FreezeSubscriptionModal subscriptionId={activeSub.id} maxDaysRemaining={maxFreezeRemaining} />
            )}
            {activeSub?.status === 'frozen' && (
              <UnfreezeSubscriptionButton subscriptionId={activeSub.id} />
            )}
            <RenewSubscriptionModal memberId={member.id} branchId={member.branch_id} plans={availablePlans} />
          </div>
        </div>

        {/* Subscription Metrics */}
        {activeSub && (
          <div className="grid grid-cols-2 sm:grid-cols-4 gap-4 pt-4 text-sm">
            <div>
              <p className="text-xs text-muted-foreground">Remaining Duration</p>
              <p className="text-lg font-bold text-foreground">
                {activeSub.status === 'frozen' ? 'Frozen' : `${remainingDays} days`}
              </p>
            </div>
            <div>
              <p className="text-xs text-muted-foreground">Sessions Remaining</p>
              <p className="text-lg font-bold text-foreground">
                {activeSub.sessions_remaining !== null ? `${activeSub.sessions_remaining} visits` : 'Unlimited'}
              </p>
            </div>
            <div>
              <p className="text-xs text-muted-foreground">Freeze Allowance</p>
              <p className="text-lg font-bold text-foreground">
                {maxFreezeRemaining}d left
                <span className="text-xs font-normal text-muted-foreground block">
                  ({activeSub.freeze_count_used} used of {activeSub.membership_plans?.max_freeze_count ?? 1})
                </span>
              </p>
            </div>
            <div>
              <p className="text-xs text-muted-foreground">Payment Status</p>
              <p className="text-lg font-bold text-foreground">
                {activeSub.paid_amount >= activeSub.total_price ? (
                  <span className="text-emerald-500">Fully Paid</span>
                ) : (
                  <span className="text-amber-500">{activeSub.remaining_balance} EGP Due</span>
                )}
              </p>
            </div>
          </div>
        )}
      </div>

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
            <CardTitle>Member Profile &amp; Emergency</CardTitle>
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
              <p className="text-muted-foreground">Date of Birth</p>
              <p>{member.date_of_birth ?? '—'}</p>
            </div>
            <div>
              <p className="text-muted-foreground">Emergency Contact</p>
              <p>
                {member.emergency_contact_name
                  ? `${member.emergency_contact_name} (${member.emergency_contact_phone ?? '—'})`
                  : '—'}
              </p>
            </div>
            <div>
              <p className="text-muted-foreground">Account App Login</p>
              <p>{member.user_id ? 'Activated' : 'Invitation pending'}</p>
            </div>
            <div>
              <p className="text-muted-foreground">Member since</p>
              <p>{formatDateTime(member.created_at)}</p>
            </div>
            {member.notes && (
              <div className="col-span-2 pt-2 border-t border-border">
                <p className="text-xs text-muted-foreground">Staff Notes</p>
                <p className="text-xs mt-0.5">{member.notes}</p>
              </div>
            )}
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
                    <div className="flex items-center gap-2">
                      <Badge variant={e.revoked_at ? 'destructive' : 'success'}>{e.revoked_at ? 'revoked' : 'active'}</Badge>
                      {!e.revoked_at && <RevokeEnrollmentButton enrollmentId={e.id} memberId={member.id} />}
                    </div>
                  </li>
                ))}
              </ul>
            )}
            <FaceEnrollmentWidget
              memberId={member.id}
              memberCode={member.member_code}
              memberName={member.full_name}
            />
          </CardContent>
        </Card>
      </div>

      {/* Payment Ledger & Invoices */}
      <div className="mt-6 grid gap-6 lg:grid-cols-2">
        <Card>
          <CardHeader>
            <CardTitle>Payment Ledger</CardTitle>
            <CardDescription>Direct cash, card, and electronic payment records.</CardDescription>
          </CardHeader>
          <CardContent>
            {payments.length === 0 ? (
              <EmptyState title="No payments recorded" description="Payments logged at reception appear here." />
            ) : (
              <Table>
                <TableHeader>
                  <TableRow>
                    <TableHead>Amount</TableHead>
                    <TableHead>Method</TableHead>
                    <TableHead>Status</TableHead>
                    <TableHead className="text-right">Date</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {payments.map((p) => (
                    <TableRow key={p.id}>
                      <TableCell className="font-semibold text-foreground">
                        {p.amount.toLocaleString()} {p.currency}
                      </TableCell>
                      <TableCell className="capitalize text-xs">{p.method.replace('_', ' ')}</TableCell>
                      <TableCell>
                        <Badge variant={p.status === 'completed' ? 'default' : 'outline'}>{p.status}</Badge>
                      </TableCell>
                      <TableCell className="text-right text-xs text-muted-foreground">
                        {formatDateTime(p.created_at)}
                      </TableCell>
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
            )}
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle>Invoices</CardTitle>
            <CardDescription>Official member billing invoices and receipts.</CardDescription>
          </CardHeader>
          <CardContent>
            {invoices.length === 0 ? (
              <EmptyState title="No invoices generated" description="Invoices issued on subscription purchases appear here." />
            ) : (
              <Table>
                <TableHeader>
                  <TableRow>
                    <TableHead>Invoice #</TableHead>
                    <TableHead>Total</TableHead>
                    <TableHead>Status</TableHead>
                    <TableHead className="text-right">Issued</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {invoices.map((inv) => (
                    <TableRow key={inv.id}>
                      <TableCell className="font-mono text-xs font-semibold">{inv.invoice_number}</TableCell>
                      <TableCell className="font-semibold text-foreground">
                        {inv.total.toLocaleString()} EGP
                        {inv.discount > 0 && (
                          <span className="block text-[11px] text-primary">-{inv.discount} discount</span>
                        )}
                      </TableCell>
                      <TableCell>
                        <Badge variant={inv.status === 'paid' ? 'default' : 'outline'}>{inv.status}</Badge>
                      </TableCell>
                      <TableCell className="text-right text-xs text-muted-foreground">
                        {formatDateTime(inv.issued_at)}
                      </TableCell>
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
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
                      <TableCell className="text-sm">
                        {formatDateTime(c.granted_at)}
                        <div className="text-xs text-muted-foreground">
                          {c.informed_acknowledged_at ? 'informed notice acknowledged' : 'no informed-notice record'}
                          {c.expires_at && ` · expires ${formatDateTime(c.expires_at)}`}
                        </div>
                      </TableCell>
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
