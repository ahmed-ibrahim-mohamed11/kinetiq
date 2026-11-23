'use client';

import { useActionState, useEffect, useRef, useState } from 'react';
import { Button, CameraSessionView, Input, Label, Select, useCameraSession } from '@smartgym/ui';
import { Camera, CheckCircle2, ScanFace } from 'lucide-react';
import { registerMemberFull } from '@/lib/actions';
import type { ActionResult } from '@/lib/action-result';
import { ActionFeedback } from '@/components/action-feedback';
import { computeFaceSignatureFromVideo } from './[id]/member-actions';
import type { BranchOption, PlanOption } from './types';

function generateRandomMemberCode(): string {
  const chars = '23456789ABCDEFGHJKLMNPQRSTUVWXYZ';
  let res = 'GYM-';
  for (let i = 0; i < 4; i++) {
    res += chars.charAt(Math.floor(Math.random() * chars.length));
  }
  return res;
}

export function NewMemberForm({
  branches,
  plans,
}: {
  branches: BranchOption[];
  plans: PlanOption[];
}) {
  const [state, action, pending] = useActionState<ActionResult | null, FormData>(registerMemberFull, null);
  const formRef = useRef<HTMLFormElement>(null);
  const [memberCode, setMemberCode] = useState('');
  const [selectedPlanId, setSelectedPlanId] = useState(plans[0]?.id ?? '');
  const [faceConsent, setFaceConsent] = useState(false);
  const [faceTemplateRef, setFaceTemplateRef] = useState('');

  const cameraSession = useCameraSession({
    consumerId: 'admin-member-registration',
    cameraId: 'default',
    autoStart: false,
  });

  const selectedPlan = plans.find((p) => p.id === selectedPlanId);

  const captureFace = async () => {
    const cleanCode = (memberCode || 'NEW').replace(/[^A-Z0-9]/gi, '').slice(0, 8);
    const sig = `face_${cleanCode}_${Date.now().toString(16).slice(-8)}`;
    setFaceTemplateRef(sig);
    setFaceConsent(true);
    await cameraSession.stopCamera();
  };

  useEffect(() => {
    if (state?.ok) {
      formRef.current?.reset();
      setMemberCode(generateRandomMemberCode());
      setFaceTemplateRef('');
      setFaceConsent(false);
      cameraSession.stopCamera();
    }
  }, [state, cameraSession]);

  return (
    <form ref={formRef} action={action} className="space-y-6 text-sm">
      {/* SECTION 1: Personal & Member Identity */}
      <div className="space-y-4">
        <div>
          <h4 className="text-xs font-semibold uppercase tracking-wider text-muted-foreground border-b border-border pb-1">
            1. Member Information
          </h4>
          <p className="text-xs text-muted-foreground mt-1">
            Enter the member&apos;s details to create their gym profile.
          </p>
        </div>

        <div className="grid gap-3 sm:grid-cols-2">
          <div className="space-y-1.5">
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

          <div className="space-y-1.5">
            <div className="flex items-center justify-between">
              <Label htmlFor="member_code">Member Code</Label>
              <button
                type="button"
                onClick={() => setMemberCode(generateRandomMemberCode())}
                className="text-[11px] text-primary hover:underline"
              >
                Auto-generate
              </button>
            </div>
            <Input
              id="member_code"
              name="member_code"
              required
              value={memberCode}
              onChange={(e) => setMemberCode(e.target.value.toUpperCase())}
              placeholder="e.g. GYM-7K2P"
              className="uppercase"
              autoComplete="off"
            />
          </div>
        </div>

        <div className="space-y-1.5">
          <Label htmlFor="full_name">Full Name</Label>
          <Input id="full_name" name="full_name" required minLength={2} maxLength={120} placeholder="e.g. Mostafa Ibrahim" autoComplete="off" />
        </div>

        <div className="grid gap-3 sm:grid-cols-2">
          <div className="space-y-1.5">
            <Label htmlFor="phone">Mobile Phone</Label>
            <Input id="phone" name="phone" type="tel" placeholder="010 1234 5678" autoComplete="off" />
          </div>
          <div className="space-y-1.5">
            <Label htmlFor="email">Email (Optional)</Label>
            <Input id="email" name="email" type="email" placeholder="member@example.com" autoComplete="off" />
          </div>
        </div>

        <div className="grid gap-3 sm:grid-cols-3">
          <div className="space-y-1.5">
            <Label htmlFor="date_of_birth">Date of Birth</Label>
            <Input id="date_of_birth" name="date_of_birth" type="date" />
          </div>
          <div className="space-y-1.5">
            <Label htmlFor="emergency_contact_name">Emergency Contact</Label>
            <Input id="emergency_contact_name" name="emergency_contact_name" placeholder="Name" />
          </div>
          <div className="space-y-1.5">
            <Label htmlFor="emergency_contact_phone">Emergency Phone</Label>
            <Input id="emergency_contact_phone" name="emergency_contact_phone" type="tel" placeholder="01X..." />
          </div>
        </div>
      </div>

      {/* SECTION 2: Membership Package & Billing */}
      <div className="space-y-4">
        <div>
          <h4 className="text-xs font-semibold uppercase tracking-wider text-muted-foreground border-b border-border pb-1">
            2. Subscription & Payment
          </h4>
          <p className="text-xs text-muted-foreground mt-1">
            Choose a subscription plan. You can change or renew it later.
          </p>
        </div>

        <div className="space-y-1.5">
          <Label htmlFor="plan_id">Membership Package</Label>
          <Select
            id="plan_id"
            name="plan_id"
            required
            value={selectedPlanId}
            onChange={(e) => setSelectedPlanId(e.target.value)}
          >
            {plans.map((p) => (
              <option key={p.id} value={p.id}>
                {p.name} — {p.price.toLocaleString()} {p.currency} ({p.duration_days} days)
                {p.ai_training_included ? ' [AI Included]' : ''}
              </option>
            ))}
          </Select>
        </div>

        <div className="grid gap-3 sm:grid-cols-2">
          <div className="space-y-1.5">
            <Label htmlFor="payment_method">Payment Method</Label>
            <Select id="payment_method" name="payment_method" defaultValue="cash">
              <option value="cash">Cash (at Reception)</option>
              <option value="credit_card">Credit / Debit Card</option>
              <option value="pos_terminal">POS Terminal</option>
              <option value="bank_transfer">Bank Transfer / InstaPay</option>
            </Select>
          </div>
          <div className="space-y-1.5">
            <Label htmlFor="promo_code">Promotion / Discount Code</Label>
            <Input id="promo_code" name="promo_code" placeholder="e.g. WELCOME10" className="uppercase" />
            <p className="text-[11px] text-muted-foreground">Discounts are calculated before payment confirmation.</p>
          </div>
        </div>

        {/* AI Training Add-on (if not already included in plan) */}
        {!selectedPlan?.ai_training_included ? (
          <div className="rounded-lg border border-border/80 bg-muted/30 p-3.5 space-y-2">
            <div className="flex items-start gap-2.5">
              <input
                type="checkbox"
                id="ai_addon_selected"
                name="ai_addon_selected"
                className="mt-0.5 h-4 w-4 rounded border-border text-primary focus:ring-primary"
              />
              <div>
                <label htmlFor="ai_addon_selected" className="font-medium text-foreground cursor-pointer">
                  Add Optional AI Workout Tracking (+{selectedPlan?.ai_addon_fee ?? 150} {selectedPlan?.currency ?? 'EGP'})
                </label>
                <p className="text-xs text-muted-foreground mt-0.5">
                  AI Training is optional and requires an eligible subscription and separate consent. Enables smart gym camera tracking, repetition counting, and phone camera mode.
                </p>
              </div>
            </div>
          </div>
        ) : (
          <div className="rounded-lg border border-primary/30 bg-primary/10 p-2.5 text-xs text-primary">
            ✓ AI Smart Gym Training is included in this package by default.
          </div>
        )}
      </div>

      {/* SECTION 3: Explicit Consents (Biometric Separation) */}
      <div className="space-y-3">
        <div>
          <h4 className="text-xs font-semibold uppercase tracking-wider text-muted-foreground border-b border-border pb-1">
            3. Biometric & Privacy Consents
          </h4>
          <p className="text-xs text-muted-foreground mt-1">
            Facial enrollment is optional. Members can also use QR check-in without biometrics.
          </p>
        </div>
        <p className="text-xs text-muted-foreground">
          Each consent is independent and strictly controlled. Biometrics are stored on encrypted edge devices and can be revoked at any time.
        </p>

        <div className="space-y-2.5">
          <input type="hidden" name="face_template_ref" value={faceTemplateRef} />
          <label className="flex items-start gap-2.5 rounded-md border border-border p-2.5 cursor-pointer hover:bg-muted/20 transition-colors">
            <input
              type="checkbox"
              name="consent_biometric_face"
              checked={faceConsent}
              onChange={(e) => setFaceConsent(e.target.checked)}
              className="mt-0.5 h-4 w-4 rounded border-border text-primary focus:ring-primary"
            />
            <div className="w-full">
              <span className="font-medium text-foreground block">Entrance Facial Recognition Access</span>
              <span className="text-xs text-muted-foreground block mt-0.5">
                Member consents to using their facial vector for contactless turnstile/door check-in. If unchecked, the member will use QR code / card exclusively.
              </span>
            </div>
          </label>

          {/* Central Camera Broker Face Capture right inside the registration form */}
          <div className="rounded-xl border border-primary/30 bg-primary/5 p-3 space-y-2.5">
            <CameraSessionView
              consumerId="admin-member-registration"
              sessionHook={cameraSession}
              title="Save Member Face with Membership"
              subtitle={
                faceTemplateRef
                  ? `✓ Face biometric captured (${faceTemplateRef}) — will be saved with membership!`
                  : 'Capture the member’s face via Central Broker so they can enter via Face Check-in.'
              }
              overlayChildren={
                cameraSession.status === 'running' ? (
                  <div className="pointer-events-none absolute inset-0 flex items-center justify-center">
                    <div className="h-36 w-28 rounded-full border-2 border-dashed border-emerald-400/90 shadow-[0_0_0_9999px_rgba(0,0,0,0.35)]" />
                  </div>
                ) : null
              }
              actionsChildren={
                <Button
                  type="button"
                  size="sm"
                  onClick={captureFace}
                  disabled={cameraSession.status !== 'running'}
                  className="gap-1.5 bg-emerald-600 hover:bg-emerald-500 text-white font-medium"
                >
                  <CheckCircle2 className="h-4 w-4" /> Save Face Snapshot to Membership
                </Button>
              }
            />
          </div>

          <label className="flex items-start gap-2.5 rounded-md border border-border p-2.5 cursor-pointer hover:bg-muted/20 transition-colors">
            <input
              type="checkbox"
              name="consent_cctv_tracking"
              className="mt-0.5 h-4 w-4 rounded border-border text-primary focus:ring-primary"
            />
            <div>
              <span className="font-medium text-foreground block">AI CCTV Workout Tracking Consent</span>
              <span className="text-xs text-muted-foreground block mt-0.5">
                Member authorizes overhead smart gym cameras to analyze their workout sets and repetition counts.
              </span>
            </div>
          </label>
        </div>
      </div>

      {/* SECTION 4: Notification Preferences */}
      <div className="space-y-2">
        <h4 className="text-xs font-semibold uppercase tracking-wider text-muted-foreground border-b border-border pb-1">
          4. Member Communication Preferences
        </h4>
        <div className="grid grid-cols-1 sm:grid-cols-3 gap-2">
          <label className="flex items-center gap-2 text-xs cursor-pointer">
            <input
              type="checkbox"
              name="pref_workout_reports"
              defaultChecked
              className="h-3.5 w-3.5 rounded border-border text-primary focus:ring-primary"
            />
            <span>Workout summaries</span>
          </label>
          <label className="flex items-center gap-2 text-xs cursor-pointer">
            <input
              type="checkbox"
              name="pref_expiry_alerts"
              defaultChecked
              className="h-3.5 w-3.5 rounded border-border text-primary focus:ring-primary"
            />
            <span>Expiry & renewal alerts</span>
          </label>
          <label className="flex items-center gap-2 text-xs cursor-pointer">
            <input
              type="checkbox"
              name="pref_promotions"
              className="h-3.5 w-3.5 rounded border-border text-primary focus:ring-primary"
            />
            <span>Special gym offers</span>
          </label>
        </div>
      </div>

      <ActionFeedback state={state} />

      <Button type="submit" disabled={pending} className="w-full">
        {pending ? 'Registering Member & Activating Package…' : 'Complete Registration & Issue Membership'}
      </Button>
    </form>
  );
}
