'use client';

import { useCallback, useEffect, useRef, useState } from 'react';
import Link from 'next/link';
import Script from 'next/script';
import { Badge, Button, Card, CardContent, CardHeader, CardTitle, LiveDot, resolveEdgeAiUrl, useCameraSession } from '@smartgym/ui';
import {
  Camera,
  ChevronDown,
  ChevronUp,
  Sliders,
  CheckCircle2,
  XCircle,
  Play,
  Square,
  Users,
  RefreshCw,
  Cpu,
  Lock,
  Unlock,
  AlertTriangle,
  Power,
} from 'lucide-react';
import type { LiveSessionRow } from '@/lib/sessions-data';

function calcAngle(
  p1: { x: number; y: number },
  p2: { x: number; y: number },
  p3: { x: number; y: number }
): number {
  const rad = Math.atan2(p3.y - p2.y, p3.x - p2.x) - Math.atan2(p1.y - p2.y, p1.x - p2.x);
  let deg = Math.abs((rad * 180.0) / Math.PI);
  if (deg > 180.0) deg = 360.0 - deg;
  return Math.round(deg);
}

const SKELETON_PAIRS: [number, number][] = [
  [5, 6],   // shoulders
  [5, 7], [7, 9],  // left arm
  [6, 8], [8, 10], // right arm
  [5, 11], [6, 12], // torso
  [11, 12], // hips
  [11, 13], [13, 15], // left leg
  [12, 14], [14, 16], // right leg
];

export interface EdgeLivePerson {
  global_id: string;
  local_track_id: number;
  camera_id: string;
  member_id: string | null;
  member_name: string | null;
  identity_status: 'verified' | 'uncertain' | 'unknown';
  identity_confidence: number;
  tracking_confidence: number;
  box: [number, number, number, number];
  keypoints: Array<[number, number, number]>;
  exercise: string;
  phase: string;
  completed_reps: number;
  valid_reps: number;
  imperfect_reps: number;
  partial_reps: number;
  set_index: number;
  knee_angle_deg: number | null;
  trunk_lean_deg: number | null;
  form_verdict: string;
  active_errors: string[];
  status_message: string;
  rep_history: Array<{
    rep_number: number;
    duration_s: number;
    min_knee_angle_deg: number;
    max_trunk_lean_deg: number;
    is_valid: boolean;
    form_verdict: string;
    feedback: string;
  }>;
}

