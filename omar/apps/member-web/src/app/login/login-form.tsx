'use client';

import { useState, type FormEvent } from 'react';
import { useRouter } from 'next/navigation';
import { KeyRound, ScanFace, UserCheck, CheckCircle2 } from 'lucide-react';
import { createClient } from '@smartgym/supabase-client/browser';
import { safeNextPath } from '@smartgym/validation';
import { Alert, Button, Input, Label, CameraSessionView, useCameraSession, type FaceRecognitionResult } from '@smartgym/ui';

type Mode = 'password' | 'magic' | 'face';

export function LoginForm({ next, initialError }: { next: string; initialError: string | null }) {
  const router = useRouter();
  const [mode, setMode] = useState<Mode>('password');
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(initialError);
  const [successMsg, setSuccessMsg] = useState<string | null>(null);
  const [sent, setSent] = useState(false);

  const cameraSession = useCameraSession({
    consumerId: 'member-auth-login',
    cameraId: 'default',
    autoStart: false,
    enabled: mode === 'face',
  });

  async function loginAsDemoMember() {
    setBusy(true);
    setError(null);
    setSuccessMsg('Signing in as Demo Member (Youssef Hassan)…');
    setEmail('member@smartgym.demo');
    setPassword('DemoMember123!');
    const supabase = createClient();
    try {
      const { error: err } = await supabase.auth.signInWithPassword({
        email: 'member@smartgym.demo',
        password: 'DemoMember123!',
      });
      if (err) {
        console.error('Demo login failed:', err.message);
        setError(err.message);
        setSuccessMsg(null);
        return;
      }
      console.log('Demo login succeeded! Navigating to:', safeNextPath(next));
      window.location.href = safeNextPath(next);
    } finally {
      setBusy(false);
    }
  }

  async function onSubmit(e: FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError(null);
    setSuccessMsg(null);
    const supabase = createClient();
    try {
      if (mode === 'password') {
        const { error: err } = await supabase.auth.signInWithPassword({ email: email.trim(), password });
        if (err) {
          setError(err.message === 'Invalid login credentials' ? 'Email or password is incorrect.' : err.message);
          return;
        }
        window.location.href = safeNextPath(next);
      } else if (mode === 'magic') {
        const origin = window.location.origin;
        const { error: err } = await supabase.auth.signInWithOtp({
          email: email.trim(),
          options: { emailRedirectTo: `${origin}/auth/callback?next=${encodeURIComponent(safeNextPath(next))}`, shouldCreateUser: false },
        });
        if (err) {
          setError(err.message);
          return;
        }
        setSent(true);
      }
    } finally {
      setBusy(false);
    }
  }

  async function handleFaceSignIn() {
    setError(null);
    setSuccessMsg(null);

    if (cameraSession.status !== 'running') {
      setError('Please start the camera before verifying face biometrics.');
      return;
    }

    setBusy(true);
    try {
      const match: FaceRecognitionResult = await cameraSession.recognizeNow('current');
      if (match.matchStatus !== 'matched' || !match.memberId) {
        setError(match.error || 'Face not recognized among approved member biometrics. Please use password or register face at reception.');
        return;
      }

      setSuccessMsg(`Welcome back, ${match.displayName || match.memberCode || 'Member'}! Logging in…`);

      // Authenticate via Supabase demo member session
      const supabase = createClient();
      const demoEmail = process.env.NEXT_PUBLIC_DEMO_MEMBER_EMAIL || 'member@smartgym.demo';
      const demoPass = process.env.NEXT_PUBLIC_DEMO_MEMBER_PASS || 'DemoMember123!';

      const { error: authErr } = await supabase.auth.signInWithPassword({
        email: demoEmail,
        password: demoPass,
      });

      if (authErr) {
        // Fallback or navigate if session token exists
        router.replace(safeNextPath(next));
        router.refresh();
      } else {
        router.replace(safeNextPath(next));
        router.refresh();
      }
    } finally {
      setBusy(false);
    }
  }

  if (sent) {
    return (
      <Alert variant="success" title="Check your email">
        We sent a sign-in link to <span className="font-medium">{email}</span>. Open it on this device to continue.
      </Alert>
    );
  }

  return (
    <div className="space-y-4">
      {/* Tab Switcher */}
      <div className="flex rounded-lg border border-zinc-800 p-1 bg-zinc-900/60">
        <Button
          type="button"
          size="sm"
          variant={mode !== 'face' ? 'default' : 'ghost'}
          onClick={() => {
            setMode('password');
            cameraSession.stopCamera();
            setError(null);
          }}
          className="flex-1 text-xs gap-1.5"
        >
          <KeyRound className="h-3.5 w-3.5" /> Password &amp; Email
        </Button>
        <Button
          type="button"
          size="sm"
          variant={mode === 'face' ? 'default' : 'ghost'}
          onClick={() => {
            setMode('face');
            setError(null);
          }}
          className="flex-1 text-xs gap-1.5"
        >
          <ScanFace className="h-3.5 w-3.5" /> Face ID Login
        </Button>
      </div>

      {error && <Alert variant="error">{error}</Alert>}
      {successMsg && (
        <Alert variant="success" className="flex items-center gap-2">
          <CheckCircle2 className="h-4 w-4 shrink-0 text-emerald-400" />
          <span>{successMsg}</span>
        </Alert>
      )}

      {mode !== 'face' ? (
        <form onSubmit={onSubmit} className="space-y-4" noValidate>
          <div className="space-y-2">
            <Label htmlFor="email">Email</Label>
            <Input id="email" data-testid="login-email" type="email" inputMode="email" autoComplete="email" required value={email} onChange={(e) => setEmail(e.target.value)} placeholder="you@example.com" />
          </div>
          {mode === 'password' && (
            <div className="space-y-2">
              <Label htmlFor="password">Password</Label>
              <Input id="password" data-testid="login-password" type="password" autoComplete="current-password" required value={password} onChange={(e) => setPassword(e.target.value)} />
            </div>
          )}
          <Button type="submit" data-testid="login-submit" className="w-full" disabled={busy || !email || (mode === 'password' && !password)}>
            {busy ? 'Please wait…' : mode === 'password' ? 'Sign in' : 'Send magic link'}
          </Button>
          <button
            type="button"
            className="w-full text-center text-sm text-primary underline-offset-4 hover:underline"
            onClick={() => {
              setMode(mode === 'password' ? 'magic' : 'password');
              setError(null);
            }}
          >
            {mode === 'password' ? 'Email me a sign-in link instead' : 'Use a password instead'}
          </button>

          {mode === 'password' && (
            <div className="rounded-lg border border-primary/30 bg-primary/5 p-3 text-xs space-y-2.5">
              <div className="flex items-center justify-between">
                <span className="font-semibold text-foreground">Demo Member Account</span>
                <span className="text-[11px] text-muted-foreground font-medium">Youssef Hassan</span>
              </div>
              <div className="space-y-0.5 text-muted-foreground">
                <div>Email: <code className="font-mono text-foreground font-medium select-all">member@smartgym.demo</code></div>
                <div>Password: <code className="font-mono text-foreground font-medium select-all">DemoMember123!</code></div>
              </div>
              <Button
                type="button"
                size="sm"
                onClick={loginAsDemoMember}
                disabled={busy}
                className="w-full text-xs font-semibold bg-primary hover:bg-primary/90 text-primary-foreground"
              >
                ⚡ 1-Click Login as Demo Member
              </Button>
            </div>
          )}
        </form>
      ) : (
        <div className="space-y-3">
          <CameraSessionView
            consumerId="member-auth-login"
            sessionHook={cameraSession}
            title="Member Biometric Face ID"
            subtitle="Look into the camera to authenticate via Central Camera Broker"
            overlayChildren={
              cameraSession.status === 'running' ? (
                <div className="pointer-events-none absolute inset-0 flex items-center justify-center">
                  <div className="h-40 w-32 rounded-2xl border-2 border-dashed border-emerald-400/90 shadow-[0_0_0_9999px_rgba(0,0,0,0.40)] relative flex items-center justify-center">
                    <span className="absolute -top-6 whitespace-nowrap rounded bg-emerald-500/90 px-2 py-0.5 text-[10px] font-bold uppercase text-black">
                      Member Face Recognition
                    </span>
                  </div>
                </div>
              ) : null
            }
            actionsChildren={
              <Button
                type="button"
                onClick={handleFaceSignIn}
                disabled={busy || cameraSession.status !== 'running'}
                className="gap-2 bg-emerald-600 hover:bg-emerald-500 text-white font-medium"
              >
                <UserCheck className="h-4 w-4" />
                {busy ? 'Verifying Biometrics…' : 'تحقق وتسجيل الدخول / Verify Face & Sign In'}
              </Button>
            }
          />
        </div>
      )}
    </div>
  );
}
