'use client';

import { useActionState, useEffect, useRef } from 'react';
import { Button, Input, Label, Select } from '@smartgym/ui';
import { createPromotion } from '@/lib/actions';
import type { ActionResult } from '@/lib/action-result';
import { ActionFeedback } from '@/components/action-feedback';

export function CreatePromotionForm({ organizationId }: { organizationId?: string }) {
  const [state, action, pending] = useActionState<ActionResult | null, FormData>(createPromotion, null);
  const formRef = useRef<HTMLFormElement>(null);

  useEffect(() => {
    if (state?.ok) formRef.current?.reset();
  }, [state]);

  return (
    <form ref={formRef} action={action} className="space-y-4 text-sm">
      <input type="hidden" name="organization_id" value={organizationId ?? ''} />

      <div className="grid grid-cols-2 gap-3">
        <div className="space-y-1.5">
          <Label htmlFor="promo_code">Promotion Code</Label>
          <Input id="promo_code" name="code" required placeholder="SUMMER25" className="uppercase" />
        </div>
        <div className="space-y-1.5">
          <Label htmlFor="promo_name">Campaign Name</Label>
          <Input id="promo_name" name="name" required placeholder="Summer Kickstart" />
        </div>
      </div>

      <div className="grid grid-cols-2 gap-3">
        <div className="space-y-1.5">
          <Label htmlFor="discount_type">Discount Type</Label>
          <Select id="discount_type" name="discount_type" defaultValue="percentage">
            <option value="percentage">Percentage Off (%)</option>
            <option value="fixed_amount">Fixed Amount (EGP)</option>
            <option value="extra_days">Bonus Extra Days</option>
          </Select>
        </div>
        <div className="space-y-1.5">
          <Label htmlFor="discount_value">Value</Label>
          <Input id="discount_value" name="discount_value" type="number" required min={0} step="0.01" placeholder="15" />
        </div>
      </div>

      <div className="grid grid-cols-2 gap-3">
        <div className="space-y-1.5">
          <Label htmlFor="usage_limit">Max Redemptions</Label>
          <Input id="usage_limit" name="usage_limit" type="number" min={1} placeholder="Unlimited" />
        </div>
        <div className="space-y-1.5">
          <Label htmlFor="valid_until">Expiration Date</Label>
          <Input id="valid_until" name="valid_until" type="date" />
        </div>
      </div>

      <ActionFeedback state={state} />

      <Button type="submit" disabled={pending} className="w-full">
        {pending ? 'Saving Promotion…' : 'Publish Promotion'}
      </Button>
    </form>
  );
}

