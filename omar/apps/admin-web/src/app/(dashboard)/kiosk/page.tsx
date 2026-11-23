import Link from 'next/link';
import { Badge, Card, CardContent, CardDescription, CardHeader, CardTitle } from '@smartgym/ui';
import { createClient } from '@smartgym/supabase-client/server';
import { PageHeader } from '@/components/page-header';
import { QrScanner, type KioskMemberOption } from './qr-scanner';

export const dynamic = 'force-dynamic';

export default async function KioskPage() {
  const supabase = await createClient();

  const [{ data: membersData }, { data: enrollmentsData }] = await Promise.all([
    supabase
      .from('members')
      .select('id, full_name, member_code, email, status')
      .eq('status', 'active')
      .order('created_at', { ascending: false })
      .limit(50),
    supabase
      .from('biometric_enrollments')
      .select('id, member_id, template_ref, enrolled_at, revoked_at')
      .is('revoked_at', null)
      .order('enrolled_at', { ascending: false }),
  ]);

  const enrollMap = new Map<string, { template_ref: string; enrolled_at: string }>();
  for (const e of enrollmentsData ?? []) {
    const row = e as { member_id: string; template_ref: string; enrolled_at: string };
    if (!enrollMap.has(row.member_id)) {
      enrollMap.set(row.member_id, { template_ref: row.template_ref, enrolled_at: row.enrolled_at });
    }
  }

  const members: KioskMemberOption[] = (membersData ?? []).map((m) => {
    const row = m as { id: string; full_name: string; member_code: string; email: string | null };
    const enr = enrollMap.get(row.id);
    return {
      id: row.id,
      full_name: row.full_name,
      member_code: row.member_code,
      email: row.email,
      template_ref: enr?.template_ref ?? null,
      enrolled_at: enr?.enrolled_at ?? null,
    };
  });

  const enrolledCount = members.filter((m) => m.template_ref !== null).length;

  return (
    <div className="space-y-6">
      <PageHeader
        title="Entrance Check-In Kiosk (Face ID & QR)"
        description="Use the laptop camera for contactless Face Recognition check-in, enroll a member's face at the desk, or scan/paste a member's QR check-in code."
        breadcrumbs={[{ label: 'Check-in Kiosk' }]}
      />
      <div className="grid gap-6 lg:grid-cols-[1.35fr_1fr]">
        <QrScanner members={members} />
        <div className="space-y-6">
          <Card>
            <CardHeader className="pb-3">
              <div className="flex items-center justify-between">
                <CardTitle className="text-base">Face-Enrolled Members</CardTitle>
                <Badge variant="default">{enrolledCount} Enrolled</Badge>
              </div>
              <CardDescription>
                Members with active biometric face templates ready for camera entrance check-in.
              </CardDescription>
            </CardHeader>
            <CardContent className="space-y-2 text-xs">
              {members.slice(0, 8).map((m) => (
                <div
                  key={m.id}
                  className="flex items-center justify-between rounded-lg border border-border p-2.5 bg-muted/10"
                >
                  <div>
                    <Link href={`/members/${m.id}`} className="font-semibold text-foreground hover:underline">
                      {m.full_name}
                    </Link>
                    <p className="text-muted-foreground font-mono text-[11px]">
                      {m.member_code} {m.email ? `· ${m.email}` : ''}
                    </p>
                  </div>
                  {m.template_ref ? (
                    <Badge variant="success" className="text-[10px] font-mono">
                      ✓ Face Enrolled
                    </Badge>
                  ) : (
                    <Badge variant="outline" className="text-[10px]">
                      QR Only
                    </Badge>
                  )}
                </div>
              ))}
            </CardContent>
          </Card>

          <Card>
            <CardHeader>
              <CardTitle className="text-base">How Entrance Check-In Works</CardTitle>
              <CardDescription>Dual-mode biometric &amp; non-biometric access control.</CardDescription>
            </CardHeader>
            <CardContent className="space-y-2.5 text-xs text-muted-foreground">
              <p>
                <strong className="text-foreground">1. Face Recognition Camera Check-In:</strong> Open the camera in Face ID mode, look at the webcam, and click <em>Verify Face &amp; Enter Gym</em>. If a member hasn&apos;t saved their face yet, you can also click <em>Enroll &amp; Save Face</em> right from the camera.
              </p>
              <p>
                <strong className="text-foreground">2. QR / Code Check-In:</strong> Members who prefer not to use biometrics can open the Member App (<code className="text-foreground">/dashboard</code>), copy the signed <code className="text-foreground">SGM1…</code> code under their QR, and paste it in the QR tab.
              </p>
            </CardContent>
          </Card>
        </div>
      </div>
    </div>
  );
}
