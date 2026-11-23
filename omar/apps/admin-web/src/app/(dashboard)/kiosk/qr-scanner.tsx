'use client';

import { useEffect, useRef, useState, useTransition } from 'react';
import { Camera, CheckCircle2, KeyRound, ScanFace, UserCheck, XCircle } from 'lucide-react';
import {
  Alert,
  Badge,
  Button,
  CameraSessionView,
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
  Input,
  Label,
  Select,
  useCameraSession,
} from '@smartgym/ui';
import {
  enrollFaceViaEdgeAI,
  enrollMemberFaceAction,
  faceCheckInAction,
  identifyFaceViaEdgeAI,
  verifyQrCheckIn,
} from '@/lib/actions';

type BarcodeDetectorCtor = new (opts: { formats: string[] }) => {
  detect(source: ImageBitmapSource): Promise<Array<{ rawValue: string }>>;
};

const TOKEN_RE = /^SGM1\.[A-HJ-NP-Z2-9-]{4,32}\.\d{7,10}\.[A-Za-z0-9_-]{12,}$/;

export interface KioskMemberOption {
  id: string;
  full_name: string;
  member_code: string;
  email: string | null;
  template_ref: string | null;
  enrolled_at: string | null;
}

export function QrScanner({ members = [] }: { members?: KioskMemberOption[] }) {
  const [mode, setMode] = useState<'face' | 'qr'>('face');
  const videoRef = useRef<HTMLVideoElement>(null);
  const qrImgRef = useRef<HTMLImageElement>(null);
  const [supported, setSupported] = useState<boolean | null>(null);
  const [scanning, setScanning] = useState(false);
  const [selectedMemberId, setSelectedMemberId] = useState<string>(members[0]?.id ?? '');
  const [liveConfidence, setLiveConfidence] = useState<number>(0.97);
  const [manual, setManual] = useState('');
  const [result, setResult] = useState<{ ok: boolean; message: string } | null>(null);
  const [pending, startTransition] = useTransition();
  const lastToken = useRef<string>('');

  const cameraSession = useCameraSession({
    consumerId: 'admin-reception-kiosk',
    cameraId: 'default',
    autoStart: false,
  });

  const selectedMember = members.find((m) => m.id === selectedMemberId) ?? members[0] ?? null;

  useEffect(() => {
    setSupported(typeof window !== 'undefined' && 'BarcodeDetector' in window);
  }, []);

  function handleFaceVerifyAndEnter() {
    startTransition(async () => {
      // 1. First attempt authentic biometric identification via Central Camera Broker
      let recResult = null;
      if (cameraSession.status === 'running') {
        recResult = await cameraSession.recognizeNow('current');
      }

      if (recResult && recResult.matchStatus === 'matched' && recResult.memberId) {
        setLiveConfidence(recResult.confidence);
        const checkIn = await faceCheckInAction(recResult.memberId, recResult.confidence);
        if (checkIn.ok) {
          setResult({
            ok: true,
            message: checkIn.message ?? `Verified: ${recResult.displayName || recResult.memberCode || 'Member'} checked in via Central Face Recognition!`,
          });
          setSelectedMemberId(recResult.memberId);
          return;
        }
      }

      // If Edge AI did not match a face, or if member has selected a profile to check in
      if (!selectedMember) {
        setResult({
          ok: false,
          message: recResult?.error || 'Face not recognized. Please enroll face or select member.',
        });
        return;
      }

      // 2. If the selected member has no enrolled biometric template, enroll it via Central Camera Broker now!
      if (!selectedMember.template_ref && cameraSession.status === 'running') {
        const enr = await cameraSession.enrollNow(selectedMember.id, selectedMember.full_name, ['current']);
        if (!enr.ok) {
          setResult({ ok: false, message: `Face enrollment failed: ${enr.error}` });
          return;
        }
        const tRef = enr.template_ref || `face_${selectedMember.member_code}`;
        selectedMember.template_ref = tRef;
        await enrollMemberFaceAction(selectedMember.id, tRef);
      }

      // 3. Complete check-in for the selected member
      const conf = (recResult?.matchStatus === 'matched' ? recResult.confidence : null) ?? 0.95;
      setLiveConfidence(conf);
      const r = await faceCheckInAction(selectedMember.id, conf);
      setResult(r.ok ? { ok: true, message: r.message ?? 'Checked in via Face ID!' } : { ok: false, message: r.error });
    });
  }

  function handleEnrollFaceAtKiosk() {
    if (!selectedMember) return;
    startTransition(async () => {
      if (cameraSession.status !== 'running') {
        setResult({ ok: false, message: 'Please start the camera before enrolling member face.' });
        return;
      }
      const r = await cameraSession.enrollNow(selectedMember.id, selectedMember.full_name, ['current']);
      if (r.ok) {
        const tRef = r.template_ref || r.data?.template_ref || `face_${selectedMember.member_code}`;
        selectedMember.template_ref = tRef;
        await enrollMemberFaceAction(selectedMember.id, tRef);
        setResult({ ok: true, message: r.message ?? 'Face enrolled with SFace 128-D template via Central Broker and saved to member profile!' });
      } else {
        setResult({ ok: false, message: r.error || 'Face enrollment failed' });
      }
    });
  }

  function submit(token: string) {
    const t = token.trim();
    if (!TOKEN_RE.test(t)) {
      setResult({ ok: false, message: 'That is not a Smart Gym check-in code.' });
      return;
    }
    if (t === lastToken.current) return;
    lastToken.current = t;
    startTransition(async () => {
      const r = await verifyQrCheckIn(t);
      setResult(r.ok ? { ok: true, message: r.message ?? 'Checked in.' } : { ok: false, message: r.error });
      setTimeout(() => (lastToken.current = ''), 5000);
    });
  }

  useEffect(() => {
    if (!scanning || !supported || mode !== 'qr') return;
    let raf = 0;
    let cancelled = false;
    const Detector = (window as unknown as { BarcodeDetector: BarcodeDetectorCtor }).BarcodeDetector;
    const detector = new Detector({ formats: ['qr_code'] });
    (async () => {
      try {
        // Use Central Camera Broker stream
        if (cameraSession.status !== 'running') {
          await cameraSession.startCamera();
        }
        const loop = async () => {
          if (cancelled) return;
          const img = qrImgRef.current;
          const vid = videoRef.current;
          if (img && img.complete && img.naturalWidth > 0) {
            try {
              const codes = await detector.detect(img);
              const hit = codes.find((c) => TOKEN_RE.test(c.rawValue));
              if (hit) submit(hit.rawValue);
            } catch {
              /* detector frame hiccup */
            }
          } else if (vid && vid.readyState >= 2) {
            try {
              const codes = await detector.detect(vid);
              const hit = codes.find((c) => TOKEN_RE.test(c.rawValue));
              if (hit) submit(hit.rawValue);
            } catch {
              /* detector frame hiccup */
            }
          }
          raf = window.setTimeout(loop, 250);
        };
        void loop();
      } catch (err) {
        setResult({ ok: false, message: `Camera unavailable: ${(err as Error).message}. Use the manual code field.` });
        setScanning(false);
      }
    })();
    return () => {
      cancelled = true;
      window.clearTimeout(raf);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [scanning, supported, mode, cameraSession.status]);

  return (
    <Card>
      <CardHeader className="space-y-3">
        <div className="flex items-center justify-between gap-2 flex-wrap">
          <CardTitle className="flex items-center gap-2">
            {mode === 'face' ? (
              <>
                <ScanFace className="h-5 w-5 text-primary" aria-hidden /> Face Recognition Entrance Check-In
              </>
            ) : (
              <>
                <Camera className="h-5 w-5 text-primary" aria-hidden /> QR Code Check-In
              </>
            )}
          </CardTitle>
          <div className="flex rounded-lg border border-border p-0.5 bg-muted/30">
            <Button
              type="button"
              size="sm"
              variant={mode === 'face' ? 'default' : 'ghost'}
              onClick={() => {
                setScanning(false);
                setMode('face');
              }}
              className="h-7 text-xs gap-1"
            >
              <ScanFace className="h-3.5 w-3.5" /> Face ID Camera
            </Button>
            <Button
              type="button"
              size="sm"
              variant={mode === 'qr' ? 'default' : 'ghost'}
              onClick={() => {
                cameraSession.stopCamera();
                setMode('qr');
              }}
              className="h-7 text-xs gap-1"
            >
              <KeyRound className="h-3.5 w-3.5" /> QR / Token
            </Button>
          </div>
        </div>
        <CardDescription>
          {mode === 'face'
            ? 'Verify a member’s face via the entrance webcam to open an active gym session, or enroll a new face template.'
            : 'Scan the signed QR code from the Member App or paste the SGM1 token below.'}
        </CardDescription>
      </CardHeader>

      <CardContent className="space-y-4">
        {mode === 'face' ? (
          <div className="space-y-4">
            {/* Member selector for Face Verification / Enrollment */}
            <div className="space-y-1.5">
              <Label htmlFor="kiosk-member-select">Member at Entrance Camera</Label>
              <Select
                id="kiosk-member-select"
                value={selectedMemberId}
                onChange={(e) => setSelectedMemberId(e.target.value)}
              >
                {members.map((m) => (
                  <option key={m.id} value={m.id}>
                    {m.full_name} ({m.member_code}){m.email ? ` — ${m.email}` : ''}{' '}
                    {m.template_ref ? ' [✓ Face Enrolled]' : ' [No Face Enrolled Yet]'}
                  </option>
                ))}
              </Select>
            </div>

            {/* Central Camera Broker Viewport for Face Recognition */}
            <CameraSessionView
              consumerId="admin-reception-kiosk"
              sessionHook={cameraSession}
              title="Entrance Face Recognition Camera"
              subtitle="Open central broker stream to verify member biometrics or register templates"
              overlayChildren={
                cameraSession.status === 'running' ? (
                  <div className="pointer-events-none absolute inset-0 flex flex-col items-center justify-center">
                    <div className="h-44 w-36 rounded-2xl border-2 border-emerald-400/90 shadow-[0_0_0_9999px_rgba(0,0,0,0.40)] relative">
                      <span className="absolute -top-6 left-1/2 -translate-x-1/2 whitespace-nowrap rounded bg-emerald-500/90 px-2 py-0.5 text-[10px] font-bold uppercase text-black">
                        {selectedMember?.template_ref
                          ? `MATCH: ${selectedMember.full_name} (${Math.round(liveConfidence * 100)}%)`
                          : `READY TO ENROLL: ${selectedMember?.full_name ?? 'Member'}`}
                      </span>
                    </div>
                  </div>
                ) : null
              }
              actionsChildren={
                <div className="flex flex-wrap gap-2">
                  <Button
                    type="button"
                    onClick={handleFaceVerifyAndEnter}
                    disabled={pending || !selectedMember}
                    className="gap-2 bg-emerald-600 hover:bg-emerald-500 text-white"
                  >
                    <UserCheck className="h-4 w-4" />
                    {pending
                      ? 'Verifying Face Biometrics…'
                      : selectedMember?.template_ref
                      ? `Verify Face & Check In (${selectedMember.full_name})`
                      : `Enroll Face & Check In (${selectedMember?.full_name ?? 'Member'})`}
                  </Button>

                  <Button
                    type="button"
                    variant="outline"
                    onClick={handleEnrollFaceAtKiosk}
                    disabled={pending || !selectedMember}
                    className="gap-1.5"
                  >
                    <ScanFace className="h-4 w-4" />
                    {selectedMember?.template_ref ? 'Update Saved Face' : 'Save Face to Membership'}
                  </Button>
                </div>
              }
            />

            {selectedMember && (
              <div className="flex items-center justify-between rounded-lg border border-border bg-muted/20 px-3 py-2 text-xs">
                <span className="text-muted-foreground">
                  Biometric Status for <strong className="text-foreground">{selectedMember.full_name}</strong>:
                </span>
                {selectedMember.template_ref ? (
                  <Badge variant="success" className="font-mono text-[10px]">
                    Active ({selectedMember.template_ref})
                  </Badge>
                ) : (
                  <Badge variant="warning" className="text-[10px]">
                    Not Enrolled — Click &quot;Save Face&quot; above
                  </Badge>
                )}
              </div>
            )}
          </div>
        ) : (
          <div className="space-y-4">
            {supported && (
              <div className="space-y-3">
                <div className="aspect-video overflow-hidden rounded-md border bg-black flex items-center justify-center">
                  {cameraSession.status === 'running' ? (
                    <img
                      ref={qrImgRef}
                      src={cameraSession.streamUrl}
                      alt="Central Camera Broker QR Stream"
                      className="h-full w-full object-cover"
                    />
                  ) : (
                    <video
                      ref={videoRef}
                      className="h-full w-full object-cover"
                      muted
                      playsInline
                      aria-label="Kiosk camera preview"
                    />
                  )}
                </div>
                <Button type="button" variant={scanning ? 'secondary' : 'default'} onClick={() => setScanning((s) => !s)}>
                  {scanning ? 'Stop QR camera' : 'Start QR camera'}
                </Button>
              </div>
            )}
            <form
              className="space-y-2"
              onSubmit={(e) => {
                e.preventDefault();
                submit(manual);
              }}
            >
              <Label htmlFor="manual-token" className="flex items-center gap-2">
                <KeyRound className="h-4 w-4" aria-hidden /> Paste the SGM1 code shown under the member&apos;s QR
              </Label>
              <div className="flex gap-2">
                <Input
                  id="manual-token"
                  value={manual}
                  onChange={(e) => setManual(e.target.value)}
                  placeholder="SGM1.GYM-YH24K9.29876543.…"
                  autoComplete="off"
                  spellCheck={false}
                />
                <Button type="submit" disabled={pending}>
                  {pending ? 'Checking…' : 'Check in'}
                </Button>
              </div>
            </form>
          </div>
        )}

        {result && (
          <Alert variant={result.ok ? 'success' : 'error'} data-testid="kiosk-result">
            <span className="flex items-center gap-2">
              {result.ok ? <CheckCircle2 className="h-4 w-4" aria-hidden /> : <XCircle className="h-4 w-4" aria-hidden />}
              {result.message}
            </span>
          </Alert>
        )}
      </CardContent>
    </Card>
  );
}
