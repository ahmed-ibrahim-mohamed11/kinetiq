import * as React from 'react';
import { cva, type VariantProps } from 'class-variance-authority';
import { cn } from '../lib/utils';

const badgeVariants = cva(
  'inline-flex items-center rounded-full border px-2.5 py-0.5 text-xs font-semibold transition-colors focus:outline-none focus:ring-2 focus:ring-ring focus:ring-offset-2',
  {
    variants: {
      variant: {
        default: 'border-transparent bg-primary text-primary-foreground',
        secondary: 'border-transparent bg-secondary text-secondary-foreground',
        destructive: 'border-transparent bg-destructive text-destructive-foreground',
        success: 'border-transparent bg-success text-success-foreground',
        warning: 'border-transparent bg-warning text-warning-foreground',
        outline: 'text-foreground',
      },
    },
    defaultVariants: { variant: 'default' },
  },
);

export interface BadgeProps extends React.HTMLAttributes<HTMLDivElement>, VariantProps<typeof badgeVariants> {}

export function Badge({ className, variant, ...props }: BadgeProps) {
  return <div className={cn(badgeVariants({ variant }), className)} {...props} />;
}

/** Map common domain statuses to badge variants in one place. */
export function statusVariant(status: string | null | undefined): BadgeProps['variant'] {
  switch (status) {
    case 'active':
    case 'online':
    case 'completed':
    case 'delivered':
    case 'granted':
      return 'success';
    case 'degraded':
    case 'reconnecting':
    case 'finalizing':
    case 'queued':
    case 'sending':
    case 'provisioning':
    case 'frozen':
      return 'warning';
    case 'offline':
    case 'failed':
    case 'dead':
    case 'withdrawn':
    case 'expired':
    case 'cancelled':
    case 'abandoned':
    case 'retired':
      return 'destructive';
    default:
      return 'secondary';
  }
}

export { badgeVariants };
