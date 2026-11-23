import type { Metadata } from 'next';
import { Card, CardContent, CardDescription, CardHeader, CardTitle, StatTile } from '@smartgym/ui';
import { createClient } from '@smartgym/supabase-client/server';
import type { Tables } from '@smartgym/supabase-client/types';
import { PageHeader } from '@/components/page-header';
import { PlansTable } from './plans-table';
import { CreatePlanForm } from './create-plan-form';
import { PromotionsTable } from './promotions-table';
import { CreatePromotionForm } from './create-promotion-form';

export const metadata: Metadata = { title: 'Subscriptions & Offers' };
export const dynamic = 'force-dynamic';

export default async function SubscriptionsPage() {
  const supabase = await createClient();

  const [plansRes, promosRes, orgRes] = await Promise.all([
    supabase
      .from('membership_plans')
      .select('*')
      .order('duration_days', { ascending: true })
      .returns<Tables<'membership_plans'>[]>(),
    supabase
      .from('promotions')
      .select('*')
      .order('created_at', { ascending: false })
      .returns<Tables<'promotions'>[]>(),
    supabase
      .from('organizations')
      .select('id, name')
      .limit(1)
      .maybeSingle(),
  ]);

  const plans = plansRes.data ?? [];
  const promotions = promosRes.data ?? [];
  const organizationId = orgRes.data?.id;

  const activePlans = plans.filter((p) => p.is_active).length;
  const aiPlans = plans.filter((p) => p.ai_training_included).length;
  const activePromos = promotions.filter((p) => p.is_active).length;
  const pendingAiPromos = promotions.filter((p) => p.is_ai_generated && !p.is_active).length;

  return (
    <>
      <PageHeader
        title="Subscriptions & Offers"
        description="Configure commercial membership packages, duration terms, freeze allowances, and AI-recommended promotional discounts."
        breadcrumbs={[{ label: 'Plans & Subscriptions' }]}
      />

      <div className="mb-6 grid grid-cols-2 lg:grid-cols-4 gap-4">
        <StatTile label="Active Packages" value={activePlans} hint={`${plans.length} total packages defined`} />
        <StatTile label="AI-Enabled Packages" value={aiPlans} hint="Smart camera workouts included" />
        <StatTile label="Active Promotions" value={activePromos} hint="Campaign discounts active" />
        <StatTile
          label="AI Suggestions"
          value={pendingAiPromos}
          hint={pendingAiPromos > 0 ? 'Requires admin approval' : 'All recommendations reviewed'}
        />
      </div>

      <div className="space-y-8">
        {/* SECTION 1: Membership Packages */}
        <div className="grid gap-6 lg:grid-cols-[2fr_1fr]">
          <Card>
            <CardHeader>
              <CardTitle>Membership Packages</CardTitle>
              <CardDescription>
                Commercial membership tiers (1, 3, 6, 12 months, day passes, personal training). Configure pricing, freeze limits, and AI entitlement.
              </CardDescription>
            </CardHeader>
            <CardContent>
              <PlansTable plans={plans} />
            </CardContent>
          </Card>

          <Card>
            <CardHeader>
              <CardTitle>New Membership Package</CardTitle>
              <CardDescription>Create a custom membership template with durations and freeze rules.</CardDescription>
            </CardHeader>
            <CardContent>
              <CreatePlanForm organizationId={organizationId} />
            </CardContent>
          </Card>
        </div>

        {/* SECTION 2: Promotions & Offers */}
        <div className="grid gap-6 lg:grid-cols-[2fr_1fr]">
          <Card>
            <CardHeader>
              <CardTitle>Discounts & Promotions</CardTitle>
              <CardDescription>
                Percentage discounts, fixed rebates, and AI-suggested retention campaigns. Suggested campaigns remain inactive until approved by admin.
              </CardDescription>
            </CardHeader>
            <CardContent>
              <PromotionsTable promotions={promotions} />
            </CardContent>
          </Card>

          <Card>
            <CardHeader>
              <CardTitle>Create Promotion</CardTitle>
              <CardDescription>Launch a new promotional offer or voucher code.</CardDescription>
            </CardHeader>
            <CardContent>
              <CreatePromotionForm organizationId={organizationId} />
            </CardContent>
          </Card>
        </div>
      </div>
    </>
  );
}
