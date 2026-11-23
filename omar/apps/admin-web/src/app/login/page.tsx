import type { Metadata } from 'next';
import { redirect } from 'next/navigation';
import { ShieldCheck } from 'lucide-react';
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@smartgym/ui';
import { getUser } from '@smartgym/supabase-client/server';
import { safeNextPath } from '@smartgym/validation';
import { LoginForm } from './login-form';

export const metadata: Metadata = { title: 'Sign in' };

export default async function LoginPage({ searchParams }: { searchParams: Promise<{ next?: string }> }) {
  const { next } = await searchParams;
  // Only same-origin paths: rejects //host, /\host, control characters and the like.
  const target = safeNextPath(next);
  const user = await getUser();
  if (user) redirect(target);

  return (
    <main className="flex min-h-screen items-center justify-center bg-muted/40 p-4">
      <Card className="w-full max-w-sm">
        <CardHeader className="items-center text-center">
          <div className="mb-2 flex h-11 w-11 items-center justify-center rounded-full bg-primary/10 text-primary">
            <ShieldCheck className="h-6 w-6" aria-hidden />
          </div>
          <CardTitle>Smart Gym Admin</CardTitle>
          <CardDescription>Staff sign-in. Accounts are provisioned by your gym administrator; there is no self sign-up.</CardDescription>
        </CardHeader>
        <CardContent>
          <LoginForm next={target} />
        </CardContent>
      </Card>
    </main>
  );
}
