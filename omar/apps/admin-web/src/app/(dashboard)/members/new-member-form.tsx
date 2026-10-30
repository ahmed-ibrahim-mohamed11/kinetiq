'use client';

import { useActionState, useEffect, useRef } from 'react';
import { Button, Input, Label, Select } from '@smartgym/ui';
import { createMember } from '@/lib/actions';
import type { ActionResult } from '@/lib/action-result';
import { ActionFeedback } from '@/components/action-feedback';
import type { BranchOption } from './types';

export function NewMemberForm({ branches }: { branches: BranchOption[] }) {
  const [state, action, pending] = useActionState<ActionResult | null, FormData>(createMember, null);
  const formRef = useRef<HTMLFormElement>(null);

  useEffect(() => {
    if (state?.ok) formRef.current?.reset();
  }, [state]);

  return (
    <form ref={formRef} action={action} className="space-y-4">
      <div className="space-y-2">
        <Label htmlFor="branch_id">Branch</Label>
        <Select id="branch_id" name="branch_id" required defaultValue={branches.length === 1 ? branches[0]!.id : ''}>
          {branches.length !== 1 && <option value="">Select a branch…</option>}
          {branches.map((b) => (
            <option key={b.id} value={b.id}>
              {b.name}
            </option>
          ))}
        </Select>
      </div>
      <div className="space-y-2">
        <Label htmlFor="full_name">Full name</Label>
        <Input id="full_name" name="full_name" required minLength={2} maxLength={120} autoComplete="off" />
      </div>
      <div className="space-y-2">
        <Label htmlFor="member_code">Member code</Label>
        <Input id="member_code" name="member_code" required placeholder="e.g. GYM-7K2P" className="uppercase" autoComplete="off" />
        <p className="text-xs text-muted-foreground">Printed on the card and encoded in the QR code. Avoid 0/O and 1/I; letters are upper-cased automatically.</p>
      </div>
      <div className="space-y-2">
        <Label htmlFor="phone">Mobile phone</Label>
        <Input id="phone" name="phone" type="tel" placeholder="010 1234 5678" autoComplete="off" />
      </div>
      <div className="space-y-2">
        <Label htmlFor="email">Email</Label>
        <Input id="email" name="email" type="email" placeholder="optional" autoComplete="off" />
      </div>
      <ActionFeedback state={state} />
      <Button type="submit" disabled={pending} className="w-full">
        {pending ? 'Creating…' : 'Create member'}
      </Button>
    </form>
  );
}
