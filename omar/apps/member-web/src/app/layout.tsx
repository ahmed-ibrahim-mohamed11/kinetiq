import type { Metadata, Viewport } from 'next';
import { connection } from 'next/server';
import '@smartgym/ui/styles.css';

export const metadata: Metadata = {
  title: { default: 'Smart Gym', template: '%s · Smart Gym' },
  description: 'Your automatic workout log: check-ins, sets, reps and daily reports.',
  manifest: '/manifest.webmanifest',
  icons: { icon: '/icon.svg' },
  appleWebApp: { capable: true, title: 'Smart Gym', statusBarStyle: 'default' },
};

export const viewport: Viewport = {
  width: 'device-width',
  initialScale: 1,
  viewportFit: 'cover',
  themeColor: [
    { media: '(prefers-color-scheme: light)', color: '#2563eb' },
    { media: '(prefers-color-scheme: dark)', color: '#0b1220' },
  ],
};

export default async function RootLayout({ children }: { children: React.ReactNode }) {
  // Render per request so Next can put the middleware's CSP nonce on its scripts.
  await connection();
  return (
    <html lang="en" suppressHydrationWarning>
      <body className="min-h-dvh" suppressHydrationWarning>{children}</body>
    </html>
  );
}
