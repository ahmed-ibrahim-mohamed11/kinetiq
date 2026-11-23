'use client';

import { useActionState } from 'react';
import { Badge, Button, Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@smartgym/ui';
import type { Tables } from '@smartgym/supabase-client/types';
import { togglePromotionStatus } from '@/lib/actions';
import type { ActionResult } from '@/lib/action-result';

type Promotion = Tables<'promotions'>;

function ApprovePromotionButton({ promotionId, isActive }: { promotionId: string; isActive: boolean }) {
  const [, action, pending] = useActionState<ActionResult | null, FormData>(togglePromotionStatus, null);

  return (
    <form action={action} className="inline-block">
      <input type="hidden" name="promotion_id" value={promotionId} />
      <input type="hidden" name="is_active" value={isActive ? 'false' : 'true'} />
      <Button
        type="submit"
        size="sm"
        variant={isActive ? 'outline' : 'default'}
        disabled={pending}
        className="h-7 text-xs px-2.5"
      >
        {pending ? 'Updating…' : isActive ? 'Deactivate' : 'Approve & Activate'}
      </Button>
    </form>
  );
}

export function PromotionsTable({ promotions }: { promotions: Promotion[] }) {
  if (promotions.length === 0) {
    return (
      <div className="rounded-md border border-border p-6 text-center text-sm text-muted-foreground">
        No active promotions or discount campaigns.
      </div>
    );
  }

  return (
    <div className="rounded-md border">
      <Table>
        <TableHeader>
          <TableRow>
            <TableHead>Code</TableHead>
            <TableHead>Campaign</TableHead>
            <TableHead>Benefit</TableHead>
            <TableHead>Redemptions</TableHead>
            <TableHead>Status & Source</TableHead>
            <TableHead className="text-right">Action</TableHead>
          </TableRow>
        </TableHeader>
        <TableBody>
          {promotions.map((p) => {
            const isAiDraft = p.is_ai_generated && !p.is_active;
            const benefitLabel =
              p.discount_type === 'percentage'
                ? `${p.discount_value}% OFF`
                : p.discount_type === 'fixed_amount'
                ? `${p.discount_value} EGP OFF`
                : `+${p.discount_value} Days`;

            return (
              <TableRow key={p.id} className={isAiDraft ? 'bg-amber-500/5' : undefined}>
                <TableCell className="font-mono text-xs font-semibold">{p.code}</TableCell>
                <TableCell>
                  <span className="font-medium text-foreground block">{p.name}</span>
                  {p.description && <span className="text-xs text-muted-foreground block">{p.description}</span>}
                </TableCell>
                <TableCell className="font-semibold text-primary">{benefitLabel}</TableCell>
                <TableCell className="text-xs text-muted-foreground">
                  {p.used_count} {p.usage_limit ? `/ ${p.usage_limit}` : 'used'}
                </TableCell>
                <TableCell>
                  <div className="flex flex-wrap gap-1 items-center">
                    {p.is_ai_generated && (
                      <Badge variant="outline" className="text-[10px] bg-purple-500/10 text-purple-400 border-purple-500/30">
                        AI Recommended
                      </Badge>
                    )}
                    <Badge variant={p.is_active ? 'default' : 'outline'}>
                      {p.is_active ? 'Active' : isAiDraft ? 'Draft Pending Admin' : 'Inactive'}
                    </Badge>
                  </div>
                </TableCell>
                <TableCell className="text-right">
                  <ApprovePromotionButton promotionId={p.id} isActive={p.is_active} />
                </TableCell>
              </TableRow>
            );
          })}
        </TableBody>
      </Table>
    </div>
  );
}

