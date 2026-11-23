import * as React from 'react';
import { cn } from '../lib/utils';

export function StatTile({
  label,
  value,
  hint,
  icon,
  className,
}: {
  label: string;
  value: React.ReactNode;
  hint?: React.ReactNode;
  icon?: React.ReactNode;
  className?: string;
}) {
  return (
    <div className={cn('rounded-lg border bg-card p-5 text-card-foreground shadow-sm', className)}>
      <div className="flex items-center justify-between">
        <p className="text-sm font-medium text-muted-foreground">{label}</p>
        {icon && <span className="text-muted-foreground">{icon}</span>}
      </div>
      <p className="mt-2 text-3xl font-semibold tabular-nums tracking-tight">{value}</p>
      {hint && <p className="mt-1 text-xs text-muted-foreground">{hint}</p>}
    </div>
  );
}

export function LiveDot({ active, className }: { active: boolean; className?: string }) {
  return (
    <span
      aria-label={active ? 'live' : 'idle'}
      className={cn('inline-block h-2.5 w-2.5 rounded-full', active ? 'bg-success animate-pulse-dot' : 'bg-muted-foreground/40', className)}
    />
  );
}

export function Alert({ variant = 'info', title, children }: { variant?: 'info' | 'warning' | 'error' | 'success'; title?: string; children: React.ReactNode }) {
  const styles = {
    info: 'border-primary/30 bg-primary/5 text-foreground',
    warning: 'border-warning/40 bg-warning/10 text-foreground',
    error: 'border-destructive/40 bg-destructive/10 text-foreground',
    success: 'border-success/40 bg-success/10 text-foreground',
  }[variant];
  return (
    <div role="alert" className={cn('rounded-md border p-4 text-sm', styles)}>
      {title && <p className="mb-1 font-medium">{title}</p>}
      <div>{children}</div>
    </div>
  );
}
