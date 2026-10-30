'use client';

import { useActionState, useState } from 'react';
import { Alert, Button } from '@smartgym/ui';
import { issueDeviceSecret } from '@/lib/actions';
import type { ActionResult } from '@/lib/action-result';

export function IssueSecret({ deviceId, deviceName }: { deviceId: string; deviceName: string }) {
  const [state, action, pending] = useActionState<ActionResult<string> | null, FormData>(issueDeviceSecret, null);
  const [copied, setCopied] = useState(false);

  async function copy(secret: string) {
    try {
      await navigator.clipboard.writeText(secret);
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    } catch {
      setCopied(false);
    }
  }

  return (
    <div className="flex flex-col items-end gap-2">
      <form
        action={action}
        onSubmit={(e) => {
          if (!window.confirm(`Issue a new secret for ${deviceName}? The current secret keeps working for 24 hours so you can rotate without downtime.`)) e.preventDefault();
        }}
      >
        <input type="hidden" name="device_id" value={deviceId} />
        <Button type="submit" size="sm" variant="outline" disabled={pending}>
          {pending ? 'Issuing…' : 'Issue new secret'}
        </Button>
      </form>
      {state && !state.ok && (
        <div className="max-w-xs text-left">
          <Alert variant="error">{state.error}</Alert>
        </div>
      )}
      {state && state.ok && state.data && (
        <div className="w-80 max-w-full text-left">
          <Alert variant="warning" title="Copy this secret now — it will not be shown again">
            <code className="mt-1 block select-all break-all rounded bg-background p-2 font-mono text-xs">{state.data}</code>
            <p className="mt-2 text-xs">
              Put it in the device&apos;s <span className="font-mono">.env</span> as <span className="font-mono">EDGE_DEVICE_SECRET</span> and restart the edge service.
            </p>
            <Button type="button" size="sm" variant="secondary" className="mt-2" onClick={() => copy(state.data as string)}>
              {copied ? 'Copied' : 'Copy to clipboard'}
            </Button>
          </Alert>
        </div>
      )}
    </div>
  );
}
