'use client';

import { useState, useEffect } from 'react';
import Link from 'next/link';
import { QRCodeSVG } from 'qrcode.react';
import {
  ShieldCheck,
  Download,
  Smartphone,
  CheckCircle2,
  AlertTriangle,
  XCircle,
  Camera,
  ExternalLink,
  ChevronRight,
  Info,
} from 'lucide-react';
import { Card, CardContent, CardDescription, CardHeader, CardTitle, Button, Badge } from '@smartgym/ui';

export function CertClient() {
  const [origin, setOrigin] = useState<string>('');
  const [hostname, setHostname] = useState<string>('');
  const [protocol, setProtocol] = useState<string>('');
  const [isSecure, setIsSecure] = useState<boolean>(false);
  const [hasMediaDevices, setHasMediaDevices] = useState<boolean>(false);
  const [cameraProbeStatus, setCameraProbeStatus] = useState<'idle' | 'testing' | 'success' | 'failed'>('idle');
  const [cameraProbeMsg, setCameraProbeMsg] = useState<string>('');

  useEffect(() => {
    if (typeof window !== 'undefined') {
      setOrigin(window.location.origin);
      setHostname(window.location.hostname);
      setProtocol(window.location.protocol);
      setIsSecure(window.isSecureContext);
      setHasMediaDevices(Boolean(navigator.mediaDevices?.getUserMedia));
    }
  }, []);

  const testHandsetCamera = async () => {
    setCameraProbeStatus('testing');
    setCameraProbeMsg('Requesting camera permission…');
    try {
      if (!navigator.mediaDevices?.getUserMedia) {
        throw new Error('navigator.mediaDevices.getUserMedia is undefined in this browser context.');
      }
      const stream = await navigator.mediaDevices.getUserMedia({
        video: { facingMode: 'user' },
        audio: false,
      });
      const tracks = stream.getVideoTracks();
      const label = tracks[0]?.label || 'Active Camera Track';
      const settings = tracks[0]?.getSettings();
      const res = `${settings?.width ?? 0}x${settings?.height ?? 0}`;
      for (const t of tracks) t.stop();
      setCameraProbeStatus('success');
      setCameraProbeMsg(`Camera probe PASSED: "${label}" (${res}). Ready for AI workout!`);
    } catch (err: any) {
      setCameraProbeStatus('failed');
      setCameraProbeMsg(`Camera probe FAILED: ${err.name || 'Error'} - ${err.message || 'Permission blocked'}`);
    }
  };

  const httpsUrl = hostname ? `https://${hostname}:3001/workout` : 'https://localhost:3001/workout';
  const caDownloadUrl = '/kinetiq-ca.crt';

  return (
    <div className="space-y-6 max-w-3xl mx-auto pb-12">
      <div className="space-y-2">
        <h1 className="text-2xl font-bold tracking-tight flex items-center gap-2">
          <ShieldCheck className="h-6 w-6 text-primary" /> Mobile HTTPS &amp; Camera Setup Guide
        </h1>
        <p className="text-sm text-muted-foreground">
          Mobile browsers (iOS Safari, Android Chrome) strictly require a secure HTTPS origin to allow handset camera access over your gym's Wi-Fi LAN.
        </p>
      </div>

      {/* Environment Diagnostics */}
      <Card className="border-border">
        <CardHeader className="pb-3">
          <CardTitle className="text-sm font-semibold uppercase tracking-wider text-muted-foreground flex items-center gap-2">
            <Info className="h-4 w-4" /> Current Browser Diagnostics
          </CardTitle>
        </CardHeader>
        <CardContent className="space-y-3">
          <div className="grid grid-cols-2 sm:grid-cols-4 gap-2 text-xs">
            <div className="rounded-lg bg-card/60 p-2.5 border border-border">
              <span className="text-muted-foreground block">Protocol</span>
              <span className="font-mono font-bold text-foreground">{protocol || '—'}</span>
            </div>
            <div className="rounded-lg bg-card/60 p-2.5 border border-border">
              <span className="text-muted-foreground block">Host</span>
              <span className="font-mono font-bold text-foreground">{hostname || '—'}</span>
            </div>
            <div className="rounded-lg bg-card/60 p-2.5 border border-border">
              <span className="text-muted-foreground block">Secure Context</span>
              <span className={`font-mono font-bold ${isSecure ? 'text-emerald-400' : 'text-red-400'}`}>
                {isSecure ? 'TRUE (Secure)' : 'FALSE (Insecure)'}
              </span>
            </div>
            <div className="rounded-lg bg-card/60 p-2.5 border border-border">
              <span className="text-muted-foreground block">Camera API</span>
              <span className={`font-mono font-bold ${hasMediaDevices ? 'text-emerald-400' : 'text-red-400'}`}>
                {hasMediaDevices ? 'Supported' : 'Blocked'}
              </span>
            </div>
          </div>

          <div className="pt-2 flex flex-col sm:flex-row items-center justify-between gap-3 bg-muted/20 p-3 rounded-lg border border-border">
            <div className="text-xs">
              <p className="font-semibold text-foreground">Handset Camera Verification Drill</p>
              <p className="text-muted-foreground text-[11px]">
                Verify whether this handset can open the camera and receive video frames without errors.
              </p>
            </div>
            <Button
              type="button"
              size="sm"
              onClick={testHandsetCamera}
              disabled={cameraProbeStatus === 'testing'}
              className="gap-1.5 shrink-0"
            >
              <Camera className="h-4 w-4" />
              {cameraProbeStatus === 'testing' ? 'Testing…' : 'Test Handset Camera'}
            </Button>
          </div>

          {cameraProbeStatus !== 'idle' && (
            <div
              className={`rounded-lg p-3 text-xs flex items-center gap-2 border ${
                cameraProbeStatus === 'success'
                  ? 'bg-emerald-500/10 border-emerald-500/30 text-emerald-300'
                  : cameraProbeStatus === 'failed'
                  ? 'bg-red-500/10 border-red-500/30 text-red-300'
                  : 'bg-primary/10 border-primary/30 text-primary'
              }`}
            >
              {cameraProbeStatus === 'success' ? (
                <CheckCircle2 className="h-4 w-4 text-emerald-400 shrink-0" />
              ) : cameraProbeStatus === 'failed' ? (
                <XCircle className="h-4 w-4 text-red-400 shrink-0" />
              ) : (
                <Camera className="h-4 w-4 text-primary shrink-0 animate-pulse" />
              )}
              <span>{cameraProbeMsg}</span>
            </div>
          )}
        </CardContent>
      </Card>

      {/* Step 1: Scan & Open on Handset */}
      <Card className="border-border">
        <CardHeader className="pb-3">
          <Badge variant="outline" className="w-fit text-[10px] mb-1">Step 1</Badge>
          <CardTitle className="text-base flex items-center gap-2">
            <Smartphone className="h-5 w-5 text-primary" /> Open Portal on Your Phone
          </CardTitle>
          <CardDescription>
            Connect your phone to the same gym Wi-Fi network and scan the QR code to open the secure member app:
          </CardDescription>
        </CardHeader>
        <CardContent className="space-y-4">
          <div className="flex flex-col sm:flex-row items-center gap-6 p-4 rounded-xl bg-card border border-border">
            <div className="p-3 bg-white rounded-xl shadow-md shrink-0">
              <QRCodeSVG value={httpsUrl} size={150} level="M" />
            </div>
            <div className="space-y-2 text-center sm:text-left">
              <p className="text-xs text-muted-foreground uppercase font-semibold">Secure Mobile URL</p>
              <p className="font-mono text-sm font-bold text-primary break-all bg-muted/40 p-2 rounded border border-border">
                {httpsUrl}
              </p>
              <p className="text-xs text-muted-foreground">
                Point your phone camera at the QR code, or type the URL directly in Safari or Chrome on your phone.
              </p>
            </div>
          </div>
        </CardContent>
      </Card>

      {/* Step 2: Install Certificate */}
      <Card className="border-border">
        <CardHeader className="pb-3">
          <Badge variant="outline" className="w-fit text-[10px] mb-1">Step 2</Badge>
          <CardTitle className="text-base flex items-center gap-2">
            <Download className="h-5 w-5 text-primary" /> Trust the KinetiQ Local Certificate
          </CardTitle>
          <CardDescription>
            Install the Root CA certificate so your phone natively trusts HTTPS and permits camera access without warnings:
          </CardDescription>
        </CardHeader>
        <CardContent className="space-y-4 text-xs">
          <div className="flex justify-center sm:justify-start">
            <a href={caDownloadUrl} download="kinetiq-ca.crt">
              <Button className="gap-2">
                <Download className="h-4 w-4" /> Download Certificate (kinetiq-ca.crt)
              </Button>
            </a>
          </div>

          <div className="grid grid-cols-1 md:grid-cols-2 gap-4 pt-2">
            {/* iOS Instructions */}
            <div className="rounded-xl border border-border bg-card/60 p-4 space-y-2.5">
              <p className="font-bold text-sm text-foreground flex items-center gap-1.5">
                🍏 Apple iPhone / iPad (iOS Safari)
              </p>
              <ol className="list-decimal pl-4 space-y-1.5 text-muted-foreground">
                <li>
                  Tap <strong>Download Certificate</strong> above in Safari. When prompted <em>"This website is trying to download a configuration profile"</em>, tap <strong>Allow</strong>.
                </li>
                <li>
                  Open the iOS <strong>Settings</strong> app. Tap <strong>Profile Downloaded</strong> near the top.
                </li>
                <li>
                  Tap <strong>Install</strong> in the top right, enter your device passcode, and tap <strong>Install</strong>.
                </li>
                <li>
                  Navigate to: <strong>Settings &gt; General &gt; About &gt; Certificate Trust Settings</strong> (at the bottom).
                </li>
                <li>
                  Under <em>"Enable full trust for root certificates"</em>, turn <strong>ON</strong> the switch for <strong>"KinetiQ Gym Local Root CA"</strong> and tap <strong>Continue</strong>.
                </li>
                <li>
                  Return to Safari and reload. The green lock appears and camera will work seamlessly!
                </li>
              </ol>
            </div>

            {/* Android Instructions */}
            <div className="rounded-xl border border-border bg-card/60 p-4 space-y-2.5">
              <p className="font-bold text-sm text-foreground flex items-center gap-1.5">
                🤖 Android (Chrome / Samsung Internet)
              </p>
              <div className="space-y-2 text-muted-foreground">
                <p className="font-semibold text-foreground">Quick Method:</p>
                <p>
                  When opening the HTTPS URL in Chrome, tap <strong>Advanced</strong> &rarr; <strong>Proceed to {hostname} (unsafe)</strong>. Android Chrome immediately permits camera access once you proceed!
                </p>
                <p className="font-semibold text-foreground pt-1">Full Root CA Install (Zero Warnings):</p>
                <ol className="list-decimal pl-4 space-y-1">
                  <li>Tap <strong>Download Certificate</strong> above.</li>
                  <li>
                    Open Android <strong>Settings &gt; Security &amp; Privacy &gt; More security settings &gt; Encryption &amp; credentials</strong>.
                  </li>
                  <li>
                    Tap <strong>Install a certificate &gt; CA certificate</strong> (tap <em>Install anyway</em> if prompted).
                  </li>
                  <li>Select the downloaded <code className="text-primary font-mono">kinetiq-ca.crt</code> file.</li>
                </ol>
              </div>
            </div>
          </div>
        </CardContent>
      </Card>

      {/* Step 3: Launch Workout */}
      <Card className="border-border">
        <CardHeader className="pb-3">
          <Badge variant="outline" className="w-fit text-[10px] mb-1">Step 3</Badge>
          <CardTitle className="text-base flex items-center gap-2">
            <CheckCircle2 className="h-5 w-5 text-emerald-400" /> Start Your AI Workout
          </CardTitle>
          <CardDescription>
            Once HTTPS is active and the certificate is trusted, open the AI Personal Trainer Camera:
          </CardDescription>
        </CardHeader>
        <CardContent>
          <Link href="/workout">
            <Button className="w-full gap-2 text-base h-11 bg-primary hover:bg-primary/90">
              <Camera className="h-5 w-5" /> Launch AI Workout Camera <ChevronRight className="h-4 w-4" />
            </Button>
          </Link>
        </CardContent>
      </Card>
    </div>
  );
}
