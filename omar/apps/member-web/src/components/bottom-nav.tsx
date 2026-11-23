'use client';

import Link from 'next/link';
import { usePathname } from 'next/navigation';
import { CreditCard, Dumbbell, History, Home, Settings } from 'lucide-react';
import { cn } from '@smartgym/ui';

const TABS = [
  { href: '/dashboard', label: 'Home', icon: Home },
  { href: '/dashboard#membership', label: 'Membership', icon: CreditCard },
  { href: '/workout', label: 'AI Training', icon: Dumbbell },
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
      <ul className="grid grid-cols-5">
        {TABS.map(({ href, label, icon: Icon }) => {
          const active =
            pathname === href ||
            (href !== '/dashboard' && pathname.startsWith(`${href}/`)) ||
            (href === '/dashboard' && pathname.startsWith('/reports'));
          return (
            <li key={href}>
              <Link
                href={href}
                aria-current={active ? 'page' : undefined}
                className={cn(
                  'flex flex-col items-center gap-1 py-2 text-[10px] font-medium transition-colors',
                  active ? 'text-primary font-semibold' : 'text-muted-foreground hover:text-foreground',
                )}
              >
                <Icon className="h-4 w-4" aria-hidden />
                <span className="truncate max-w-[60px]">{label}</span>
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
        const active = pathname === href || (href !== '/dashboard' && pathname.startsWith(`${href}/`));
        return (
          <Link
            key={href}
            href={href}
            aria-current={active ? 'page' : undefined}
            className={cn(
              'rounded-md px-3 py-1.5 text-xs font-medium transition-colors',
              active ? 'bg-primary/10 text-primary font-semibold' : 'text-muted-foreground hover:text-foreground',
            )}
          >
            {label}
          </Link>
        );
      })}
    </nav>
  );
}
