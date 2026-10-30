import { redirect } from 'next/navigation';
import { ShieldAlert } from 'lucide-react';
import { Button, Card, CardContent, CardDescription, CardHeader, CardTitle } from '@smartgym/ui';
import { getUser } from '@smartgym/supabase-client/server';
import { Nav } from '@/components/nav';
import { getStaffRoles } from '@smartgym/supabase-client/server';

export default async function DashboardLayout({ children }: { children: React.ReactNode }) {
  const user = await getUser();
  if (!user) redirect('/login');
  const roles = await getStaffRoles();

  if (roles.length === 0) {
    return (
      <main className="flex min-h-screen items-center justify-center bg-muted/40 p-4">
        <Card className="w-full max-w-md">
          <CardHeader className="items-center text-center">
            <div className="mb-2 flex h-11 w-11 items-center justify-center rounded-full bg-destructive/10 text-destructive">
              <ShieldAlert className="h-6 w-6" aria-hidden />
            </div>
            <CardTitle>Access restricted to staff</CardTitle>
            <CardDescription>
              The account <span className="font-medium text-foreground">{user.email}</span> has no staff role. Members use the member app; staff
              accounts are granted a role by a gym owner or administrator.
            </CardDescription>
          </CardHeader>
          <CardContent className="flex justify-center">
            <form action="/auth/signout" method="post">
              <Button type="submit" variant="outline">
                Sign out
              </Button>
            </form>
          </CardContent>
        </Card>
      </main>
    );
  }

  const roleNames = Array.from(new Set(roles.map((r) => r.role)));

  return (
    <div className="min-h-screen md:flex">
      <Nav email={user.email ?? ''} roles={roleNames} />
      <main className="min-w-0 flex-1 p-4 md:p-8">{children}</main>
    </div>
  );
}
