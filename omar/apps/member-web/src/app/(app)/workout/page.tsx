import type { Metadata } from 'next';
import Link from 'next/link';
import { redirect } from 'next/navigation';
import { createClient, getCurrentMember } from '@smartgym/supabase-client/server';
import { Card, CardContent, CardDescription, CardHeader, CardTitle, Button } from '@smartgym/ui';
import { Sparkles, Lock } from 'lucide-react';
import { PhoneWorkoutClient } from './phone-workout-client';

export const metadata: Metadata = { title: 'Personal AI Workout' };
export const dynamic = 'force-dynamic';

export default async function WorkoutPage() {
  const member = await getCurrentMember();
  if (!member) redirect('/login');
  const supabase = await createClient();

  // Check subscription and AI feature entitlement
  const { data: subData } = await supabase
    .from('member_subscriptions')
    .select('*, membership_plans(*)')
    .eq('member_id', member.id)
    .order('created_at', { ascending: false })
    .limit(1)
    .maybeSingle();

  const sub = subData as (typeof subData & { membership_plans: { name: string; ai_training_included: boolean } | null }) | null;
  const isSubActive = sub && (sub.status === 'active' || sub.status === 'frozen');
  const aiEntitled = Boolean(
    member.status === 'active' ||
      (isSubActive && (sub?.ai_addon_active || sub?.membership_plans?.ai_training_included))
  );

  if (!aiEntitled) {
    return (
      <div className="space-y-5">
        <Card className="border-border">
          <CardHeader className="text-center pb-2">
            <div className="mx-auto flex h-12 w-12 items-center justify-center rounded-full bg-primary/10 text-primary mb-2">
              <Lock className="h-6 w-6" />
            </div>
            <CardTitle className="text-lg">AI Smart Training Add-on Required</CardTitle>
            <CardDescription>
              Personal camera tracking, live rep counting, and biomechanical feedback require an active AI Training entitlement.
            </CardDescription>
          </CardHeader>
          <CardContent className="space-y-4 text-sm text-center">
            <div className="rounded-lg bg-muted/30 p-4 text-xs text-muted-foreground text-left space-y-2 border border-border">
              <p className="font-medium text-foreground">With the AI Training Add-on you get:</p>
              <ul className="list-disc pl-4 space-y-1">
                <li>Overhead gym camera tracking with automated repetition logs.</li>
                <li>Phone camera workout mode with live audio coaching.</li>
                <li>Biomechanical squat depth and posture fault analysis.</li>
              </ul>
            </div>
            <p className="text-xs text-muted-foreground">
              Please contact the front desk at your gym branch to upgrade your membership package or add the AI feature.
            </p>
            <Link href="/dashboard">
              <Button variant="outline" className="w-full">
                Back to Dashboard
              </Button>
            </Link>
          </CardContent>
        </Card>
      </div>
    );
  }

  return <PhoneWorkoutClient memberName={member.full_name} />;
}

