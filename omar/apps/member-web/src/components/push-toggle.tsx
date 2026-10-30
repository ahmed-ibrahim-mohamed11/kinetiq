'use client';

import { useCallback, useEffect, useState } from 'react';
import { Alert, Button } from '@smartgym/ui';
import { disablePushSubscription, registerPushSubscription } from '@/lib/actions';

type State = 'unsupported' | 'checking' | 'denied' | 'off' | 'on' | 'busy';

function urlBase64ToUint8Array(base64String: string): Uint8Array {
  const padding = '='.repeat((4 - (base64String.length % 4)) % 4);
  const base64 = (base64String + padding).replace(/-/g, '+').replace(/_/g, '/');
  const raw = window.atob(base64);
  const out = new Uint8Array(raw.length);
  for (let i = 0; i < raw.length; i++) out[i] = raw.charCodeAt(i);
  return out;
}

export function PushToggle({ vapidPublicKey, activeServerSubscriptions }: { vapidPublicKey: string; activeServerSubscriptions: number }) {
  const [state, setState] = useState<State>('checking');
  const [error, setError] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    if (typeof window === 'undefined' || !('serviceWorker' in navigator) || !('PushManager' in window) || !('Notification' in window)) {
      setState('unsupported');
      return;
    }
    if (Notification.permission === 'denied') {
      setState('denied');
      return;
    }
    const reg = await navigator.serviceWorker.getRegistration('/');
    const sub = reg ? await reg.pushManager.getSubscription() : null;
    setState(sub ? 'on' : 'off');
  }, []);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  async function enable() {
    setError(null);
    setState('busy');
    try {
      const permission = await Notification.requestPermission();
      if (permission !== 'granted') {
        setState(permission === 'denied' ? 'denied' : 'off');
        return;
      }
      const reg = await navigator.serviceWorker.register('/sw.js', { scope: '/' });
      await navigator.serviceWorker.ready;
      const existing = await reg.pushManager.getSubscription();
      const sub =
        existing ??
        (await reg.pushManager.subscribe({
          userVisibleOnly: true,
          applicationServerKey: urlBase64ToUint8Array(vapidPublicKey) as BufferSource,
        }));
      const json = sub.toJSON();
      if (!json.endpoint || !json.keys?.p256dh || !json.keys?.auth) throw new Error('browser returned an incomplete subscription');
      const result = await registerPushSubscription({
        endpoint: json.endpoint,
        keys: { p256dh: json.keys.p256dh, auth: json.keys.auth },
        user_agent: navigator.userAgent.slice(0, 300),
      });
      if (!result.ok) throw new Error(result.error ?? 'could not save subscription');
      setState('on');
    } catch (err) {
      setError((err as Error).message);
      await refresh();
    }
  }

  async function disable() {
    setError(null);
    setState('busy');
    try {
      const reg = await navigator.serviceWorker.getRegistration('/');
      const sub = reg ? await reg.pushManager.getSubscription() : null;
      if (sub) {
        await disablePushSubscription({ endpoint: sub.endpoint });
        await sub.unsubscribe();
      }
      setState('off');
    } catch (err) {
      setError((err as Error).message);
      await refresh();
    }
  }

  if (!vapidPublicKey) {
    return (
      <Alert variant="info" title="Push notifications are not configured yet">
        Report notifications will be delivered by email until the gym enables push delivery for this app.
      </Alert>
    );
  }

  return (
    <div className="space-y-3">
      {error && <Alert variant="error">{error}</Alert>}
      {state === 'unsupported' && (
        <Alert variant="warning">This browser does not support push notifications. On iPhone, add Smart Gym to your Home Screen first, then enable notifications from there.</Alert>
      )}
      {state === 'denied' && (
        <Alert variant="warning">Notifications are blocked for this site in your browser settings. Allow them there, then reload this page.</Alert>
      )}
      {(state === 'off' || state === 'on' || state === 'busy' || state === 'checking') && (
        <div className="flex flex-wrap items-center justify-between gap-3 rounded-lg border p-4">
          <div>
            <p className="font-medium">Report ready notifications</p>
            <p className="text-sm text-muted-foreground">
              {state === 'on' ? 'Enabled on this device.' : 'Get a notification on this device when your workout report is generated.'}
              {activeServerSubscriptions > 0 && ` ${activeServerSubscriptions} device${activeServerSubscriptions === 1 ? '' : 's'} registered.`}
            </p>
          </div>
          {state === 'on' ? (
            <Button variant="outline" onClick={() => void disable()} disabled={state !== 'on'}>
              Disable
            </Button>
          ) : (
            <Button onClick={() => void enable()} disabled={state === 'busy' || state === 'checking'}>
              {state === 'busy' ? 'Enabling…' : 'Enable'}
            </Button>
          )}
        </div>
      )}
    </div>
  );
}
