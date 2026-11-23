'use client';

import { useState, type FormEvent } from 'react';
import { useRouter } from 'next/navigation';
import { createClient } from '@smartgym/supabase-client/browser';
import { Alert, Button, Input, Label } from '@smartgym/ui';

type Mode = 'password' | 'magic';

export function LoginForm({ next, initialError }: { next: string; initialError: string | null }) {
  const router = useRouter();
  const [mode, setMode] = useState<Mode>('password');
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(initialError);
  const [sent, setSent] = useState(false);

  async function onSubmit(e: FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError(null);
    const supabase = createClient();
    try {
      if (mode === 'password') {
        const { error: err } = await supabase.auth.signInWithPassword({ email: email.trim(), password });
        if (err) {
          setError(err.message === 'Invalid login credentials' ? 'Email or password is incorrect.' : err.message);
          return;
        }
        router.replace(next);
        router.refresh();
      } else {
        const origin = window.location.origin;
        const { error: err } = await supabase.auth.signInWithOtp({
          email: email.trim(),
          options: { emailRedirectTo: `${origin}/auth/callback?next=${encodeURIComponent(next)}`, shouldCreateUser: false },
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

  if (sent) {
    return (
      <Alert variant="success" title="Check your email">
        We sent a sign-in link to <span className="font-medium">{email}</span>. Open it on this device to continue.
      </Alert>
    );
  }

  return (
    <form onSubmit={onSubmit} className="space-y-4" noValidate>
      {error && (
        <Alert variant="error">
          {error}
        </Alert>
      )}
      <div className="space-y-2">
        <Label htmlFor="email">Email</Label>
        <Input id="email" type="email" inputMode="email" autoComplete="email" required value={email} onChange={(e) => setEmail(e.target.value)} placeholder="you@example.com" />
      </div>
      {mode === 'password' && (
        <div className="space-y-2">
          <Label htmlFor="password">Password</Label>
          <Input id="password" type="password" autoComplete="current-password" required value={password} onChange={(e) => setPassword(e.target.value)} />
        </div>
      )}
      <Button type="submit" className="w-full" disabled={busy || !email || (mode === 'password' && !password)}>
        {busy ? 'Please waitâ€¦' : mode === 'password' ? 'Sign in' : 'Send magic link'}
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
    </form>
  );
}
