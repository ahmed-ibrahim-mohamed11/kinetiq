'use client';

import { useEffect, useRef, useState, useTransition } from 'react';
import { Camera, CheckCircle2, KeyRound, XCircle } from 'lucide-react';
import { Alert, Button, Card, CardContent, CardDescription, CardHeader, CardTitle, Input, Label } from '@smartgym/ui';
import { verifyQrCheckIn } from '@/lib/actions';

type BarcodeDetectorCtor = new (opts: { formats: string[] }) => { detect(source: ImageBitmapSource): Promise<Array<{ rawValue: string }>> };

const TOKEN_RE = /^SGM1\.[A-HJ-NP-Z2-9-]{4,32}\.\d{7,10}\.[A-Za-z0-9_-]{12,}$/;

export function QrScanner() {
  const videoRef = useRef<HTMLVideoElement>(null);
  const [supported, setSupported] = useState<boolean | null>(null);
  const [scanning, setScanning] = useState(false);
  const [manual, setManual] = useState('');
  const [result, setResult] = useState<{ ok: boolean; message: string } | null>(null);
  const [pending, startTransition] = useTransition();
  const lastToken = useRef<string>('');

  useEffect(() => {
    setSupported(typeof window !== 'undefined' && 'BarcodeDetector' in window && !!navigator.mediaDevices?.getUserMedia);
  }, []);

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
    if (!scanning || !supported) return;
    let stream: MediaStream | null = null;
    let raf = 0;
    let cancelled = false;
    const Detector = (window as unknown as { BarcodeDetector: BarcodeDetectorCtor }).BarcodeDetector;
    const detector = new Detector({ formats: ['qr_code'] });
    (async () => {
      try {
        stream = await navigator.mediaDevices.getUserMedia({ video: { facingMode: 'environment' }, audio: false });
        if (cancelled || !videoRef.current) return;
        videoRef.current.srcObject = stream;
        await videoRef.current.play();
        const loop = async () => {
          if (cancelled || !videoRef.current) return;
          try {
            const codes = await detector.detect(videoRef.current);
            const hit = codes.find((c) => TOKEN_RE.test(c.rawValue));
            if (hit) submit(hit.rawValue);
          } catch {
            /* detector hiccup on a frame; keep scanning */
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
      stream?.getTracks().forEach((t) => t.stop());
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [scanning, supported]);

  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex items-center gap-2">
          <Camera className="h-5 w-5" aria-hidden /> QR check-in
        </CardTitle>
        <CardDescription>
          {supported === false
            ? 'This browser has no built-in QR detector. Use the manual code field below (Chrome or Edge support camera scanning).'
            : 'Point the kiosk camera at the QR code in the member app.'}
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-4">
        {supported && (
          <div className="space-y-3">
            <div className="aspect-video overflow-hidden rounded-md border bg-black">
              <video ref={videoRef} className="h-full w-full object-cover" muted playsInline aria-label="Kiosk camera preview" />
            </div>
            <Button type="button" variant={scanning ? 'secondary' : 'default'} onClick={() => setScanning((s) => !s)}>
              {scanning ? 'Stop camera' : 'Start camera'}
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
            <KeyRound className="h-4 w-4" aria-hidden /> Or paste the code shown under the QR
          </Label>
          <div className="flex gap-2">
            <Input id="manual-token" value={manual} onChange={(e) => setManual(e.target.value)} placeholder="SGM1.GYM-TEST-M2A7.29876543.…" autoComplete="off" spellCheck={false} />
            <Button type="submit" disabled={pending}>
              {pending ? 'Checking…' : 'Check in'}
            </Button>
          </div>
        </form>
        {result && (
          <Alert variant={result.ok ? 'success' : 'error'}>
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
