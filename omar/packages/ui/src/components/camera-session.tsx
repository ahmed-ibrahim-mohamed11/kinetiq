'use client';

import * as React from 'react';
import { Camera, RefreshCw, Power, ShieldCheck, Activity, Users, AlertCircle } from 'lucide-react';
import { Badge } from './badge';
import { Button } from './button';
import { cn } from '../lib/utils';

export type CameraState =
  | 'idle'
  | 'starting'
  | 'running'
  | 'stopping'
  | 'reconnecting'
  | 'error'
  | 'permission_denied'
  | 'device_not_found';

export interface FaceRecognitionResult {
  eventId: string;
  cameraId: string;
  consumerId: string;
  memberId: string | null;
  memberCode?: string | null;
  displayName?: string | null;
  matchStatus: 'matched' | 'unknown' | 'no_consent' | 'rejected' | 'error';
  confidence: number;
  modelVersion: string;
  occurredAt: string;
  cooldown?: boolean;
  error?: string;
}

export interface CameraSessionOptions {
  consumerId: string;
  cameraId?: string;
  edgeAiUrl?: string;
  autoStart?: boolean;
  enabled?: boolean;
  onRecognition?: (result: FaceRecognitionResult) => void;
}

export interface CameraPipelineHealth {
  status?: 'ok' | 'degraded' | 'paused' | 'offline' | string;
  active_provider?: string | null;
  frames_processed?: number;
  detections?: number;
  last_processed_frame_at?: number | string | null;
  model_status?: string;
  stages?: {
    detector?: string | null;
    pose?: string | null;
    reid?: string | null;
    face?: string | null;
  };
  cameras?: Record<string, { status?: string; fps?: number; inference_fps?: number }>;
  sync_last_error?: string | null;
  [key: string]: unknown;
}

export function resolveEdgeAiUrl(propUrl?: string): string {
  if (propUrl) {
    if (typeof window !== 'undefined' && window.location.hostname && window.location.hostname !== 'localhost' && window.location.hostname !== '127.0.0.1') {
      try {
        const u = new URL(propUrl, window.location.origin);
        if (u.hostname === 'localhost' || u.hostname === '127.0.0.1') {
          u.hostname = window.location.hostname;
          return u.toString().replace(/\/$/, '');
        }
      } catch {
        // fallback to propUrl
      }
    }
    return propUrl;
  }
  if (process.env.NEXT_PUBLIC_EDGE_AI_URL) {
    const envUrl = process.env.NEXT_PUBLIC_EDGE_AI_URL;
    if (typeof window !== 'undefined' && window.location.hostname && window.location.hostname !== 'localhost' && window.location.hostname !== '127.0.0.1') {
      try {
        const u = new URL(envUrl, window.location.origin);
        if (u.hostname === 'localhost' || u.hostname === '127.0.0.1') {
          u.hostname = window.location.hostname;
          return u.toString().replace(/\/$/, '');
        }
      } catch {
        // fallback
      }
    }
    return envUrl;
  }
  if (typeof window !== 'undefined' && window.location.hostname && window.location.hostname !== 'localhost' && window.location.hostname !== '127.0.0.1') {
    return `http://${window.location.hostname}:8088`;
  }
  return 'http://localhost:8088';
}

