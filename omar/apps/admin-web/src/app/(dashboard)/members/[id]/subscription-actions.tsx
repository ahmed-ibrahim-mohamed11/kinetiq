'use client';

import { useActionState, useState } from 'react';
import { Button, Input, Label, Select } from '@smartgym/ui';
import { freezeSubscription, unfreezeSubscription, renewSubscriptionAction } from '@/lib/actions';
import type { ActionResult } from '@/lib/action-result';
import { ActionFeedback } from '@/components/action-feedback';
import type { Tables } from '@smartgym/supabase-client/types';

type MembershipPlan = Pick<Tables<'membership_plans'>, 'id' | 'name' | 'price' | 'currency' | 'duration_days' | 'ai_training_included'>;

export function FreezeSubscriptionModal({
  subscriptionId,
  maxDaysRemaining,
}: {
  subscriptionId: string;
  maxDaysRemaining: number;
}) {
  const [open, setOpen] = useState(false);
  const [state, action, pending] = useActionState<ActionResult | null, FormData>(freezeSubscription, null);

  if (!open) {
    return (
      <Button size="sm" variant="outline" onClick={() => setOpen(true)}>
        Freeze Subscription
      </Button>
    );
  }

  return (
    <div className="rounded-lg border border-border bg-card p-4 shadow-sm space-y-3">
      <div className="flex items-center justify-between">
        <h5 className="font-semibold text-sm">Freeze Membership</h5>
        <button type="button" onClick={() => setOpen(false)} className="text-xs text-muted-foreground hover:text-foreground">
          ✕ Cancel
        </button>
      </div>
      <p className="text-xs text-muted-foreground">
        Freezing extends the member&apos;s expiration date by the frozen duration. Max allowance left: {maxDaysRemaining} days.
      </p>
      <form action={action} className="space-y-3 text-sm">
        <input type="hidden" name="subscription_id" value={subscriptionId} />
        <div className="grid grid-cols-2 gap-3">
          <div className="space-y-1">
            <Label htmlFor="freeze_days">Freeze Days</Label>
            <Input id="freeze_days" name="days" type="number" required min={1} max={Math.max(1, maxDaysRemaining)} defaultValue={7} />
          </div>
          <div className="space-y-1">
            <Label htmlFor="freeze_reason">Reason</Label>
            <Input id="freeze_reason" name="reason" placeholder="Travel / Medical" required />
          </div>
        </div>
        <ActionFeedback state={state} />
        <Button type="submit" disabled={pending} size="sm" className="w-full">
          {pending ? 'Processing Freeze…' : 'Confirm Freeze'}
        </Button>
      </form>
    </div>
  );
}

export function UnfreezeSubscriptionButton({ subscriptionId }: { subscriptionId: string }) {
  const [state, action, pending] = useActionState<ActionResult | null, FormData>(unfreezeSubscription, null);

  return (
    <form action={action} className="inline-block">
      <input type="hidden" name="subscription_id" value={subscriptionId} />
      <ActionFeedback state={state} />
      <Button type="submit" size="sm" variant="default" disabled={pending} className="bg-emerald-600 hover:bg-emerald-700">
        {pending ? 'Unfreezing…' : 'Unfreeze & Resume'}
      </Button>
    </form>
  );
}

export function RenewSubscriptionModal({
  memberId,
  branchId,
  plans,
}: {
  memberId: string;
  branchId: string;
  plans: MembershipPlan[];
}) {
  const [open, setOpen] = useState(false);
  const [state, action, pending] = useActionState<ActionResult | null, FormData>(renewSubscriptionAction, null);

  if (!open) {
    return (
      <Button size="sm" variant="default" onClick={() => setOpen(true)}>
        Renew / Upgrade Subscription
      </Button>
    );
  }

  return (
    <div className="rounded-lg border border-border bg-card p-4 shadow-sm space-y-3">
      <div className="flex items-center justify-between">
        <h5 className="font-semibold text-sm">Renew or Upgrade Membership</h5>
        <button type="button" onClick={() => setOpen(false)} className="text-xs text-muted-foreground hover:text-foreground">
          ✕ Cancel
        </button>
      </div>
      <form action={action} className="space-y-3 text-sm">
        <input type="hidden" name="member_id" value={memberId} />
        <input type="hidden" name="branch_id" value={branchId} />

        <div className="space-y-1">
          <Label htmlFor="renew_plan_id">Select Package</Label>
          <Select id="renew_plan_id" name="plan_id" required defaultValue={plans[0]?.id}>
            {plans.map((p) => (
              <option key={p.id} value={p.id}>
                {p.name} — {p.price} {p.currency} ({p.duration_days}d)
              </option>
            ))}
          </Select>
        </div>

        <div className="grid grid-cols-2 gap-3">
          <div className="space-y-1">
            <Label htmlFor="renew_payment_method">Payment Method</Label>
            <Select id="renew_payment_method" name="payment_method" defaultValue="cash">
              <option value="cash">Cash</option>
              <option value="credit_card">Credit Card</option>
              <option value="pos_terminal">POS Terminal</option>
              <option value="bank_transfer">InstaPay / Bank</option>
            </Select>
          </div>
          <div className="space-y-1">
            <Label htmlFor="renew_promo_code">Promo Code</Label>
            <Input id="renew_promo_code" name="promo_code" placeholder="Optional" className="uppercase" />
          </div>
        </div>

        <div className="rounded-md border border-border p-2 bg-muted/20">
          <label className="flex items-center gap-2 text-xs cursor-pointer">
            <input type="checkbox" name="ai_addon_selected" className="h-4 w-4 rounded border-border text-primary focus:ring-primary" />
            <span className="font-medium text-foreground">Include AI Workout Tracking Add-on (+150 EGP/mo)</span>
          </label>
        </div>

        <ActionFeedback state={state} />

        <Button type="submit" disabled={pending} size="sm" className="w-full">
          {pending ? 'Processing Renewal…' : 'Confirm Renewal & Record Payment'}
        </Button>
      </form>
    </div>
  );
}

