'use client';

import { useActionState, useEffect, useRef } from 'react';
import { Button, Input, Label, Select } from '@smartgym/ui';
import { createMembershipPlan } from '@/lib/actions';
import type { ActionResult } from '@/lib/action-result';
import { ActionFeedback } from '@/components/action-feedback';

export function CreatePlanForm({ organizationId }: { organizationId?: string }) {
  const [state, action, pending] = useActionState<ActionResult | null, FormData>(createMembershipPlan, null);
  const formRef = useRef<HTMLFormElement>(null);

  useEffect(() => {
    if (state?.ok) formRef.current?.reset();
  }, [state]);

  return (
    <form ref={formRef} action={action} className="space-y-4 text-sm">
      <input type="hidden" name="organization_id" value={organizationId ?? ''} />

      <div className="space-y-1.5">
        <Label htmlFor="plan_name">Package Name</Label>
        <Input id="plan_name" name="name" required placeholder="e.g. 3 Months Pro Plus" minLength={2} maxLength={80} />
      </div>

      <div className="grid grid-cols-2 gap-3">
        <div className="space-y-1.5">
          <Label htmlFor="plan_code">Code</Label>
          <Input id="plan_code" name="code" required placeholder="PLAN_3M_PRO" className="uppercase" />
        </div>
        <div className="space-y-1.5">
          <Label htmlFor="plan_price">Price (EGP)</Label>
          <Input id="plan_price" name="price" type="number" required min={0} step="0.01" placeholder="2200" />
        </div>
      </div>

      <div className="grid grid-cols-2 gap-3">
        <div className="space-y-1.5">
          <Label htmlFor="duration_days">Duration (Days)</Label>
          <Input id="duration_days" name="duration_days" type="number" required min={1} defaultValue={90} />
          <p className="text-[11px] text-muted-foreground">This plan is valid for the configured duration days.</p>
        </div>
        <div className="space-y-1.5">
          <Label htmlFor="included_sessions">Sessions (blank for unlimited)</Label>
          <Input id="included_sessions" name="included_sessions" type="number" min={1} placeholder="Unlimited" />
        </div>
      </div>

      <div className="grid grid-cols-2 gap-3">
        <div className="space-y-1.5">
          <Label htmlFor="freeze_allowance_days">Max Freeze Days</Label>
          <Input id="freeze_allowance_days" name="freeze_allowance_days" type="number" min={0} defaultValue={14} />
          <p className="text-[11px] text-muted-foreground">Freezing temporarily pauses the active membership.</p>
        </div>
        <div className="space-y-1.5">
          <Label htmlFor="max_freeze_count">Max Freezes Allowed</Label>
          <Input id="max_freeze_count" name="max_freeze_count" type="number" min={0} defaultValue={1} />
        </div>
      </div>

      <div className="rounded-md border border-border p-3 space-y-2 bg-muted/20">
        <label className="flex items-center gap-2 cursor-pointer">
          <input type="checkbox" name="ai_training_included" className="h-4 w-4 rounded border-border text-primary focus:ring-primary" />
          <span className="font-medium text-foreground">Include AI Workout Tracking (Free in Package)</span>
        </label>
        <div className="space-y-1 pt-1">
          <Label htmlFor="ai_addon_fee">Optional AI Add-on Price (EGP)</Label>
          <Input id="ai_addon_fee" name="ai_addon_fee" type="number" min={0} defaultValue={150} />
        </div>
        <p className="text-xs text-muted-foreground">
          Configure the optional AI fee charged when members choose to add AI camera tracking to this package.
        </p>
      </div>

      <ActionFeedback state={state} />

      <Button type="submit" disabled={pending} className="w-full">
        {pending ? 'Saving Package…' : 'Create Package'}
      </Button>
    </form>
  );
}

