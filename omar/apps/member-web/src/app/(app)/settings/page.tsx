import type { Metadata } from 'next';
import Link from 'next/link';
import { redirect } from 'next/navigation';
import { createClient, getCurrentMember, getUser } from '@smartgym/supabase-client/server';
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@smartgym/ui';
import { ConsentList } from '@/components/consent-list';
import { PushToggle } from '@/components/push-toggle';
import { NotificationPrefsForm } from '@/components/notification-prefs-form';
import { LanguageToggle } from '@/components/language-toggle';

export const metadata: Metadata = { title: 'Settings' };
export const dynamic = 'force-dynamic';

export default async function SettingsPage() {
  const [user, member] = await Promise.all([getUser(), getCurrentMember()]);
  if (!user || !member) redirect('/login');
  const supabase = await createClient();

  const [{ data: consents }, { count: pushCount }, { data: prefs }] = await Promise.all([
    supabase.from('consent_records').select('*').eq('member_id', member.id).order('granted_at', { ascending: false }),
    supabase.from('push_subscriptions').select('id', { count: 'exact', head: true }).eq('user_id', user.id).is('disabled_at', null),
    supabase.from('notification_preferences').select('*').eq('member_id', member.id).maybeSingle(),
  ]);

  const currentPrefs = {
    workout_reports: prefs?.workout_reports ?? true,
    subscription_expiry_alerts: prefs?.subscription_expiry_alerts ?? true,
    promotional_offers: prefs?.promotional_offers ?? false,
  };

  return (
    <div className="space-y-5">
      <div>
        <h1 className="text-xl font-semibold tracking-tight">Settings</h1>
        <p className="text-sm text-muted-foreground">
          {member.full_name} · member code <span className="font-mono">{member.member_code}</span> · {user.email}
        </p>
      </div>

      <Card>
        <CardHeader>
          <CardTitle className="text-base">اللغة والاتجاه / Language & Direction</CardTitle>
          <CardDescription>تبديل لغة العرض بين العربية والإنجليزية (يدعم الاتجاه من اليمين لليسار RTL).</CardDescription>
        </CardHeader>
        <CardContent>
          <LanguageToggle />
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle className="text-base">Push Notifications</CardTitle>
          <CardDescription>Enable Web Push notifications on this browser or mobile device.</CardDescription>
        </CardHeader>
        <CardContent className="space-y-4">
          <PushToggle vapidPublicKey={process.env.NEXT_PUBLIC_VAPID_PUBLIC_KEY ?? ''} activeServerSubscriptions={pushCount ?? 0} />
          <div className="border-t border-border pt-3">
            <h4 className="text-xs font-semibold uppercase tracking-wider text-muted-foreground mb-1">
              Communication Preferences
            </h4>
            <NotificationPrefsForm initialPrefs={currentPrefs} />
          </div>
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle className="text-base">Your consents</CardTitle>
          <CardDescription>
            You can withdraw a consent at any time. Withdrawing face identification deletes your face template from the gym&apos;s edge device and switches you to QR check-in.
          </CardDescription>
        </CardHeader>
        <CardContent>
          <ConsentList consents={consents ?? []} />
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle className="text-base">Privacy</CardTitle>
        </CardHeader>
        <CardContent className="space-y-2 text-sm text-muted-foreground">
          <ul className="list-disc space-y-1 pl-5">
            <li>No video is stored by this system. Cameras are processed live on a device inside the gym and only workout events are kept.</li>
            <li>If you opted into face identification, your face template is stored encrypted on the gym&apos;s edge device only. It never leaves the building.</li>
            <li>Individual repetition events are kept for 365 days; your reports stay available while your membership is active.</li>
          </ul>
          <p>
            <Link href="/privacy" className="text-primary underline-offset-4 hover:underline">
              Read the full explanation
            </Link>
          </p>
        </CardContent>
      </Card>
    </div>
  );
}
