'use client';

import { useState } from 'react';
import Link from 'next/link';
import { usePathname, useRouter } from 'next/navigation';
import {
  Activity,
  Camera,
  ChevronLeft,
  ChevronRight,
  Cpu,
  CreditCard,
  Dumbbell,
  LayoutDashboard,
  LogOut,
  QrCode,
  ScrollText,
  Search,
  SlidersHorizontal,
  UserPlus,
  Users,
} from 'lucide-react';
import { cn } from '@smartgym/ui';

interface NavGroup {
  title: string;
  items: {
    href: string;
    label: string;
    icon: React.ComponentType<{ className?: string }>;
    exact?: boolean;
  }[];
}

const NAV_SECTIONS: NavGroup[] = [
  {
    title: 'Overview',
    items: [{ href: '/dashboard', label: 'Dashboard', icon: LayoutDashboard, exact: true }],
  },
  {
    title: 'Reception',
    items: [
      { href: '/members?action=new', label: 'Register Member', icon: UserPlus, exact: true },
      { href: '/kiosk', label: 'Check-in Kiosk', icon: QrCode, exact: true },
    ],
  },
  {
    title: 'Members',
    items: [{ href: '/members', label: 'Member Directory', icon: Users, exact: true }],
  },
  {
    title: 'Business',
    items: [{ href: '/subscriptions', label: 'Plans & Subscriptions', icon: CreditCard, exact: false }],
  },
  {
    title: 'AI Operations',
    items: [
      { href: '/sessions', label: 'Live Sessions', icon: Activity, exact: false },
      { href: '/cameras', label: 'Cameras & Zones', icon: Camera, exact: false },
      { href: '/equipment', label: 'Equipment', icon: Dumbbell, exact: false },
      { href: '/diagnostics', label: 'AI Diagnostics', icon: SlidersHorizontal, exact: false },
    ],
  },
  {
    title: 'Administration',
    items: [
      { href: '/devices', label: 'Edge Devices', icon: Cpu, exact: false },
      { href: '/audit', label: 'Audit Logs', icon: ScrollText, exact: false },
    ],
  },
];

