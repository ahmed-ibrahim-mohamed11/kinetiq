import type { Metadata } from 'next';
import { CertClient } from './cert-client';

export const metadata: Metadata = {
  title: 'Mobile HTTPS & Certificate Setup',
  description: 'Step-by-step instructions for trusting the local HTTPS certificate on iPhone and Android handsets.',
};

export const dynamic = 'force-dynamic';

export default function CertPage() {
  return (
    <main className="min-h-dvh bg-background p-4 sm:p-8">
      <CertClient />
    </main>
  );
}
