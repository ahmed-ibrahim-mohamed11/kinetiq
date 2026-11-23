import type { Metadata } from 'next';
import { Alert, Card, CardContent, CardDescription, CardHeader, CardTitle } from '@smartgym/ui';
import { createClient } from '@smartgym/supabase-client/server';
import { PageHeader } from '@/components/page-header';
import { MembersTable } from './members-table';
import { NewMemberForm } from './new-member-form';
import type { BranchOption, MemberListRow, PlanOption } from './types';

export const metadata: Metadata = { title: 'Members' };
export const dynamic = 'force-dynamic';

export default async function MembersPage() {
  const supabase = await createClient();
  const [membersRes, branchesRes, plansRes] = await Promise.all([
    supabase
      .from('members')
      .select('id, full_name, member_code, phone, email, status, branch_id, created_at')
      .order('created_at', { ascending: false })
      .returns<MemberListRow[]>(),
    supabase.from('branches').select('id, name, organization_id').order('name').returns<BranchOption[]>(),
    supabase
      .from('membership_plans')
      .select('id, name, code, duration_days, price, currency, ai_training_included, ai_addon_fee')
      .eq('is_active', true)
      .order('duration_days')
      .returns<PlanOption[]>(),
  ]);
  const members = membersRes.data ?? [];
  const branches = branchesRes.data ?? [];
  const plans = plansRes.data ?? [];
  const branchName = new Map(branches.map((b) => [b.id, b.name]));

  return (
    <>
      <PageHeader
        title="Members"
        description="Membership records for your branches. Consent and biometric enrollment are managed per member."
        breadcrumbs={[{ label: 'Members' }]}
      />
      {membersRes.error && (
        <div className="mb-4">
          <Alert variant="error" title="Could not load members">
            {membersRes.error.message}
          </Alert>
        </div>
      )}
      <div className="grid gap-6 lg:grid-cols-[1.6fr_1.4fr]">
        <MembersTable members={members.map((m) => ({ ...m, branch_name: branchName.get(m.branch_id) ?? '—' }))} />
        <Card>
          <CardHeader>
            <CardTitle>Reception Registration</CardTitle>
            <CardDescription>Register a new gym member, assign an initial membership package, configure optional AI workout tracking, and record explicit biometric and CCTV consent.</CardDescription>
          </CardHeader>
          <CardContent>
            {branches.length === 0 ? (
              <p className="text-sm text-muted-foreground">No branch is accessible to your account, so members cannot be created.</p>
            ) : (
              <NewMemberForm branches={branches} plans={plans} />
            )}
          </CardContent>
        </Card>
      </div>
    </>
  );
}
