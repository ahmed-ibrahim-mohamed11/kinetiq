'use client';

import Link from 'next/link';
import { usePathname } from 'next/navigation';
import { Activity, Camera, Cpu, Dumbbell, LayoutDashboard, LogOut, QrCode, ScrollText, Users } from 'lucide-react';
import { cn } from '@smartgym/ui';

const NAV = [
  { href: '/dashboard', label: 'Dashboard', icon: LayoutDashboard },
  { href: '/kiosk', label: 'Check-in Kiosk', icon: QrCode },
  { href: '/sessions', label: 'Live Sessions', icon: Activity },
  { href: '/members', label: 'Members', icon: Users },
  { href: '/equipment', label: 'Equipment', icon: Dumbbell },
  { href: '/cameras', label: 'Cameras & Zones', icon: Camera },
  { href: '/devices', label: 'Edge Devices', icon: Cpu },
  { href: '/audit', label: 'Audit', icon: ScrollText },
] as const;

export function Nav({ email, roles }: { email: string; roles: string[] }) {
  const pathname = usePathname();
  const isActive = (href: string) => pathname === href || pathname.startsWith(`${href}/`);

  return (
    <>
      {/* Desktop sidebar */}
      <aside className="hidden w-64 shrink-0 flex-col border-r bg-card md:flex md:min-h-screen">
        <div className="border-b px-5 py-4">
          <p className="text-base font-semibold tracking-tight">Smart Gym Admin</p>
          <p className="truncate text-xs text-muted-foreground" title={email}>
            {email}
          </p>
          {roles.length > 0 && <p className="mt-1 text-xs text-muted-foreground">{roles.join(', ')}</p>}
        </div>
        <nav className="flex-1 space-y-1 p-3" aria-label="Main">
          {NAV.map(({ href, label, icon: Icon }) => (
            <Link
              key={href}
              href={href}
              aria-current={isActive(href) ? 'page' : undefined}
              className={cn(
                'flex items-center gap-3 rounded-md px-3 py-2 text-sm font-medium transition-colors',
                isActive(href) ? 'bg-primary/10 text-primary' : 'text-muted-foreground hover:bg-accent hover:text-accent-foreground',
              )}
            >
              <Icon className="h-4 w-4" aria-hidden />
              {label}
            </Link>
          ))}
        </nav>
        <div className="border-t p-3">
          <form action="/auth/signout" method="post">
            <button
              type="submit"
              className="flex w-full items-center gap-3 rounded-md px-3 py-2 text-sm font-medium text-muted-foreground transition-colors hover:bg-accent hover:text-accent-foreground"
            >
              <LogOut className="h-4 w-4" aria-hidden />
              Sign out
            </button>
          </form>
        </div>
      </aside>

      {/* Mobile top bar */}
      <header className="sticky top-0 z-20 border-b bg-card md:hidden">
        <div className="flex items-center justify-between px-4 py-3">
          <p className="text-sm font-semibold">Smart Gym Admin</p>
          <form action="/auth/signout" method="post">
            <button type="submit" className="flex items-center gap-1 text-xs text-muted-foreground" aria-label="Sign out">
              <LogOut className="h-4 w-4" aria-hidden /> Sign out
            </button>
          </form>
        </div>
        <nav className="flex gap-1 overflow-x-auto px-2 pb-2" aria-label="Main">
          {NAV.map(({ href, label, icon: Icon }) => (
            <Link
              key={href}
              href={href}
              aria-current={isActive(href) ? 'page' : undefined}
              className={cn(
                'flex shrink-0 items-center gap-1.5 rounded-md px-3 py-1.5 text-xs font-medium',
                isActive(href) ? 'bg-primary/10 text-primary' : 'text-muted-foreground',
              )}
            >
              <Icon className="h-3.5 w-3.5" aria-hidden />
              {label}
            </Link>
          ))}
        </nav>
      </header>
    </>
  );
}
