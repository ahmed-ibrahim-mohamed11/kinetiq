'use client';

import { useActionState, useEffect, useRef, useState } from 'react';
import { Button, Input, Label, Select } from '@smartgym/ui';
import { createCamera } from '@/lib/actions';
import type { ActionResult } from '@/lib/action-result';
import { ActionFeedback } from '@/components/action-feedback';

export function CameraForm({ branches, devices }: { branches: { id: string; name: string }[]; devices: { id: string; name: string; branch_id: string }[] }) {
  const [state, action, pending] = useActionState<ActionResult | null, FormData>(createCamera, null);
  const formRef = useRef<HTMLFormElement>(null);
  const [branchId, setBranchId] = useState(branches.length === 1 ? branches[0]!.id : '');
  const [url, setUrl] = useState('');
  useEffect(() => {
    if (state?.ok) {
      formRef.current?.reset();
      setUrl('');
    }
  }, [state]);

  const urlHasCredentials = /^[a-z]+:\/\/[^/@]+@/i.test(url);
  const deviceOptions = devices.filter((d) => !branchId || d.branch_id === branchId);

  return (
    <form ref={formRef} action={action} className="space-y-4">
      <div className="space-y-2">
        <Label htmlFor="cam_branch">Branch</Label>
        <Select id="cam_branch" name="branch_id" required value={branchId} onChange={(e) => setBranchId(e.target.value)}>
          {branches.length !== 1 && <option value="">Select a branch…</option>}
          {branches.map((b) => (
            <option key={b.id} value={b.id}>
              {b.name}
            </option>
          ))}
        </Select>
      </div>
      <div className="space-y-2">
        <Label htmlFor="cam_name">Name</Label>
        <Input id="cam_name" name="name" required maxLength={100} placeholder="CAM-FLOOR-A" />
      </div>
      <div className="grid grid-cols-2 gap-3">
        <div className="space-y-2">
          <Label htmlFor="cam_protocol">Protocol</Label>
          <Select id="cam_protocol" name="protocol" defaultValue="rtsp">
            <option value="rtsp">RTSP</option>
            <option value="onvif">ONVIF</option>
            <option value="nvr_channel">NVR channel</option>
          </Select>
        </div>
        <div className="space-y-2">
          <Label htmlFor="cam_purpose">Purpose</Label>
          <Select id="cam_purpose" name="purpose" defaultValue="floor">
            <option value="floor">Floor</option>
            <option value="entrance">Entrance</option>
            <option value="exit">Exit</option>
            <option value="mixed">Mixed</option>
          </Select>
        </div>
      </div>
      <div className="space-y-2">
        <Label htmlFor="cam_url">Stream URL (no credentials)</Label>
        <Input
          id="cam_url"
          name="stream_url"
          required
          placeholder="rtsp://192.168.10.21:554/Streaming/Channels/101"
          value={url}
          onChange={(e) => setUrl(e.target.value)}
          aria-invalid={urlHasCredentials || undefined}
          className={urlHasCredentials ? 'border-destructive' : undefined}
        />
        {urlHasCredentials ? (
          <p className="text-xs text-destructive">Remove the username and password from the URL. Credentials are stored only in the edge device&apos;s secrets file.</p>
        ) : (
          <p className="text-xs text-muted-foreground">rtsp://, rtsps://, http:// or https://</p>
        )}
      </div>
      <div className="space-y-2">
        <Label htmlFor="cam_device">Edge device</Label>
        <Select id="cam_device" name="edge_device_id" defaultValue="">
          <option value="">Unassigned</option>
          {deviceOptions.map((d) => (
            <option key={d.id} value={d.id}>
              {d.name}
            </option>
          ))}
        </Select>
      </div>
      <div className="grid grid-cols-3 gap-3">
        <div className="space-y-2">
          <Label htmlFor="cam_w">Width</Label>
          <Input id="cam_w" name="resolution_w" type="number" min="1" placeholder="1920" />
        </div>
        <div className="space-y-2">
          <Label htmlFor="cam_h">Height</Label>
          <Input id="cam_h" name="resolution_h" type="number" min="1" placeholder="1080" />
        </div>
        <div className="space-y-2">
          <Label htmlFor="cam_fps">FPS</Label>
          <Input id="cam_fps" name="fps" type="number" min="1" max="120" step="0.5" placeholder="20" />
        </div>
      </div>
      <label className="flex items-center gap-2 text-sm">
        <input type="checkbox" name="enabled" defaultChecked className="h-4 w-4 rounded border-input" />
        Enabled for AI processing
      </label>
      <ActionFeedback state={state} />
      <Button type="submit" disabled={pending || urlHasCredentials} className="w-full">
        {pending ? 'Saving…' : 'Register camera'}
      </Button>
    </form>
  );
}
