'use client';

import { Badge, Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@smartgym/ui';
import type { Tables } from '@smartgym/supabase-client/types';

type MembershipPlan = Tables<'membership_plans'>;

export function PlansTable({ plans }: { plans: MembershipPlan[] }) {
  if (plans.length === 0) {
    return (
      <div className="rounded-md border border-border p-6 text-center text-sm text-muted-foreground">
        No membership plans configured yet. Create a package to start enrolling members.
      </div>
    );
  }

  return (
    <div className="rounded-md border">
      <Table>
        <TableHeader>
          <TableRow>
            <TableHead>Code</TableHead>
            <TableHead>Package Name</TableHead>
            <TableHead>Price</TableHead>
            <TableHead>Duration</TableHead>
            <TableHead>Freeze Policy</TableHead>
            <TableHead>AI Feature</TableHead>
            <TableHead className="text-right">Status</TableHead>
          </TableRow>
        </TableHeader>
        <TableBody>
          {plans.map((p) => (
            <TableRow key={p.id}>
              <TableCell className="font-mono text-xs font-semibold">{p.code}</TableCell>
              <TableCell className="font-medium text-foreground">
                {p.name}
                {p.included_sessions && (
                  <span className="block text-xs text-muted-foreground">{p.included_sessions} sessions</span>
                )}
              </TableCell>
              <TableCell className="font-semibold">
                {p.price.toLocaleString()} {p.currency}
              </TableCell>
              <TableCell className="text-muted-foreground">
                {p.duration_days} days ({Math.round((p.duration_days / 30) * 10) / 10} mo)
              </TableCell>
              <TableCell className="text-xs text-muted-foreground">
                {p.freeze_allowance_days > 0 ? (
                  <span>
                    Up to {p.freeze_allowance_days}d ({p.max_freeze_count}x)
                  </span>
                ) : (
                  <span>No freezes</span>
                )}
              </TableCell>
              <TableCell>
                {p.ai_training_included ? (
                  <Badge variant="default" className="bg-primary/20 text-primary border-primary/30">
                    AI Included
                  </Badge>
                ) : (
                  <span className="text-xs text-muted-foreground">Add-on only</span>
                )}
              </TableCell>
              <TableCell className="text-right">
                <Badge variant={p.is_active ? 'default' : 'outline'}>
                  {p.is_active ? 'Active' : 'Archived'}
                </Badge>
              </TableCell>
            </TableRow>
          ))}
        </TableBody>
      </Table>
    </div>
  );
}