export function useCameraSession({
  consumerId,
  cameraId = 'default',
  edgeAiUrl: propEdgeAiUrl,
  autoStart = false,
  enabled = true,
  onRecognition,
}: CameraSessionOptions) {
  const edgeAiUrl = React.useMemo(() => resolveEdgeAiUrl(propEdgeAiUrl), [propEdgeAiUrl]);
  const [status, setStatus] = React.useState<CameraState>('idle');
  const [activeConsumers, setActiveConsumers] = React.useState<number>(0);
  const [lastMatch, setLastMatch] = React.useState<FaceRecognitionResult | null>(null);
  const [error, setError] = React.useState<string | null>(null);
  const [health, setHealth] = React.useState<CameraPipelineHealth | null>(null);
  const [streamEpoch, setStreamEpoch] = React.useState<number>(() => Date.now());

  const heartbeatTimerRef = React.useRef<NodeJS.Timeout | null>(null);
  const statusPollTimerRef = React.useRef<NodeJS.Timeout | null>(null);
  const statusRef = React.useRef<CameraState>('idle');
  statusRef.current = status;

  const streamUrl = `${edgeAiUrl}/api/camera/stream?camera_id=${encodeURIComponent(
    cameraId
  )}&consumer_id=${encodeURIComponent(consumerId)}&_t=${streamEpoch}`;

  const fetchStatus = React.useCallback(async () => {
    try {
      const res = await fetch(`${edgeAiUrl}/api/camera/status?camera_id=${encodeURIComponent(cameraId)}`);
      if (res.ok) {
        const data = await res.json();
        setActiveConsumers(data.active_consumers || 0);
        if (data.state && statusRef.current === 'running' && data.state !== 'running') {
          setStatus(data.state);
        }
      }
    } catch {
      // Edge AI service offline or unreachable
    }

    // The camera broker status only proves that a logical subscription exists.
    // Poll the pipeline health separately so the UI can distinguish a live
    // stream from a loaded/processing detector instead of showing a false
    // "Detection: Active" badge as soon as /start returns.
    try {
      const res = await fetch(`${edgeAiUrl}/healthz`, { cache: 'no-store' });
      const data = await res.json().catch(() => null);
      if (data && typeof data === 'object') setHealth(data as CameraPipelineHealth);
    } catch {
      setHealth(null);
    }
  }, [edgeAiUrl, cameraId]);

  const sendHeartbeat = React.useCallback(async () => {
    try {
      await fetch(`${edgeAiUrl}/api/camera/heartbeat`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ camera_id: cameraId, consumer_id: consumerId }),
      });
    } catch {
      // Ignored during network glitch
    }
  }, [edgeAiUrl, cameraId, consumerId]);

  const stopCamera = React.useCallback(async () => {
    if (heartbeatTimerRef.current) {
      clearInterval(heartbeatTimerRef.current);
      heartbeatTimerRef.current = null;
    }
    if (statusPollTimerRef.current) {
      clearInterval(statusPollTimerRef.current);
      statusPollTimerRef.current = null;
    }

    setStatus('stopping');
    try {
      const res = await fetch(`${edgeAiUrl}/api/camera/stop`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ camera_id: cameraId, consumer_id: consumerId }),
      });
      if (res.ok) {
        const data = await res.json();
        setActiveConsumers(data.active_consumers ?? 0);
      }
    } catch (err: any) {
      console.warn('Camera stop notification failed:', err);
    } finally {
      setStatus('idle');
    }
  }, [edgeAiUrl, cameraId, consumerId]);

  const startCamera = React.useCallback(async () => {
    setError(null);
    setStatus('starting');
    try {
      const res = await fetch(`${edgeAiUrl}/api/camera/start`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ camera_id: cameraId, consumer_id: consumerId }),
      });
      if (!res.ok) {
        const errJson = await res.json().catch(() => ({}));
        throw new Error(errJson.error || `HTTP ${res.status}`);
      }
      const data = await res.json();
      if (data?.ok === false) {
        throw new Error(data.error || `Camera did not become ready (state: ${data.state || 'unknown'})`);
      }
      setActiveConsumers(data.active_consumers || 1);
      setStatus('running');
      setStreamEpoch(Date.now());
      void fetchStatus();

      // Start regular heartbeat (every 10s)
      if (heartbeatTimerRef.current) clearInterval(heartbeatTimerRef.current);
      heartbeatTimerRef.current = setInterval(sendHeartbeat, 10000);

      // Start periodic status poll (every 5s)
      if (statusPollTimerRef.current) clearInterval(statusPollTimerRef.current);
      statusPollTimerRef.current = setInterval(fetchStatus, 5000);
    } catch (err: any) {
      let msg = err.message || 'Failed to start camera session';
      if (typeof window !== 'undefined' && !window.isSecureContext && window.location.hostname !== 'localhost' && window.location.hostname !== '127.0.0.1') {
        msg = `Insecure context: Phone access requires HTTPS. Connect via https://${window.location.hostname}:3001`;
      } else if (msg.includes('Failed to fetch') || msg.includes('NetworkError') || msg.includes('network error')) {
        msg = `Connection to Edge AI failed (${edgeAiUrl}). Check Wi-Fi LAN connection and Windows Firewall.`;
      }
      setError(msg);
      setStatus(msg.includes('denied') ? 'permission_denied' : 'error');
    }
  }, [edgeAiUrl, cameraId, consumerId, sendHeartbeat, fetchStatus]);

  const reconnect = React.useCallback(async () => {
    setStatus('reconnecting');
    await stopCamera();
    await new Promise((resolve) => setTimeout(resolve, 400));
    await startCamera();
  }, [stopCamera, startCamera]);

  const recognizeNow = React.useCallback(
    async (imageB64?: string, minConfidence?: number): Promise<FaceRecognitionResult> => {
      try {
        const res = await fetch(`${edgeAiUrl}/api/camera/recognize`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            camera_id: cameraId,
            consumer_id: consumerId,
            image_b64: imageB64 || 'current',
            min_confidence: minConfidence,
          }),
        });
        const data: FaceRecognitionResult = await res.json();
        setLastMatch(data);
        if (onRecognition) {
          onRecognition(data);
        }
        return data;
      } catch (err: any) {
        const fallback: FaceRecognitionResult = {
          eventId: `err_${Date.now()}`,
          cameraId,
          consumerId,
          memberId: null,
          matchStatus: 'error',
          confidence: 0,
          modelVersion: 'yunet_sface_v1',
          occurredAt: new Date().toISOString(),
          error: err.message || 'Network error recognizing face',
        };
        setLastMatch(fallback);
        return fallback;
      }
    },
    [edgeAiUrl, cameraId, consumerId, onRecognition]
  );

  const enrollNow = React.useCallback(
    async (memberId: string, memberName?: string, imagesB64?: string[]) => {
      const res = await fetch(`${edgeAiUrl}/api/camera/enroll`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          camera_id: cameraId,
          consumer_id: consumerId,
          member_id: memberId,
          member_name: memberName,
          images_b64: imagesB64 || ['current'],
        }),
      });
      return await res.json();
    },
    [edgeAiUrl, cameraId, consumerId]
  );

  // Auto-start and strict-mode unmount cleanup
  React.useEffect(() => {
    if (!enabled) {
      setStatus('idle');
      return;
    }
    let didCancel = false;
    if (autoStart) {
      startCamera();
    } else {
      fetchStatus();
    }

    return () => {
      didCancel = true;
      if (heartbeatTimerRef.current) clearInterval(heartbeatTimerRef.current);
      if (statusPollTimerRef.current) clearInterval(statusPollTimerRef.current);

      if (statusRef.current === 'running') {
        // Send keepalive unregister to release session cleanly
        try {
          fetch(`${edgeAiUrl}/api/camera/stop`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ camera_id: cameraId, consumer_id: consumerId }),
            keepalive: true,
          }).catch(() => {});
        } catch {
          // Ignore unmount error
        }
      }
    };
  }, [autoStart, enabled]); // eslint-disable-line react-hooks/exhaustive-deps

  const subscribe = React.useCallback(
    async (targetCameraId?: string, targetConsumerId?: string, options?: Record<string, any>) => {
      const cid = targetCameraId || cameraId;
      const cons = targetConsumerId || consumerId;
      const res = await fetch(`${edgeAiUrl}/api/camera/subscribe`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ camera_id: cid, consumer_id: cons, options }),
      });
      return await res.json();
    },
    [edgeAiUrl, cameraId, consumerId]
  );

  const unsubscribe = React.useCallback(
    async (targetCameraId?: string, targetConsumerId?: string) => {
      const cid = targetCameraId || cameraId;
      const cons = targetConsumerId || consumerId;
      const res = await fetch(`${edgeAiUrl}/api/camera/unsubscribe`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ camera_id: cid, consumer_id: cons }),
      });
      return await res.json();
    },
    [edgeAiUrl, cameraId, consumerId]
  );

  const getCameraStatus = React.useCallback(
    async (targetCameraId?: string) => {
      const cid = targetCameraId || cameraId;
      const res = await fetch(`${edgeAiUrl}/api/camera/status?camera_id=${encodeURIComponent(cid)}`);
      return await res.json();
    },
    [edgeAiUrl, cameraId]
  );

  const getDiagnostics = React.useCallback(
    async (targetCameraId?: string) => {
      const cid = targetCameraId || cameraId;
      const res = await fetch(`${edgeAiUrl}/api/camera/diagnostics?camera_id=${encodeURIComponent(cid)}`);
      return await res.json();
    },
    [edgeAiUrl, cameraId]
  );

  return {
    status,
    activeConsumers,
    lastMatch,
    error,
    streamUrl,
    startCamera,
    stopCamera,
    subscribe,
    unsubscribe,
    heartbeat: sendHeartbeat,
    getCameraStatus,
    getDiagnostics,
    health,
    detectionReady: Boolean(
      health?.active_provider &&
        health.active_provider !== 'None' &&
        health?.stages?.detector
    ),
    detectionActive: Boolean(
      health?.active_provider &&
        health.active_provider !== 'None' &&
        health?.stages?.detector &&
        Number(health?.frames_processed || 0) > 0
    ),
    recognitionReady: Boolean(health?.stages?.face),
    reconnect,
    recognizeNow,
    enrollNow,
    fetchStatus,
  };
}

