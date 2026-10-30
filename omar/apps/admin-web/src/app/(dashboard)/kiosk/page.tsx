import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@smartgym/ui';
import { PageHeader } from '@/components/page-header';
import { QrScanner } from './qr-scanner';

export const dynamic = 'force-dynamic';

export default function KioskPage() {
  return (
    <div className="space-y-6">
      <PageHeader
        title="Front-desk check-in"
        description="Scan the member's QR code from the Smart Gym app, or type the code shown under it. Check-in runs under your staff account, so only members of your branches can be checked in."
      />
      <div className="grid gap-6 lg:grid-cols-2">
        <QrScanner />
        <Card>
          <CardHeader>
            <CardTitle>How it works</CardTitle>
            <CardDescription>The non-biometric alternative to face identification.</CardDescription>
          </CardHeader>
          <CardContent className="space-y-3 text-sm text-muted-foreground">
            <p>Members open the app, which shows a QR code that changes every minute. Codes are signed by the server and cannot be reused after about two minutes, so a screenshot does not work.</p>
            <p>The scan is verified by the cloud (qr-token function) and creates the attendance session through the same path as a face check-in. Workout tracking on the floor then proceeds identically.</p>
            <p>If the camera is unavailable, ask the member for the text code under the QR and paste it in the field.</p>
          </CardContent>
        </Card>
      </div>
    </div>
  );
}