export function CameraVisionMonitor({
  activeSessions = [],
  cameraCount = 0,
}: {
  activeSessions?: LiveSessionRow[];
  cameraCount?: number;
}) {
  const [isOpen, setIsOpen] = useState(true);
  const [visionMode, setVisionMode] = useState<'edge-ai' | 'browser'>('edge-ai');

  const cameraSession = useCameraSession({
    consumerId: 'admin-workout-monitor',
    cameraId: 'default',
    autoStart: true,
  });
  const edgeAiBase = resolveEdgeAiUrl();

  // Edge AI Pipeline State
  const [edgeOnline, setEdgeOnline] = useState<boolean | null>(null);
  const [edgeCameraOwner, setEdgeCameraOwner] = useState<string>('edge-ai');
  const [edgeCameraPaused, setEdgeCameraPaused] = useState(false);
  const [edgePeople, setEdgePeople] = useState<EdgeLivePerson[]>([]);
  const [edgeActionBusy, setEdgeActionBusy] = useState(false);
  const [edgeError, setEdgeError] = useState<string | null>(null);

  // Browser MediaPipe State
  const [cameraActive, setCameraActive] = useState(false);
  const [modelReady, setModelReady] = useState(false);
  const [camError, setCamError] = useState<string | null>(null);
  const [kneeAngle, setKneeAngle] = useState(0);
  const [torsoLean, setTorsoLean] = useState(0);
  const [rightReps, setRightReps] = useState(0);
  const [wrongReps, setWrongReps] = useState(0);
  const [fsmState, setFsmState] = useState<'STANDBY' | 'READY' | 'DESCENDING' | 'BOTTOM' | 'ASCENDING'>('STANDBY');
  const [verdict, setVerdict] = useState<'RIGHT' | 'WRONG' | 'IDLE'>('IDLE');
  const [verdictText, setVerdictText] = useState('Click "Start Trainee Camera" to inspect single-trainee pose & form');

  const videoRef = useRef<HTMLVideoElement | null>(null);
  const localStreamRef = useRef<MediaStream | null>(null);
  const canvasRef = useRef<HTMLCanvasElement | null>(null);
  const edgeCanvasRef = useRef<HTMLCanvasElement | null>(null);
  const poseRef = useRef<any>(null);
  const fsmRef = useRef<'STANDBY' | 'READY' | 'DESCENDING' | 'BOTTOM' | 'ASCENDING'>('STANDBY');
  const minAngleRef = useRef(180);
  const faultRef = useRef<string | null>(null);
  const rightRepsRef = useRef(0);
  const wrongRepsRef = useRef(0);
  const timerRef = useRef<number>(0);

  useEffect(() => {
    return () => {
      if (timerRef.current) window.clearTimeout(timerRef.current);
      for (const track of localStreamRef.current?.getTracks() ?? []) track.stop();
      localStreamRef.current = null;
    };
  }, []);

  const activeSets = activeSessions.filter((s) => s.latest_set?.in_progress);
  const totalRepsInProgress = activeSets.reduce((acc, s) => acc + (s.latest_set?.rep_count ?? 0), 0);

  // ── Edge AI Polling Loop ───────────────────────────────────────────────────
  useEffect(() => {
    if (!isOpen || visionMode !== 'edge-ai') return;
    let cancelled = false;

    const pollEdgeAi = async () => {
      try {
        const res = await fetch(`${edgeAiBase}/live`, { signal: AbortSignal.timeout(2000) });
        if (!res.ok) throw new Error(`HTTP ${res.status}`);
        const data = await res.json();
        if (cancelled) return;

        setEdgeOnline(true);
        setEdgeCameraOwner(data.camera_owner ?? 'edge-ai');
        setEdgeCameraPaused(Boolean(data.camera_paused));
        setEdgePeople(data.people ?? []);
        setEdgeError(null);

        // Render multi-person skeletons on edge canvas
        drawEdgeOverlay(data.people ?? []);
      } catch (err) {
        if (cancelled) return;
        setEdgeOnline(false);
        setEdgeError('Edge AI service offline on port 8088. (Ensure `python -m edge_ai run` is executing)');
      }
    };

    void pollEdgeAi();
    const interval = setInterval(pollEdgeAi, 350);
    return () => {
      cancelled = true;
      clearInterval(interval);
    };
  }, [edgeAiBase, isOpen, visionMode]);

  // Draw multi-person bounding boxes, skeleton joints & rep badges on Canvas
  const drawEdgeOverlay = (people: EdgeLivePerson[]) => {
    const canvas = edgeCanvasRef.current;
    if (!canvas) return;
    const ctx = canvas.getContext('2d');
    if (!ctx) return;

    // Clear overlay
    ctx.clearRect(0, 0, canvas.width, canvas.height);

    for (const p of people) {
      const isRight = p.form_verdict === 'RIGHT FORM';
      const color = isRight ? '#10b981' : '#ef4444';

      // 1. Draw Bounding Box
      const [x1, y1, x2, y2] = p.box;
      const w = x2 - x1;
      const h = y2 - y1;

      ctx.strokeStyle = color;
      ctx.lineWidth = 2.5;
      ctx.strokeRect(x1, y1, w, h);

      // 2. Draw Label Header Pill
      const title = `${p.member_name || `Track #${p.local_track_id}`}: ${p.completed_reps} Reps (${p.form_verdict})`;
      ctx.font = 'bold 12px sans-serif';
      const textWidth = ctx.measureText(title).width;

      ctx.fillStyle = color;
      ctx.fillRect(x1, Math.max(0, y1 - 22), textWidth + 14, 22);

      ctx.fillStyle = '#ffffff';
      ctx.fillText(title, x1 + 7, Math.max(15, y1 - 6));

      // 3. Draw Skeleton Bones
      const kps = p.keypoints;
      if (kps && kps.length >= 17) {
        ctx.strokeStyle = color;
        ctx.lineWidth = 2;
        for (const [i1, i2] of SKELETON_PAIRS) {
          const pt1 = kps[i1];
          const pt2 = kps[i2];
          if (pt1 && pt2 && (pt1[2] ?? 0) > 0.25 && (pt2[2] ?? 0) > 0.25) {
            ctx.beginPath();
            ctx.moveTo(pt1[0], pt1[1]);
            ctx.lineTo(pt2[0], pt2[1]);
            ctx.stroke();
          }
        }

        // Draw Joints
        for (const pt of kps) {
          if ((pt[2] ?? 0) > 0.25) {
            ctx.beginPath();
            ctx.arc(pt[0], pt[1], 3.5, 0, 2 * Math.PI);
            ctx.fillStyle = '#38bdf8';
            ctx.fill();
            ctx.strokeStyle = '#ffffff';
            ctx.lineWidth = 1;
            ctx.stroke();
          }
        }
      }
    }
  };

  // ── Edge AI Camera Control Actions ─────────────────────────────────────────
  const handleAcquireCamera = async () => {
    setEdgeActionBusy(true);
    try {
      const res = await fetch(`${edgeAiBase}/camera/acquire`, { method: 'POST' });
      const data = await res.json();
      if (data.ok) {
        setEdgeCameraOwner('edge-ai');
        setEdgeCameraPaused(false);
      }
    } catch (e: any) {
      setEdgeError(`Acquire failed: ${e.message}`);
    } finally {
      setEdgeActionBusy(false);
    }
  };

  const handleReleaseCamera = async () => {
    setEdgeActionBusy(true);
    try {
      const res = await fetch(`${edgeAiBase}/camera/release`, { method: 'POST' });
      const data = await res.json();
      if (data.ok) {
        setEdgeCameraOwner('browser');
        setEdgeCameraPaused(true);
      }
    } catch (e: any) {
      setEdgeError(`Release failed: ${e.message}`);
    } finally {
      setEdgeActionBusy(false);
    }
  };

  const handleResetWorkout = async () => {
    setEdgeActionBusy(true);
    try {
      await fetch(`${edgeAiBase}/api/workout/reset`, { method: 'POST', body: '{}' });
      setEdgePeople([]);
    } catch (e: any) {
      setEdgeError(`Reset failed: ${e.message}`);
    } finally {
      setEdgeActionBusy(false);
    }
  };

  // ── Browser MediaPipe Single-Person Implementation ─────────────────────────
  const onPoseResults = useCallback((results: any) => {
    const canvas = canvasRef.current;
    const video = videoRef.current;
    if (!canvas || !video) return;
    const ctx = canvas.getContext('2d');
    if (!ctx) return;

    canvas.width = video.videoWidth || 640;
    canvas.height = video.videoHeight || 480;
    ctx.clearRect(0, 0, canvas.width, canvas.height);

    if (!results.poseLandmarks) {
      setVerdict('IDLE');
      setVerdictText('No trainee detected in camera view');
      return;
    }

    const lm = results.poseLandmarks;
    const lS = lm[11], rS = lm[12], lH = lm[23], rH = lm[24], lK = lm[25], rK = lm[26], lA = lm[27], rA = lm[28];
    const leftOk = (lH?.visibility ?? 0) > 0.35 && (lK?.visibility ?? 0) > 0.35 && (lA?.visibility ?? 0) > 0.35;
    const rightOk = (rH?.visibility ?? 0) > 0.35 && (rK?.visibility ?? 0) > 0.35 && (rA?.visibility ?? 0) > 0.35;

    let angle = 174;
    if (leftOk && rightOk) {
      angle = Math.round((calcAngle(lH, lK, lA) + calcAngle(rH, rK, rA)) / 2);
    } else if (leftOk) {
      angle = calcAngle(lH, lK, lA);
    } else if (rightOk) {
      angle = calcAngle(rH, rK, rA);
    }

    let lean = 12;
    if (lS && lH && (lS.visibility ?? 0) > 0.3 && (lH.visibility ?? 0) > 0.3) {
      const dx = Math.abs(lS.x - lH.x);
      const dy = Math.max(0.001, Math.abs(lH.y - lS.y));
      lean = Math.round(Math.atan2(dx, dy) * (180.0 / Math.PI));
    }

    setKneeAngle(angle);
    setTorsoLean(lean);

    const prevFsm = fsmRef.current;
    let nextFsm = prevFsm;

    if (prevFsm === 'STANDBY' && angle >= 155) {
      nextFsm = 'READY';
      minAngleRef.current = 180;
      faultRef.current = null;
    } else if (prevFsm === 'READY' && angle < 145) {
      nextFsm = 'DESCENDING';
    } else if (prevFsm === 'DESCENDING') {
      if (angle < minAngleRef.current) minAngleRef.current = angle;
      if (lean > 38 && !faultRef.current) {
        faultRef.current = 'Excessive forward trunk lean (> 38°)';
      }
      if (angle <= 95) {
        nextFsm = 'BOTTOM';
      } else if (angle > minAngleRef.current + 12) {
        nextFsm = 'ASCENDING';
      }
    } else if (prevFsm === 'BOTTOM') {
      if (angle < minAngleRef.current) minAngleRef.current = angle;
      if (angle > 105) nextFsm = 'ASCENDING';
    } else if (prevFsm === 'ASCENDING') {
      if (angle >= 155) {
        const reachedDepth = minAngleRef.current <= 95;
        const formFault = faultRef.current;

        if (reachedDepth && !formFault) {
          rightRepsRef.current += 1;
          setRightReps(rightRepsRef.current);
          setVerdict('RIGHT');
          setVerdictText(`✓ RIGHT FORM — Rep ${rightRepsRef.current} counted (Depth ${minAngleRef.current}°, Back ${lean}°)`);
        } else {
          wrongRepsRef.current += 1;
          setWrongReps(wrongRepsRef.current);
          setVerdict('WRONG');
          const issue = formFault || (minAngleRef.current > 95 ? `Shallow depth (${minAngleRef.current}° — target ≤ 90°)` : 'Form fault');
          setVerdictText(`✗ WRONG FORM — Fault rep ${wrongRepsRef.current}: ${issue}`);
        }
        nextFsm = 'READY';
        minAngleRef.current = 180;
        faultRef.current = null;
      }
    }

    fsmRef.current = nextFsm;
    setFsmState(nextFsm);

    // Draw skeleton on canvas
    const drawJoint = (p: any, col: string) => {
      if (!p || (p.visibility ?? 0) < 0.3) return;
      ctx.beginPath();
      ctx.arc(p.x * canvas.width, p.y * canvas.height, 4, 0, 2 * Math.PI);
      ctx.fillStyle = col;
      ctx.fill();
    };

    const drawBone = (a: any, b: any, col: string) => {
      if (!a || !b || (a.visibility ?? 0) < 0.3 || (b.visibility ?? 0) < 0.3) return;
      ctx.beginPath();
      ctx.moveTo(a.x * canvas.width, a.y * canvas.height);
      ctx.lineTo(b.x * canvas.width, b.y * canvas.height);
      ctx.strokeStyle = col;
      ctx.lineWidth = 3;
      ctx.stroke();
    };

    const bColor = fsmRef.current === 'BOTTOM' || nextFsm === 'READY' ? '#10b981' : '#38bdf8';
    drawBone(lS, rS, bColor);
    drawBone(lS, lH, bColor);
    drawBone(rS, rH, bColor);
    drawBone(lH, rH, bColor);
    drawBone(lH, lK, bColor);
    drawBone(rH, rK, bColor);
    drawBone(lK, lA, bColor);
    drawBone(rK, rA, bColor);

    [lS, rS, lH, rH, lK, rK, lA, rA].forEach((p) => drawJoint(p, '#ffffff'));
  }, []);

  const initPose = useCallback(() => {
    if (typeof window === 'undefined' || !(window as any).Pose) return;
    try {
      const pose = new (window as any).Pose({
        locateFile: (file: string) => `https://cdn.jsdelivr.net/npm/@mediapipe/pose/${file}`,
      });
      pose.setOptions({
        modelComplexity: 1,
        smoothLandmarks: true,
        minDetectionConfidence: 0.5,
        minTrackingConfidence: 0.5,
      });
      pose.onResults(onPoseResults);
      poseRef.current = pose;
      setModelReady(true);
    } catch (e) {
      console.warn('Admin Pose init error:', e);
    }
  }, [onPoseResults]);

  const brokerImgRef = useRef<HTMLImageElement | null>(null);

  const toggleLiveCamera = async () => {
    if (cameraActive) {
      if (timerRef.current) window.clearTimeout(timerRef.current);
      for (const track of localStreamRef.current?.getTracks() ?? []) track.stop();
      localStreamRef.current = null;
      if (videoRef.current) videoRef.current.srcObject = null;
      setCameraActive(false);
      return;
    }
    setCamError(null);
    try {
      if (!window.isSecureContext && window.location.hostname !== 'localhost' && window.location.hostname !== '127.0.0.1') {
        throw new Error('Browser camera access requires HTTPS (or localhost).');
      }
      if (!navigator.mediaDevices?.getUserMedia) {
        throw new Error('This browser does not expose camera access.');
      }
      // Step 1: Stop Edge AI consumer session
      await cameraSession.stopCamera();
      // Step 2: Request Edge AI to release physical camera handle
      await handleReleaseCamera();
      // Step 3: Wait 300ms for OS/Windows DirectShow driver to release the hardware
      await new Promise((resolve) => setTimeout(resolve, 300));

      // Step 4: Open browser camera stream safely
      const stream = await navigator.mediaDevices.getUserMedia({ video: { facingMode: { ideal: 'user' } }, audio: false });
      localStreamRef.current = stream;
      if (!videoRef.current) throw new Error('Browser camera preview is not ready. Try again.');
      videoRef.current.srcObject = stream;
      await videoRef.current.play().catch(() => undefined);
      setCameraActive(true);
      setTimeout(() => {
        initPose();
      }, 100);
    } catch (err: any) {
      const errName = err?.name || '';
      const errMsg = err?.message || String(err);
      if (errName === 'NotReadableError' || errMsg.includes('Device in use') || errMsg.includes('NotReadableError')) {
        setCamError('Camera device is currently locked by another application or the Windows camera driver. Please wait 1-2 seconds and try again.');
      } else {
        setCamError(`Could not open camera stream: ${errMsg}`);
      }
    }
  };

  const selectVisionMode = async (nextMode: 'edge-ai' | 'browser') => {
    if (nextMode === visionMode) return;
    if (cameraActive) {
      if (timerRef.current) window.clearTimeout(timerRef.current);
      for (const track of localStreamRef.current?.getTracks() ?? []) track.stop();
      localStreamRef.current = null;
      if (videoRef.current) videoRef.current.srcObject = null;
      setCameraActive(false);
    }
    setVisionMode(nextMode);
    if (nextMode === 'edge-ai') {
      // Allow hardware device to settle before re-acquiring on Edge AI
      await new Promise((resolve) => setTimeout(resolve, 250));
      await handleAcquireCamera();
      try {
        await cameraSession.startCamera();
      } catch (err) {
        setEdgeError(`Could not reconnect Edge AI camera: ${(err as Error).message}`);
      }
    } else {
      // Switching to browser mode: release backend pipeline
      await cameraSession.stopCamera();
      await handleReleaseCamera();
    }
  };

  useEffect(() => {
    if (!cameraActive) return;
    let cancelled = false;
    const pump = async () => {
      if (cancelled) return;
      if (!poseRef.current && (window as any).Pose) {
        initPose();
      }
      const img = brokerImgRef.current;
      const vid = videoRef.current;
      if (poseRef.current) {
        try {
          if (img && img.complete && img.naturalWidth > 0) {
            await poseRef.current.send({ image: img });
          } else if (vid && vid.readyState >= 2) {
            await poseRef.current.send({ image: vid });
          }
        } catch {
          // ignore frame error
        }
      }
      if (!cancelled) timerRef.current = window.setTimeout(pump, 80);
    };
    void pump();
    return () => {
      cancelled = true;
      if (timerRef.current) window.clearTimeout(timerRef.current);
    };
  }, [cameraActive, modelReady, initPose]);

  return (
    <Card className="border-border shadow-sm">
      <Script
        src="https://cdn.jsdelivr.net/npm/@mediapipe/pose/pose.js"
        strategy="afterInteractive"
        onLoad={() => initPose()}
      />
      <CardHeader
        className="py-3 px-4 flex flex-row items-center justify-between cursor-pointer select-none"
        onClick={() => setIsOpen(!isOpen)}
      >
        <div className="flex items-center gap-2">
          <Camera className="h-4 w-4 text-primary" />
          <CardTitle className="text-sm font-semibold">
            Gym Camera Vision &amp; Live Trainee Form Monitor
          </CardTitle>
          <Badge variant="outline" className="text-[10px]">
            {visionMode === 'edge-ai' ? (
              edgeOnline ? (
                <span className="flex items-center gap-1 text-emerald-400">
                  <LiveDot active /> Edge AI Multi-Person Active ({edgePeople.length} in view)
                </span>
              ) : (
                <span className="flex items-center gap-1 text-amber-400">
                  <AlertTriangle className="h-3 w-3" /> Edge AI Offline (:8088)
                </span>
              )
            ) : cameraActive ? (
              <span className="flex items-center gap-1 text-emerald-400">
                <LiveDot active /> Browser Single-Camera Active
              </span>
            ) : (
              'Ready — Select Vision Pipeline Mode'
            )}
          </Badge>
        </div>
        <div className="flex items-center gap-2">
          <Link
            href="/diagnostics"
            onClick={(e) => e.stopPropagation()}
            className="text-[11px] text-muted-foreground hover:text-primary transition-colors flex items-center gap-1"
          >
            <Sliders className="h-3 w-3" /> FSM Diagnostics Lab
          </Link>
          <Button variant="ghost" size="sm" className="h-8 w-8 p-0">
            {isOpen ? <ChevronUp className="h-4 w-4" /> : <ChevronDown className="h-4 w-4" />}
          </Button>
        </div>
      </CardHeader>

      {isOpen && (
        <CardContent className="space-y-4 pt-2">
          {/* Mode Switcher */}
          <div className="flex items-center justify-between border-b border-border pb-3">
            <div className="flex gap-2">
              <Button
                type="button"
                size="sm"
                variant={visionMode === 'edge-ai' ? 'default' : 'outline'}
                onClick={() => void selectVisionMode('edge-ai')}
                className="gap-1.5 text-xs"
              >
                <Cpu className="h-3.5 w-3.5" /> Edge AI Multi-Person Pipeline (Port 8088)
              </Button>
              <Button
                type="button"
                size="sm"
                variant={visionMode === 'browser' ? 'default' : 'outline'}
                onClick={() => void selectVisionMode('browser')}
                className="gap-1.5 text-xs"
              >
                <Camera className="h-3.5 w-3.5" /> Browser Single-Trainee (MediaPipe)
              </Button>
            </div>
            <div className="flex flex-wrap items-center gap-1.5">
              <Badge
                variant={cameraSession.status === 'running' ? 'success' : cameraSession.status === 'idle' ? 'outline' : 'destructive'}
                className="text-[10px] uppercase font-medium"
              >
                <span className={`h-1.5 w-1.5 rounded-full mr-1 ${cameraSession.status === 'running' ? 'bg-emerald-400 animate-pulse' : 'bg-zinc-500'}`} />
                Camera: {cameraSession.status === 'running' ? 'Connected' : cameraSession.status}
              </Badge>

              <Badge variant={cameraSession.detectionActive ? 'success' : cameraSession.detectionReady ? 'warning' : 'outline'} className="text-[10px] uppercase font-medium">
                Detection: {cameraSession.detectionActive ? 'Active' : cameraSession.detectionReady ? 'Waiting for frames' : 'Model offline'}
              </Badge>

              <Badge variant="outline" className="text-[10px] uppercase font-medium bg-blue-950/40 text-blue-300 border-blue-800/40">
                Recognition: {cameraSession.recognitionReady ? 'Ready' : 'Model offline'}
              </Badge>

              <Badge variant="outline" className="text-[10px] uppercase font-medium text-zinc-300 border-zinc-700">
                Consumers: {cameraSession.activeConsumers}
              </Badge>

              {cameraSession.status !== 'running' ? (
                <Button
                  type="button"
                  size="sm"
                  onClick={() => cameraSession.startCamera()}
                  className="h-7 text-xs bg-emerald-600 hover:bg-emerald-500 text-white gap-1"
                >
                  <Power className="h-3 w-3" /> تشغيل الكاميرا / Start Camera
                </Button>
              ) : (
                <Button
                  type="button"
                  size="sm"
                  variant="destructive"
                  onClick={() => cameraSession.stopCamera()}
                  className="h-7 text-xs gap-1"
                >
                  <Power className="h-3 w-3" /> إيقاف الكاميرا / Stop Camera
                </Button>
              )}

              <Button
                type="button"
                size="sm"
                variant="outline"
                onClick={() => cameraSession.reconnect()}
                className="h-7 text-xs gap-1"
              >
                <RefreshCw className="h-3 w-3" /> إعادة الاتصال / Reconnect
              </Button>

              <Button
                type="button"
                size="sm"
                variant="ghost"
                disabled={edgeActionBusy}
                onClick={handleResetWorkout}
                className="h-7 text-xs gap-1 text-muted-foreground"
              >
                <RefreshCw className="h-3 w-3" /> Reset Reps
              </Button>
            </div>
          </div>

          {/* MODE 1: Edge AI Multi-Person Stream */}
          {visionMode === 'edge-ai' && (
            <div className="space-y-4">
              {edgeError && (
                <div className="rounded-lg border border-amber-500/40 bg-amber-500/10 p-3 text-xs text-amber-300 flex items-center justify-between">
                  <div className="flex items-center gap-2">
                    <AlertTriangle className="h-4 w-4 shrink-0 text-amber-400" />
                    <span>{edgeError}</span>
                  </div>
                  <Button
                    size="sm"
                    variant="outline"
                    className="h-7 text-xs"
                    onClick={() => void selectVisionMode('browser')}
                  >
                    Switch to Browser Camera
                  </Button>
                </div>
              )}

              {/* Edge AI Multi-Person Video Canvas & Telemetry */}
              <div className="grid gap-4 lg:grid-cols-[1.5fr_1fr]">
                <div className="relative aspect-video w-full overflow-hidden rounded-xl bg-black border border-border">
                  {/* Live JPEG Stream from Edge AI Worker */}
                  <img
                    src={`${edgeAiBase}/camera/frame.jpg`}
                    alt="Edge AI Video Feed"
                    className="h-full w-full object-contain"
                    onError={(e) => {
                      (e.target as HTMLElement).style.opacity = '0.3';
                    }}
                    onLoad={(e) => {
                      (e.target as HTMLElement).style.opacity = '1';
                    }}
                  />
                  {/* Real-time Multi-Person Keypoints Overlay */}
                  <canvas
                    ref={edgeCanvasRef}
                    width={800}
                    height={480}
                    className="absolute inset-0 h-full w-full pointer-events-none"
                  />

                  {/* Top Stats Overlay */}
                  <div className="absolute top-3 left-3 flex gap-2">
                    <Badge className="bg-emerald-600/90 text-white backdrop-blur-md">
                      <Users className="h-3.5 w-3.5 mr-1" /> {edgePeople.length} People Tracked
                    </Badge>
                    <Badge variant="outline" className="bg-black/60 text-white backdrop-blur-md border-white/20">
                      Camera: {edgeCameraPaused ? 'Released to Browser' : 'Controlled by Edge AI'}
                    </Badge>
                  </div>
                </div>

                {/* Per-Person Real-time Telemetry List */}
                <div className="space-y-2.5 max-h-[420px] overflow-y-auto pr-1">
                  <h4 className="text-xs font-semibold uppercase tracking-wider text-muted-foreground flex items-center justify-between">
                    <span>Simultaneous Trainees In View</span>
                    <span className="font-mono">{edgePeople.length} active</span>
                  </h4>

                  {edgePeople.length === 0 ? (
                    <div className="rounded-xl border border-dashed border-border p-6 text-center text-xs text-muted-foreground">
                      <Users className="h-6 w-6 mx-auto mb-2 opacity-40 text-primary" />
                      Camera active on Edge AI — Waiting for trainees to step in front of camera...
                    </div>
                  ) : (
                    edgePeople.map((person) => {
                      const isRight = person.form_verdict === 'RIGHT FORM';
                      return (
                        <div
                          key={person.global_id}
                          className={`rounded-xl border p-3.5 space-y-2 transition-all ${
                            isRight
                              ? 'border-emerald-500/40 bg-emerald-500/5'
                              : 'border-red-500/40 bg-red-500/10'
                          }`}
                        >
                          <div className="flex items-center justify-between">
                            <div className="flex items-center gap-2">
                              {isRight ? (
                                <CheckCircle2 className="h-4 w-4 text-emerald-400 shrink-0" />
                              ) : (
                                <XCircle className="h-4 w-4 text-red-400 shrink-0" />
                              )}
                              <div>
                                <h5 className="font-bold text-sm text-foreground">
                                  {person.member_name || `Trainee #${person.local_track_id}`}
                                </h5>
                                <p className="text-[10px] text-muted-foreground font-mono">
                                  Track {person.global_id.slice(0, 10)} · {person.identity_status}
                                </p>
                              </div>
                            </div>
                            <Badge
                              variant="outline"
                              className={`text-xs font-bold ${
                                isRight ? 'border-emerald-500 text-emerald-400' : 'border-red-500 text-red-400'
                              }`}
                            >
                              {person.form_verdict}
                            </Badge>
                          </div>

                          <div className="grid grid-cols-4 gap-1.5 text-center pt-1 border-t border-border/50">
                            <div className="rounded bg-card/60 p-1.5 border border-border">
                              <p className="text-[9px] uppercase text-muted-foreground">Total Reps</p>
                              <p className="font-bold text-sm text-primary tabular-nums">{person.completed_reps}</p>
                            </div>
                            <div className="rounded bg-card/60 p-1.5 border border-border">
                              <p className="text-[9px] uppercase text-muted-foreground">Valid / Fault</p>
                              <p className="font-bold text-xs tabular-nums text-foreground">
                                <span className="text-emerald-400">{person.valid_reps}</span> /{' '}
                                <span className="text-red-400">{person.imperfect_reps}</span>
                              </p>
                            </div>
                            <div className="rounded bg-card/60 p-1.5 border border-border">
                              <p className="text-[9px] uppercase text-muted-foreground">Knee Angle</p>
                              <p className="font-bold text-xs text-emerald-400 tabular-nums">
                                {person.knee_angle_deg ? `${person.knee_angle_deg}°` : '—'}
                              </p>
                            </div>
                            <div className="rounded bg-card/60 p-1.5 border border-border">
                              <p className="text-[9px] uppercase text-muted-foreground">Trunk Lean</p>
                              <p className="font-bold text-xs text-sky-400 tabular-nums">
                                {person.trunk_lean_deg ? `${person.trunk_lean_deg}°` : '—'}
                              </p>
                            </div>
                          </div>

                          {person.active_errors && person.active_errors.length > 0 && (
                            <div className="flex flex-wrap gap-1 pt-1">
                              {person.active_errors.map((err, i) => (
                                <Badge key={i} variant="destructive" className="text-[10px] py-0 px-1.5">
                                  {err.replace(/_/g, ' ')}
                                </Badge>
                              ))}
                            </div>
                          )}

                          <p className="text-[11px] text-muted-foreground italic truncate">
                            {person.status_message}
                          </p>
                        </div>
                      );
                    })
                  )}
                </div>
              </div>
            </div>
          )}

          {/* MODE 2: Browser MediaPipe Direct Trainee Camera */}
          {visionMode === 'browser' && (
            <div className="rounded-xl border border-primary/30 bg-card p-4 space-y-3">
              <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-3">
                <div>
                  <h4 className="font-semibold text-sm text-foreground flex items-center gap-2">
                    <Camera className="h-4 w-4 text-primary" />
                    Browser Trainee Camera (MediaPipe Pose)
                  </h4>
                  <p className="text-xs text-muted-foreground">
                    Inspect single trainee posture directly via browser camera when Edge AI service is not running.
                  </p>
                </div>
                <Button
                  type="button"
                  size="sm"
                  variant={cameraActive ? 'destructive' : 'default'}
                  onClick={toggleLiveCamera}
                  className="gap-1.5 shrink-0"
                >
                  {cameraActive ? (
                    <>
                      <Square className="h-3.5 w-3.5 fill-current" /> Stop Trainee Camera
                    </>
                  ) : (
                    <>
                      <Play className="h-3.5 w-3.5 fill-current" /> Start Live Trainee Camera
                    </>
                  )}
                </Button>
              </div>

              {camError && <p className="text-xs text-red-400">{camError}</p>}

              {cameraActive && (
                <div className="grid gap-4 lg:grid-cols-[1.4fr_1fr] pt-2">
                  <div className="relative aspect-video w-full overflow-hidden rounded-xl bg-black border border-border">
                    <video
                      ref={videoRef}
                      playsInline
                      muted
                      autoPlay
                      className="h-full w-full object-cover -scale-x-100"
                    />
                    <canvas
                      ref={canvasRef}
                      className="absolute inset-0 h-full w-full pointer-events-none -scale-x-100"
                    />
                    <div className="absolute top-3 left-3 flex gap-2">
                      <Badge className="bg-emerald-600 text-white">✓ Right: {rightReps}</Badge>
                      <Badge variant="destructive">✗ Wrong: {wrongReps}</Badge>
                    </div>
                    <div className="absolute bottom-3 inset-x-3 bg-black/80 backdrop-blur-md px-3 py-1.5 rounded-lg border border-white/15 text-xs text-white text-center">
                      {verdictText}
                    </div>
                  </div>

                  <div className="space-y-3 flex flex-col justify-between">
                    <div
                      className={`rounded-xl border p-3.5 ${
                        verdict === 'RIGHT'
                          ? 'border-emerald-500/40 bg-emerald-500/10'
                          : verdict === 'WRONG'
                          ? 'border-red-500/50 bg-red-500/15'
                          : 'border-border bg-muted/20'
                      }`}
                    >
                      <div className="flex items-center gap-2">
                        {verdict === 'RIGHT' ? (
                          <CheckCircle2 className="h-5 w-5 text-emerald-400" />
                        ) : verdict === 'WRONG' ? (
                          <XCircle className="h-5 w-5 text-red-400" />
                        ) : (
                          <Camera className="h-5 w-5 text-primary" />
                        )}
                        <span className="text-xs font-bold uppercase tracking-wider">
                          {verdict === 'RIGHT'
                            ? '✓ RIGHT FORM'
                            : verdict === 'WRONG'
                            ? '✗ WRONG FORM'
                            : 'WAITING FOR POSE'}
                        </span>
                      </div>
                      <p className="text-xs mt-1 text-foreground">{verdictText}</p>
                    </div>

                    <div className="grid grid-cols-2 gap-2 text-center">
                      <div className="rounded-lg border border-border p-2.5 bg-muted/20">
                        <p className="text-[10px] uppercase text-muted-foreground">Knee Angle</p>
                        <p className="text-lg font-bold text-emerald-400 tabular-nums">
                          {kneeAngle > 0 ? `${kneeAngle}°` : '—'}
                        </p>
                      </div>
                      <div className="rounded-lg border border-border p-2.5 bg-muted/20">
                        <p className="text-[10px] uppercase text-muted-foreground">Back Lean</p>
                        <p className={`text-lg font-bold tabular-nums ${torsoLean > 38 ? 'text-red-400' : 'text-sky-400'}`}>
                          {torsoLean > 0 ? `${torsoLean}°` : '—'}
                        </p>
                      </div>
                      <div className="rounded-lg border border-border p-2.5 bg-muted/20">
                        <p className="text-[10px] uppercase text-muted-foreground">FSM State</p>
                        <p className="text-xs font-mono font-bold text-foreground mt-1">{fsmState}</p>
                      </div>
                      <div className="rounded-lg border border-border p-2.5 bg-muted/20">
                        <p className="text-[10px] uppercase text-muted-foreground">Valid / Fault Reps</p>
                        <p className="text-sm font-bold text-foreground mt-0.5">
                          <span className="text-emerald-400">{rightReps}</span> / <span className="text-red-400">{wrongReps}</span>
                        </p>
                      </div>
                    </div>
                  </div>
                </div>
              )}
            </div>
          )}

          {activeSets.length > 0 && (
            <div className="space-y-3">
              <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
                {activeSets.map((session) => {
                  const set = session.latest_set;
                  if (!set) return null;
                  return (
                    <div
                      key={session.id}
                      className="rounded-xl border border-primary/30 bg-primary/5 p-4 space-y-3 shadow-sm"
                    >
                      <div className="flex items-center justify-between">
                        <div>
                          <p className="text-xs font-semibold text-primary uppercase tracking-wider">
                            Active Workout Set
                          </p>
                          <h4 className="font-bold text-sm text-foreground">{session.member_name}</h4>
                          <p className="text-[11px] text-muted-foreground font-mono">{session.member_code}</p>
                        </div>
                        <Badge variant="default" className="bg-primary/20 text-primary border-primary/30 text-xs">
                          <LiveDot active className="mr-1" /> Live Set {set.set_index}
                        </Badge>
                      </div>

                      <div className="grid grid-cols-3 gap-2 pt-2 border-t border-border/60 text-center">
                        <div className="rounded-lg bg-card/60 p-2 border border-border">
                          <p className="text-[10px] text-muted-foreground uppercase">Exercise</p>
                          <p className="font-semibold text-xs text-foreground truncate">{set.exercise_name}</p>
                        </div>
                        <div className="rounded-lg bg-card/60 p-2 border border-border">
                          <p className="text-[10px] text-muted-foreground uppercase">Reps Counted</p>
                          <p className="font-bold text-lg text-primary tabular-nums">{set.rep_count}</p>
                        </div>
                        <div className="rounded-lg bg-card/60 p-2 border border-border">
                          <p className="text-[10px] text-muted-foreground uppercase">Quality</p>
                          <p className="font-semibold text-xs text-emerald-400">
                            {set.verified ? 'Verified' : 'Evaluating'}
                          </p>
                        </div>
                      </div>
                    </div>
                  );
                })}
              </div>
            </div>
          )}

          <div className="grid grid-cols-2 sm:grid-cols-4 gap-3 text-sm">
            <div className="rounded-lg border border-border p-3 bg-muted/20">
              <p className="text-xs text-muted-foreground">Configured Cameras</p>
              <p className="text-base font-bold text-foreground mt-0.5">{cameraCount} active</p>
            </div>
            <div className="rounded-lg border border-border p-3 bg-muted/20">
              <p className="text-xs text-muted-foreground">Active Sets In Gym</p>
              <p className="text-base font-bold text-emerald-400 mt-0.5 tabular-nums">{activeSets.length}</p>
            </div>
            <div className="rounded-lg border border-border p-3 bg-muted/20">
              <p className="text-xs text-muted-foreground">Reps in Flight</p>
              <p className="text-base font-bold text-primary mt-0.5 tabular-nums">{totalRepsInProgress}</p>
            </div>
            <div className="rounded-lg border border-border p-3 bg-muted/20">
              <p className="text-xs text-muted-foreground">Edge AI Service</p>
              <p className="text-xs font-semibold text-emerald-500 mt-1 flex items-center gap-1">
                <LiveDot active={edgeOnline === true} /> ONNX Multi-Person
              </p>
            </div>
          </div>
        </CardContent>
      )}
    </Card>
  );
}
