import Link from 'next/link';
import { redirect } from 'next/navigation';
import { LogOut } from 'lucide-react';
import { createClient, getCurrentMember, getUser } from '@smartgym/supabase-client/server';
import { Button, Card, CardContent, CardDescription, CardHeader, CardTitle } from '@smartgym/ui';
import { BottomNav, DesktopNav } from '@/components/bottom-nav';

function SignOutButton({ compact = false }: { compact?: boolean }) {
  return (
    <form action="/auth/signout" method="post">
      <Button type="submit" variant={compact ? 'ghost' : 'outline'} size={compact ? 'icon' : 'default'} aria-label="Sign out" title="Sign out">
        <LogOut className="h-4 w-4" aria-hidden />
        {!compact && 'Sign out'}
      </Button>
    </form>
  );
}

export default async function AppLayout({ children }: { children: React.ReactNode }) {
  const user = await getUser();
  if (!user) redirect('/login');

  const member = await getCurrentMember();
  if (!member) {
    return (
      <main className="flex min-h-dvh flex-col items-center justify-center px-4 py-10">
        <Card className="w-full max-w-md">
          <CardHeader>
            <CardTitle>Your account is not linked to a membership yet</CardTitle>
            <CardDescription>
              You are signed in as <span className="font-medium text-foreground">{user.email}</span>, but no membership is connected to this email.
            </CardDescription>
          </CardHeader>
          <CardContent className="space-y-4 text-sm">
            <p>Ask the front desk to link this email to your member profile. Once that is done, sign in again and your dashboard will appear here.</p>
            <SignOutButton />
          </CardContent>
        </Card>
      </main>
    );
  }

  const supabase = await createClient();
  const { data: branch } = await supabase.from('branches').select('name, timezone').eq('id', member.branch_id).maybeSingle();

  return (
    <div className="flex min-h-dvh flex-col">
      <header className="sticky top-0 z-20 border-b bg-background/95 backdrop-blur supports-[backdrop-filter]:bg-background/80 print:hidden">
        <div className="mx-auto flex h-14 w-full max-w-3xl items-center justify-between gap-3 px-4">
          <Link href="/dashboard" className="flex min-w-0 items-center gap-2">
            <img src="/icon.svg" alt="" width={28} height={28} className="rounded-md" />
            <span className="min-w-0">
              <span className="block truncate text-sm font-semibold leading-tight">{branch?.name ?? 'Smart Gym'}</span>
              <span className="block truncate text-xs text-muted-foreground">{member.full_name}</span>
            </span>
          </Link>
          <div className="flex items-center gap-2">
            <DesktopNav />
            <SignOutButton compact />
          </div>
        </div>
      </header>
      <main className="mx-auto w-full max-w-3xl flex-1 px-4 pb-24 pt-5 md:pb-10">{children}</main>
      <BottomNav />
    </div>
  );
}
