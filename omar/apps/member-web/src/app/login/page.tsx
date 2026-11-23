import type { Metadata } from 'next';
import { redirect } from 'next/navigation';
import { getUser } from '@smartgym/supabase-client/server';
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@smartgym/ui';
import { LoginForm } from './login-form';

export const metadata: Metadata = { title: 'Sign in' };

export default async function LoginPage({ searchParams }: { searchParams: Promise<{ next?: string; error?: string }> }) {
  const params = await searchParams;
  const user = await getUser();
  if (user) redirect('/dashboard');
  const next = params.next && params.next.startsWith('/') && !params.next.startsWith('//') ? params.next : '/dashboard';

  return (
    <main className="flex min-h-dvh flex-col items-center justify-center px-4 py-10">
      <div className="mb-6 flex items-center gap-3">
        <img src="/icon.svg" alt="" width={40} height={40} className="rounded-lg" />
        <div>
          <p className="text-xl font-semibold leading-tight">Smart Gym</p>
          <p className="text-sm text-muted-foreground">Member dashboard</p>
        </div>
      </div>
      <Card className="w-full max-w-sm">
        <CardHeader>
          <CardTitle>Sign in</CardTitle>
          <CardDescription>Use the email registered with your gym membership.</CardDescription>
        </CardHeader>
        <CardContent>
          <LoginForm next={next} initialError={params.error === 'link_invalid' ? 'That sign-in link is invalid or has expired. Request a new one.' : null} />
          <p className="mt-6 text-xs text-muted-foreground">
            There is no self sign-up. Member accounts are created by gym staff at the front desk; if you have not received an invitation, ask them to link your email to your membership.
          </p>
        </CardContent>
      </Card>
    </main>
  );
}
