'use client';

import { useActionState, useEffect, useRef } from 'react';
import { Button, Input, Label, Select } from '@smartgym/ui';
import { createEquipment } from '@/lib/actions';
import type { ActionResult } from '@/lib/action-result';
import { ActionFeedback } from '@/components/action-feedback';

const CATEGORIES = [
  { value: 'machine', label: 'Machine' },
  { value: 'cable', label: 'Cable station' },
  { value: 'free_weight_station', label: 'Free-weight station' },
  { value: 'cardio', label: 'Cardio' },
  { value: 'bodyweight_station', label: 'Bodyweight station' },
] as const;

export function EquipmentForm({ branches, exercises }: { branches: { id: string; name: string }[]; exercises: { id: string; name: string; slug: string }[] }) {
  const [state, action, pending] = useActionState<ActionResult | null, FormData>(createEquipment, null);
  const formRef = useRef<HTMLFormElement>(null);
  useEffect(() => {
    if (state?.ok) formRef.current?.reset();
  }, [state]);

  return (
    <form ref={formRef} action={action} className="space-y-4">
      <div className="space-y-2">
        <Label htmlFor="eq_branch_id">Branch</Label>
        <Select id="eq_branch_id" name="branch_id" required defaultValue={branches.length === 1 ? branches[0]!.id : ''}>
          {branches.length !== 1 && <option value="">Select a branch…</option>}
          {branches.map((b) => (
            <option key={b.id} value={b.id}>
              {b.name}
            </option>
          ))}
        </Select>
      </div>
      <div className="space-y-2">
        <Label htmlFor="eq_name">Name</Label>
        <Input id="eq_name" name="name" required maxLength={100} placeholder="Lat Pulldown #1" />
      </div>
      <div className="space-y-2">
        <Label htmlFor="eq_category">Category</Label>
        <Select id="eq_category" name="category" required defaultValue="machine">
          {CATEGORIES.map((c) => (
            <option key={c.value} value={c.value}>
              {c.label}
            </option>
          ))}
        </Select>
      </div>
      <div className="space-y-2">
        <Label htmlFor="eq_exercise">Default exercise</Label>
        <Select id="eq_exercise" name="default_exercise_id" defaultValue="">
          <option value="">None</option>
          {exercises.map((e) => (
            <option key={e.id} value={e.id}>
              {e.name}
            </option>
          ))}
        </Select>
      </div>
      <div className="grid grid-cols-2 gap-3">
        <div className="space-y-2">
          <Label htmlFor="eq_x">Position X (m)</Label>
          <Input id="eq_x" name="position_x_m" type="number" step="0.1" min="0" placeholder="e.g. 5.0" />
        </div>
        <div className="space-y-2">
          <Label htmlFor="eq_y">Position Y (m)</Label>
          <Input id="eq_y" name="position_y_m" type="number" step="0.1" min="0" placeholder="e.g. 4.0" />
        </div>
      </div>
      <label className="flex items-center gap-2 text-sm">
        <input type="checkbox" name="enabled" defaultChecked className="h-4 w-4 rounded border-input" />
        Enabled
      </label>
      <ActionFeedback state={state} />
      <Button type="submit" disabled={pending} className="w-full">
        {pending ? 'Saving…' : 'Add equipment'}
      </Button>
    </form>
  );
}