export interface CameraSessionViewProps {
  consumerId: string;
  cameraId?: string;
  edgeAiUrl?: string;
  title?: string;
  subtitle?: string;
  autoStart?: boolean;
  onRecognition?: (result: FaceRecognitionResult) => void;
  overlayChildren?: React.ReactNode;
  actionsChildren?: React.ReactNode;
  className?: string;
  sessionHook?: ReturnType<typeof useCameraSession>;
}

export function CameraSessionView({
  consumerId,
  cameraId = 'default',
  edgeAiUrl,
  title = 'Central Camera Stream',
  subtitle = 'Hardware camera multiplexed through central broker',
  autoStart = false,
  onRecognition,
  overlayChildren,
  actionsChildren,
  className,
  sessionHook,
}: CameraSessionViewProps) {
  const defaultHook = useCameraSession({
    consumerId,
    cameraId,
    edgeAiUrl,
    autoStart,
    onRecognition,
  });

  const session = sessionHook || defaultHook;
  const isRunning = session.status === 'running';
  const isStarting = session.status === 'starting';
  const isReconnecting = session.status === 'reconnecting';
  const detectionLabel = !isRunning
    ? 'Standby'
    : isStarting
    ? 'Model loading'
    : session.detectionActive
    ? 'Detection active'
    : session.detectionReady
    ? 'Waiting for frames'
    : 'Model offline';
  const recognitionLabel = !isRunning
    ? 'Offline'
    : session.recognitionReady
    ? 'Recognition ready'
    : 'Recognition unavailable';

  return (
    <div
      className={cn(
        'relative flex flex-col rounded-2xl border border-zinc-800 bg-zinc-950 p-4 shadow-2xl overflow-hidden',
        className
      )}
    >
      {/* Top Header & System Badges */}
      <div className="flex flex-wrap items-center justify-between gap-3 pb-3 border-b border-zinc-800/80">
        <div>
          <h3 className="text-base font-semibold text-zinc-100 flex items-center gap-2">
            <Camera className="h-5 w-5 text-emerald-400" />
            {title}
          </h3>
          {subtitle && <p className="text-xs text-zinc-400 mt-0.5">{subtitle}</p>}
        </div>

        {/* 4 Required Status Badges */}
        <div className="flex flex-wrap items-center gap-2">
          {/* Badge 1: Camera Connected / State */}
          <Badge
            variant={
              isRunning
                ? 'success'
                : isStarting || isReconnecting
                ? 'warning'
                : session.status === 'idle'
                ? 'outline'
                : 'destructive'
            }
            className="flex items-center gap-1.5 px-2.5 py-1 text-xs uppercase tracking-wider font-medium"
          >
            <span
              className={cn(
                'h-2 w-2 rounded-full',
                isRunning
                  ? 'bg-emerald-400 animate-pulse'
                  : isStarting || isReconnecting
                  ? 'bg-amber-400 animate-ping'
                  : 'bg-zinc-500'
              )}
            />
            Camera: {isRunning ? 'Connected' : session.status}
          </Badge>

          {/* Badge 2: Detection Active */}
          <Badge
            variant={session.detectionActive ? 'success' : session.detectionReady ? 'warning' : 'outline'}
            className="flex items-center gap-1.5 px-2.5 py-1 text-xs uppercase tracking-wider font-medium"
          >
            <Activity className={cn('h-3.5 w-3.5', session.detectionActive ? 'text-emerald-400' : 'text-zinc-500')} />
            Detection: {detectionLabel}
          </Badge>

          {/* Badge 3: Recognition Ready */}
          <Badge
            variant={session.recognitionReady ? 'secondary' : 'outline'}
            className="flex items-center gap-1.5 px-2.5 py-1 text-xs uppercase tracking-wider font-medium bg-blue-950/40 text-blue-300 border-blue-800/50"
          >
            <ShieldCheck className="h-3.5 w-3.5 text-blue-400" />
            Recognition: {recognitionLabel}
          </Badge>

          {/* Badge 4: Consumers Count */}
          <Badge
            variant="outline"
            className="flex items-center gap-1.5 px-2.5 py-1 text-xs uppercase tracking-wider font-medium border-zinc-700 text-zinc-300"
          >
            <Users className="h-3.5 w-3.5 text-indigo-400" />
            Consumers: {session.activeConsumers}
          </Badge>
        </div>
      </div>

      {/* Observable Model & Detection Telemetry Bar */}
      <div className="flex flex-wrap items-center justify-between gap-2 mt-2 px-3 py-1.5 rounded-lg bg-zinc-900/80 border border-zinc-800 text-[11px] font-mono text-zinc-400">
        <div className="flex flex-wrap items-center gap-3">
          <span>Frames: <strong className="text-zinc-200">{session.health?.frames_processed ?? 0}</strong></span>
          <span>Detections: <strong className="text-zinc-200">{session.health?.detections ?? 0}</strong></span>
          <span>Inference: <strong className="text-emerald-400">{session.health?.cameras?.[cameraId]?.inference_fps ?? 0} fps</strong></span>
        </div>
        <div className="flex flex-wrap items-center gap-3">
          <span>Provider: <strong className="text-zinc-300">{session.health?.active_provider || 'CPUExecutionProvider'}</strong></span>
          <span>Detector: <strong className={session.health?.stages?.detector ? 'text-emerald-400' : 'text-amber-400'}>{session.health?.stages?.detector || 'None'}</strong></span>
        </div>
      </div>

      {/* Main Video Viewport */}
      <div className="relative mt-3 flex-1 min-h-[300px] aspect-video w-full overflow-hidden rounded-xl bg-zinc-900 flex items-center justify-center border border-zinc-800">
        {isRunning ? (
          <>
            <img
              src={session.streamUrl}
              alt="Central Camera Stream"
              className="h-full w-full object-cover select-none"
              onError={() => {
                // If stream breaks, will fallback or reconnect
              }}
            />
            {/* Live Indicator overlay */}
            <div className="absolute top-3 left-3 flex items-center gap-1.5 rounded-md bg-black/60 px-2 py-1 text-[11px] font-semibold text-emerald-400 backdrop-blur-md border border-emerald-500/20">
              <span className="h-2 w-2 rounded-full bg-emerald-400 animate-pulse" />
              LIVE BROKER
            </div>
          </>
        ) : (
          <div className="flex flex-col items-center justify-center text-center p-6 space-y-3">
            <div className="rounded-full bg-zinc-800/80 p-4 text-zinc-400 border border-zinc-700">
              <Camera className="h-8 w-8 text-zinc-400" />
            </div>
            <div className="max-w-sm">
              <p className="text-sm font-medium text-zinc-200">
                {isStarting
                  ? 'Connecting to Central Camera Broker...'
                  : isReconnecting
                  ? 'Reconnecting to Hardware Camera...'
                  : 'Camera session is currently idle'}
              </p>
              <p className="text-xs text-zinc-500 mt-1">
                Click &quot;تشغيل الكاميرا / Start Camera&quot; to join this camera session without hardware conflicts.
              </p>
            </div>
            {session.error && (
              <div className="flex items-center gap-2 rounded-lg bg-red-950/50 border border-red-800/60 p-2.5 text-xs text-red-300">
                <AlertCircle className="h-4 w-4 text-red-400 shrink-0" />
                <span>{session.error}</span>
              </div>
            )}
          </div>
        )}

        {/* Custom Layer Overlays (e.g. Bounding Boxes, Face Reticle, Skeleton) */}
        {overlayChildren}
      </div>

      {/* Control Buttons Bar */}
      <div className="mt-4 flex flex-wrap items-center justify-between gap-3 pt-3 border-t border-zinc-800/80">
        {/* Exact Required Bilingual Control Buttons */}
        <div className="flex flex-wrap items-center gap-2">
          {!isRunning ? (
            <Button
              type="button"
              variant="default"
              size="sm"
              onClick={session.startCamera}
              disabled={isStarting || isReconnecting}
              className="bg-emerald-600 hover:bg-emerald-500 text-white font-medium"
            >
              <Power className="mr-1.5 h-4 w-4" />
              تشغيل الكاميرا / Start Camera
            </Button>
          ) : (
            <Button
              type="button"
              variant="destructive"
              size="sm"
              onClick={session.stopCamera}
              className="font-medium"
            >
              <Power className="mr-1.5 h-4 w-4" />
              إيقاف الكاميرا / Stop Camera
            </Button>
          )}

          <Button
            type="button"
            variant="outline"
            size="sm"
            onClick={session.reconnect}
            disabled={isStarting || isReconnecting}
            className="border-zinc-700 text-zinc-300 hover:bg-zinc-800 hover:text-white"
          >
            <RefreshCw className={cn('mr-1.5 h-4 w-4', isReconnecting && 'animate-spin')} />
            إعادة الاتصال / Reconnect
          </Button>
        </div>

        {/* Consumer-specific actions (e.g. Verify Face, Enroll Face, Workout Buttons) */}
        {actionsChildren && (
          <div className="flex flex-wrap items-center gap-2">
            {actionsChildren}
          </div>
        )}
      </div>
    </div>
  );
}

