'use client';

import Link from 'next/link';
import { usePathname } from 'next/navigation';
import { History, Home, Settings } from 'lucide-react';
import { cn } from '@smartgym/ui';

const TABS = [
  { href: '/dashboard', label: 'Home', icon: Home },
  { href: '/history', label: 'History', icon: History },
  { href: '/settings', label: 'Settings', icon: Settings },
];

export function BottomNav() {
  const pathname = usePathname();
  return (
    <nav
      aria-label="Primary"
      className="fixed inset-x-0 bottom-0 z-20 border-t bg-background/95 backdrop-blur supports-[backdrop-filter]:bg-background/80 print:hidden md:hidden"
      style={{ paddingBottom: 'env(safe-area-inset-bottom)' }}
    >
      <ul className="grid grid-cols-3">
        {TABS.map(({ href, label, icon: Icon }) => {
          const active = pathname === href || pathname.startsWith(`${href}/`) || (href === '/dashboard' && pathname.startsWith('/reports'));
          return (
            <li key={href}>
              <Link
                href={href}
                aria-current={active ? 'page' : undefined}
                className={cn('flex flex-col items-center gap-1 py-2.5 text-xs font-medium', active ? 'text-primary' : 'text-muted-foreground')}
              >
                <Icon className="h-5 w-5" aria-hidden />
                {label}
              </Link>
            </li>
          );
        })}
      </ul>
    </nav>
  );
}

export function DesktopNav() {
  const pathname = usePathname();
  return (
    <nav aria-label="Primary" className="hidden items-center gap-1 md:flex">
      {TABS.map(({ href, label }) => {
        const active = pathname === href || pathname.startsWith(`${href}/`);
        return (
          <Link
            key={href}
            href={href}
            aria-current={active ? 'page' : undefined}
            className={cn('rounded-md px-3 py-1.5 text-sm font-medium', active ? 'bg-accent text-accent-foreground' : 'text-muted-foreground hover:text-foreground')}
          >
            {label}
          </Link>
        );
      })}
    </nav>
  );
}
