import type { Metadata, Viewport } from 'next';
import { connection } from 'next/server';
import '@smartgym/ui/styles.css';

export const metadata: Metadata = {
  title: { default: 'Smart Gym Admin', template: '%s · Smart Gym Admin' },
  description: 'Operations console for the AI Smart Gym platform: members, consent, cameras, equipment zones and edge devices.',
  robots: { index: false, follow: false },
};

export const viewport: Viewport = { width: 'device-width', initialScale: 1 };

export default async function RootLayout({ children }: { children: React.ReactNode }) {
  // Render per request so Next can put the middleware's CSP nonce on its scripts.
  await connection();
  return (
    <html lang="en" className="h-full">
      <body className="min-h-full">{children}</body>
    </html>
  );
}