export function Nav({ email, roles }: { email: string; roles: string[] }) {
  const pathname = usePathname();
  const router = useRouter();
  const [collapsed, setCollapsed] = useState(false);
  const [quickQuery, setQuickQuery] = useState('');

  const isCurrent = (href: string, exact?: boolean) => {
    const baseHref = href.split('?')[0]!;
    if (exact) {
      return pathname === baseHref;
    }
    return pathname === baseHref || pathname.startsWith(`${baseHref}/`);
  };

  const handleQuickSearch = (e: React.FormEvent) => {
    e.preventDefault();
    const q = quickQuery.trim();
    if (q) {
      router.push(`/members?q=${encodeURIComponent(q)}`);
    } else {
      router.push('/members');
    }
  };

  return (
    <>
      {/* Desktop sidebar */}
      <aside
        className={cn(
          'hidden shrink-0 flex-col border-r bg-card transition-all duration-200 md:flex md:min-h-screen',
          collapsed ? 'w-20' : 'w-64',
        )}
      >
        {/* Brand & user banner */}
        <div className="relative border-b px-4 py-3.5">
          <div className="flex items-center justify-between">
            {!collapsed && (
              <div className="min-w-0 flex-1">
                <p className="text-base font-bold tracking-tight text-foreground flex items-center gap-2">
                  <span className="flex h-6 w-6 items-center justify-center rounded-md bg-primary text-[11px] font-black text-primary-foreground">
                    KQ
                  </span>
                  KinetiQ
                </p>
                <p className="truncate text-xs text-muted-foreground mt-0.5" title={email}>
                  {email}
                </p>
                {roles.length > 0 && (
                  <p className="text-[10px] uppercase font-semibold tracking-wider text-primary/80 mt-0.5">
                    {roles.join(', ')}
                  </p>
                )}
              </div>
            )}
            {collapsed && (
              <div className="mx-auto flex h-8 w-8 items-center justify-center rounded-lg bg-primary text-xs font-bold text-primary-foreground">
                KQ
              </div>
            )}

            <button
              type="button"
              onClick={() => setCollapsed(!collapsed)}
              title={collapsed ? 'Expand sidebar' : 'Collapse sidebar'}
              aria-label={collapsed ? 'Expand sidebar' : 'Collapse sidebar'}
              className="ml-auto rounded-md p-1.5 text-muted-foreground hover:bg-accent hover:text-accent-foreground transition-colors"
            >
              {collapsed ? <ChevronRight className="h-4 w-4" /> : <ChevronLeft className="h-4 w-4" />}
            </button>
          </div>

          {/* Quick search input (visible when expanded) */}
          {!collapsed && (
            <form onSubmit={handleQuickSearch} className="mt-3">
              <div className="relative">
                <Search className="absolute left-2.5 top-1/2 -translate-y-1/2 h-3.5 w-3.5 text-muted-foreground" />
                <input
                  type="text"
                  placeholder="Quick find member…"
                  value={quickQuery}
                  onChange={(e) => setQuickQuery(e.target.value)}
                  className="w-full rounded-md border border-input bg-background/50 pl-8 pr-2.5 py-1 text-xs text-foreground placeholder:text-muted-foreground focus:outline-none focus:ring-1 focus:ring-primary"
                />
              </div>
            </form>
          )}
        </div>

        {/* Sectioned Navigation list */}
        <nav className="flex-1 space-y-4 p-3 overflow-y-auto" aria-label="Main Navigation">
          {NAV_SECTIONS.map((section) => (
            <div key={section.title} className="space-y-1">
              {!collapsed && (
                <p className="px-2.5 text-[10px] font-bold uppercase tracking-wider text-muted-foreground/70">
                  {section.title}
                </p>
              )}
              {section.items.map(({ href, label, icon: Icon, exact }) => {
                const active = isCurrent(href, exact);
                return (
                  <Link
                    key={href}
                    href={href}
                    aria-current={active ? 'page' : undefined}
                    title={collapsed ? label : undefined}
                    className={cn(
                      'flex items-center gap-3 rounded-lg px-2.5 py-2 text-xs font-medium transition-colors',
                      active
                        ? 'bg-primary/10 text-primary font-semibold shadow-xs'
                        : 'text-muted-foreground hover:bg-accent hover:text-accent-foreground',
                      collapsed && 'justify-center px-2',
                    )}
                  >
                    <Icon className="h-4 w-4 shrink-0" aria-hidden />
                    {!collapsed && <span className="truncate">{label}</span>}
                  </Link>
                );
              })}
            </div>
          ))}
        </nav>

        {/* Footer sign out */}
        <div className="border-t p-3">
          <form action="/auth/signout" method="post">
            <button
              type="submit"
              title={collapsed ? 'Sign out' : undefined}
              className={cn(
                'flex w-full items-center gap-3 rounded-lg px-2.5 py-2 text-xs font-medium text-muted-foreground transition-colors hover:bg-accent hover:text-accent-foreground',
                collapsed && 'justify-center px-2',
              )}
            >
              <LogOut className="h-4 w-4 shrink-0" aria-hidden />
              {!collapsed && <span>Sign out</span>}
            </button>
          </form>
        </div>
      </aside>

      {/* Mobile top bar */}
      <header className="sticky top-0 z-20 border-b bg-card md:hidden">
        <div className="flex items-center justify-between px-4 py-3">
          <div className="flex items-center gap-2">
            <span className="flex h-6 w-6 items-center justify-center rounded-md bg-primary text-[11px] font-black text-primary-foreground">
              KQ
            </span>
            <p className="text-sm font-bold">KinetiQ Admin</p>
          </div>
          <form action="/auth/signout" method="post">
            <button
              type="submit"
              className="flex items-center gap-1.5 text-xs text-muted-foreground hover:text-foreground"
              aria-label="Sign out"
            >
              <LogOut className="h-4 w-4" aria-hidden /> Sign out
            </button>
          </form>
        </div>
        <nav className="flex gap-1 overflow-x-auto px-2 pb-2" aria-label="Mobile Navigation">
          {NAV_SECTIONS.flatMap((s) => s.items).map(({ href, label, icon: Icon, exact }) => {
            const active = isCurrent(href, exact);
            return (
              <Link
                key={href}
                href={href}
                aria-current={active ? 'page' : undefined}
                className={cn(
                  'flex shrink-0 items-center gap-1.5 rounded-md px-2.5 py-1.5 text-xs font-medium',
                  active ? 'bg-primary/10 text-primary font-semibold' : 'text-muted-foreground',
                )}
              >
                <Icon className="h-3.5 w-3.5" aria-hidden />
                {label}
              </Link>
            );
          })}
        </nav>
      </header>
    </>
  );
}
