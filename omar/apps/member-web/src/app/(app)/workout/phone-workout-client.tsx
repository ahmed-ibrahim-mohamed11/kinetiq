'use client';

import { useEffect, useRef, useState, useCallback } from 'react';
import Link from 'next/link';
import Script from 'next/script';
import { Button, Card, CardContent, CardHeader, CardTitle, Badge, useCameraSession } from '@smartgym/ui';
import {
  Volume2,
  VolumeX,
  Camera,
  Activity,
  RotateCcw,
  CheckCircle2,
  AlertTriangle,
  XCircle,
  Play,
  Square,
  Loader2,
  Sparkles,
  RefreshCw,
  Power,
} from 'lucide-react';
import { savePhoneCameraWorkoutSession } from '@/lib/actions';

declare global {
  interface Window {
    Pose?: any;
  }
}

// 2D interior joint angle from normalized landmark points
function calculateAngle(
  p1: { x: number; y: number },
  p2: { x: number; y: number },
  p3: { x: number; y: number }
): number {
  const radians = Math.atan2(p3.y - p2.y, p3.x - p2.x) - Math.atan2(p1.y - p2.y, p1.x - p2.x);
  let angle = Math.abs((radians * 180.0) / Math.PI);
  if (angle > 180.0) angle = 360.0 - angle;
  return Math.round(angle);
}

// Torso lean angle relative to vertical (0° = perfectly upright, >38° = excessive forward lean)
function calculateTorsoLean(
  shoulder: { x: number; y: number },
  hip: { x: number; y: number }
): number {
  const dx = Math.abs(shoulder.x - hip.x);
  const dy = Math.max(0.001, Math.abs(hip.y - shoulder.y));
  return Math.round((Math.atan2(dx, dy) * 180.0) / Math.PI);
}

function parseCameraError(err: any, hostname = 'localhost'): string {
  const name = err?.name || '';
  const msg = err?.message || '';

  if (name === 'NotAllowedError' || name === 'PermissionDeniedError' || msg.includes('Permission denied') || msg.includes('Permission dismissed')) {
    return '🚫 Camera Permission Denied: Your handset or browser blocked camera access. Tap the lock/shield icon in the browser address bar, set Camera to "Allow", then tap Start Camera.';
  }
  if (name === 'NotFoundError' || name === 'DevicesNotFoundError' || msg.includes('Requested device not found')) {
    return '📷 Camera Not Found: No camera device was detected on your handset.';
  }
  if (name === 'NotReadableError' || name === 'TrackStartError' || msg.includes('Could not start video source') || msg.includes('Device in use')) {
    return '📹 Camera In Use: The camera hardware is currently locked by another application. Close other camera apps and try again.';
  }
  if (name === 'OverconstrainedError' || name === 'ConstraintNotSatisfiedError') {
    return '⚙️ Camera Constraint Error: The requested camera resolution or facing direction is not supported by your hardware.';
  }
  if (msg.includes('Insecure') || msg.includes('HTTPS')) {
    return `🔒 Insecure Context: Phone browsers strictly require HTTPS for camera access. Open https://${hostname}:3001/cert to install the certificate.`;
  }
  return `Camera Error: ${msg || name || 'Could not initialize video stream.'}`;
}

export function PhoneWorkoutClient({ memberName }: { memberName: string }) {
  const videoRef = useRef<HTMLVideoElement | null>(null);
  const brokerImgRef = useRef<HTMLImageElement | null>(null);
  const canvasRef = useRef<HTMLCanvasElement | null>(null);
  const [modelLoaded, setModelLoaded] = useState(false);
  const [modelError, setModelError] = useState<string | null>(null);
  const [streamActive, setStreamActive] = useState(false);
  const [localCameraActive, setLocalCameraActive] = useState(false);
  const [cameraSource, setCameraSource] = useState<'phone' | 'broker'>('phone');
  const [poseFrameCount, setPoseFrameCount] = useState(0);
  const [cameraError, setCameraError] = useState<string | null>(null);
  const [facingMode, setFacingMode] = useState<'user' | 'environment'>('user');
  const [voiceEnabled, setVoiceEnabled] = useState(true);
  const [isExercising, setIsExercising] = useState(false);

  const brokerSession = useCameraSession({
    consumerId: 'member-phone-workout',
    cameraId: 'default',
    autoStart: false,
    enabled: cameraSource === 'broker',
  });

  const isBrokerRunning = brokerSession.status === 'running';
  const isCameraRunning = cameraSource === 'broker' ? isBrokerRunning : localCameraActive;
  const detectionActive = modelLoaded && isCameraRunning && poseFrameCount > 0;
  const localStreamRef = useRef<MediaStream | null>(null);

  // Biomechanical state derived from real camera pose
  const [repCount, setRepCount] = useState(0);
  const [wrongRepCount, setWrongRepCount] = useState(0);
  const [currentState, setCurrentState] = useState<'STANDBY' | 'READY' | 'DESCENDING' | 'BOTTOM' | 'ASCENDING'>('STANDBY');
  const [currentKneeAngle, setCurrentKneeAngle] = useState(0);
  const [currentTorsoAngle, setCurrentTorsoAngle] = useState(0);
  const [formStatus, setFormStatus] = useState<'RIGHT' | 'WRONG' | 'WAITING'>('WAITING');
  const [formFaultReason, setFormFaultReason] = useState<string | null>(null);
  const [feedback, setFeedback] = useState('Position yourself in front of the camera to begin live form analysis');
  const [workoutFinished, setWorkoutFinished] = useState(false);
  const [saving, setSaving] = useState(false);
  const [savedSessionId, setSavedSessionId] = useState<string | null>(null);
  const [saveError, setSaveError] = useState<string | null>(null);

  const startTimeRef = useRef<number>(0);
  const repCountRef = useRef(0);
  const wrongRepCountRef = useRef(0);
  const minAngleInRepRef = useRef<number>(180);
  const maxTorsoInRepRef = useRef<number>(0);
  const hadFaultInRepRef = useRef<string | null>(null);
  const repTelemetryRef = useRef<Array<{
    rep_number: number;
    verdict: 'correct' | 'incorrect';
    min_knee_angle_deg: number;
    max_trunk_lean_deg: number;
    detected_errors: string[];
    confidence: number;
  }>>([]);
  const stateRef = useRef<'STANDBY' | 'READY' | 'DESCENDING' | 'BOTTOM' | 'ASCENDING'>('STANDBY');
  const lastRepTimeRef = useRef<number>(0);
  const poseInstanceRef = useRef<any>(null);
  const rafRef = useRef<number>(0);

  // Voice coaching assistant
  const speak = useCallback(
    (text: string) => {
      if (!voiceEnabled || typeof window === 'undefined' || !window.speechSynthesis) return;
      try {
        window.speechSynthesis.cancel();
        const utterance = new SpeechSynthesisUtterance(text);
        utterance.rate = 1.05;
        utterance.pitch = 1.0;
        window.speechSynthesis.speak(utterance);
      } catch {
        // Fallback silently if audio fails
      }
    },
    [voiceEnabled]
  );

  // Process Real Pose Estimation Results from Camera
  const onPoseResults = useCallback(
    (results: any) => {
      const canvas = canvasRef.current;
      const video = videoRef.current;
      const img = brokerImgRef.current;
      if (!canvas) return;

      const ctx = canvas.getContext('2d');
      if (!ctx) return;

      const sourceW = (cameraSource === 'broker' && brokerSession.status === 'running' && img?.naturalWidth) ? img.naturalWidth : (video?.videoWidth || 640);
      const sourceH = (cameraSource === 'broker' && brokerSession.status === 'running' && img?.naturalHeight) ? img.naturalHeight : (video?.videoHeight || 480);

      canvas.width = sourceW;
      canvas.height = sourceH;

      ctx.save();
      ctx.clearRect(0, 0, canvas.width, canvas.height);

      if (!results.poseLandmarks) {
        setCurrentState('STANDBY');
        setCurrentKneeAngle(0);
        setCurrentTorsoAngle(0);
        setFormStatus('WAITING');
        setFormFaultReason(null);
        setFeedback('No trainee detected — step into camera view');
        ctx.restore();
        return;
      }

      const lm = results.poseLandmarks;

      // Keypoints: 11/12 shoulders, 23/24 hips, 25/26 knees, 27/28 ankles
      const lShoulder = lm[11];
      const rShoulder = lm[12];
      const lHip = lm[23];
      const rHip = lm[24];
      const lKnee = lm[25];
      const rKnee = lm[26];
      const lAnkle = lm[27];
      const rAnkle = lm[28];

      const leftVis = (lHip?.visibility ?? 0) > 0.30 && (lKnee?.visibility ?? 0) > 0.30 && (lAnkle?.visibility ?? 0) > 0.30;
      const rightVis = (rHip?.visibility ?? 0) > 0.30 && (rKnee?.visibility ?? 0) > 0.30 && (rAnkle?.visibility ?? 0) > 0.30;
      const upperVis = (lShoulder?.visibility ?? 0) > 0.30 && (lHip?.visibility ?? 0) > 0.30;
      const headVis = (lm[0]?.visibility ?? 0) > 0.25 || (lm[2]?.visibility ?? 0) > 0.25 || (lShoulder?.visibility ?? 0) > 0.25 || (rShoulder?.visibility ?? 0) > 0.25;

      // Draw skeleton helper
      const drawLine = (p1: any, p2: any) => {
        if (!p1 || !p2 || (p1.visibility ?? 0) < 0.25 || (p2.visibility ?? 0) < 0.25) return;
        ctx.beginPath();
        ctx.moveTo(p1.x * canvas.width, p1.y * canvas.height);
        ctx.lineTo(p2.x * canvas.width, p2.y * canvas.height);
        ctx.stroke();
      };

      // Always draw visible landmarks and connections (Head, Eyes, Shoulders, Arms, Torso, Legs)
      ctx.lineWidth = 4;
      ctx.strokeStyle = '#38bdf8';

      // Face & head outline
      const nose = lm[0];
      const lEye = lm[2];
      const rEye = lm[5];
      const lElbow = lm[13];
      const rElbow = lm[14];
      const lWrist = lm[15];
      const rWrist = lm[16];

      drawLine(lEye, nose);
      drawLine(rEye, nose);
      drawLine(lEye, rEye);
      drawLine(lShoulder, rShoulder);
      drawLine(lShoulder, lElbow);
      drawLine(lElbow, lWrist);
      drawLine(rShoulder, rElbow);
      drawLine(rElbow, rWrist);
      drawLine(lShoulder, lHip);
      drawLine(rShoulder, rHip);
      drawLine(lHip, rHip);
      drawLine(lHip, lKnee);
      drawLine(lKnee, lAnkle);
      drawLine(rHip, rKnee);
      drawLine(rKnee, rAnkle);

      // Draw Keypoint circles for all active keypoints
      [nose, lEye, rEye, lShoulder, rShoulder, lElbow, rElbow, lWrist, rWrist, lHip, rHip, lKnee, rKnee, lAnkle, rAnkle].forEach((pt) => {
        if (pt && (pt.visibility ?? 0) > 0.25) {
          ctx.beginPath();
          ctx.arc(pt.x * canvas.width, pt.y * canvas.height, 5, 0, 2 * Math.PI);
          ctx.fillStyle = '#38bdf8';
          ctx.fill();
        }
      });

      if (!leftVis && !rightVis && !upperVis) {
        if (headVis) {
          setCurrentState('STANDBY');
          setCurrentKneeAngle(175);
          setCurrentTorsoAngle(12);
          setFormStatus('WAITING');
          setFormFaultReason(null);
          setFeedback('👤 Upper body tracked! Step back 1.5–2m so your hips and knees are visible to score squats, or click the desk test buttons below.');
        } else {
          setCurrentState('STANDBY');
          setCurrentKneeAngle(0);
          setCurrentTorsoAngle(0);
          setFormStatus('WAITING');
          setFormFaultReason('Step back so your body is visible');
          setFeedback('No trainee detected — step into camera view');
        }
        ctx.restore();
        return;
      }

      // Compute actual anatomical knee angle
      let angle = 175;
      if (leftVis && rightVis) {
        const lAngle = calculateAngle(lHip, lKnee, lAnkle);
        const rAngle = calculateAngle(rHip, rKnee, rAnkle);
        angle = Math.round((lAngle + rAngle) / 2);
      } else if (leftVis) {
        angle = calculateAngle(lHip, lKnee, lAnkle);
      } else if (rightVis) {
        angle = calculateAngle(rHip, rKnee, rAnkle);
      } else if (upperVis) {
        // Fallback if sitting/close to laptop camera: estimate depth from shoulder-to-hip vertical compression
        const torsoLen = Math.abs(lHip.y - lShoulder.y);
        angle = torsoLen < 0.25 ? 88 : torsoLen < 0.32 ? 125 : 172;
      }

      // Compute torso lean angle
      let torsoLean = 10;
      if (lShoulder && lHip && (lShoulder.visibility ?? 0) > 0.3 && (lHip.visibility ?? 0) > 0.3) {
        torsoLean = calculateTorsoLean(lShoulder, lHip);
      } else if (rShoulder && rHip && (rShoulder.visibility ?? 0) > 0.3 && (rHip.visibility ?? 0) > 0.3) {
        torsoLean = calculateTorsoLean(rShoulder, rHip);
      }

      // Detect knee valgus (knees caving inward relative to hips/ankles when both legs visible)
      let kneeValgus = false;
      if (leftVis && rightVis && angle < 145) {
        const hipDist = Math.abs(lHip.x - rHip.x);
        const kneeDist = Math.abs(lKnee.x - rKnee.x);
        if (hipDist > 0.08 && kneeDist < hipDist * 0.68) {
          kneeValgus = true;
        }
      }

      setCurrentKneeAngle(angle);
      setCurrentTorsoAngle(torsoLean);
      if (stateRef.current !== 'STANDBY' && stateRef.current !== 'READY') {
        maxTorsoInRepRef.current = Math.max(maxTorsoInRepRef.current, torsoLean);
      }

      // Determine instantaneous RIGHT vs WRONG posture
      let liveFault: string | null = null;
      if (torsoLean > 38) {
        liveFault = `Excessive Forward Back Lean (${torsoLean}° — Keep Chest Up!)`;
      } else if (kneeValgus) {
        liveFault = 'Knee Valgus Detected — Push Knees Outward Over Toes!';
      }

      if (liveFault) {
        setFormStatus('WRONG');
        setFormFaultReason(liveFault);
        hadFaultInRepRef.current = liveFault;
      } else {
        setFormStatus('RIGHT');
        setFormFaultReason(null);
      }

      // Biomechanical FSM State Machine
      const now = Date.now();
      const cur = stateRef.current;

      if (cur === 'STANDBY' || cur === 'READY') {
        if (angle >= 155) {
          stateRef.current = 'READY';
          setCurrentState('READY');
          minAngleInRepRef.current = 180;
          hadFaultInRepRef.current = null;
          if (!liveFault) {
            setFeedback('✓ RIGHT POSTURE — Stand tall, begin squat descent when ready');
          } else {
            setFeedback(`✗ WRONG FORM: ${liveFault}`);
          }
        } else if (angle < 148) {
          stateRef.current = 'DESCENDING';
          setCurrentState('DESCENDING');
          minAngleInRepRef.current = angle;
          setFeedback(liveFault ? `✗ WRONG FORM: ${liveFault}` : 'Descending — squat down to ≤ 95° parallel depth');
        }
      } else if (cur === 'DESCENDING') {
        if (angle < minAngleInRepRef.current) minAngleInRepRef.current = angle;
        if (angle <= 98) {
          stateRef.current = 'BOTTOM';
          setCurrentState('BOTTOM');
          setFeedback(
            liveFault
              ? `✗ Depth reached, but ${liveFault}`
              : '✓ PARALLEL DEPTH ACHIEVED (RIGHT FORM)! Drive up through heels'
          );
        } else if (angle > 155) {
          // Went down partially and came back up without reaching parallel -> WRONG / SHALLOW REP!
          stateRef.current = 'READY';
          setCurrentState('READY');
          wrongRepCountRef.current += 1;
          setWrongRepCount(wrongRepCountRef.current);
          const shallowMsg = `Shallow Squat (stopped at ${minAngleInRepRef.current}°, need ≤ 98° parallel)`;
          setFormStatus('WRONG');
          setFormFaultReason(shallowMsg);
          setFeedback(`✗ WRONG REP: ${shallowMsg}`);
          speak('Shallow squat. Go deeper.');
        }
      } else if (cur === 'BOTTOM') {
        if (angle < minAngleInRepRef.current) minAngleInRepRef.current = angle;
        if (angle > 115) {
          stateRef.current = 'ASCENDING';
          setCurrentState('ASCENDING');
          setFeedback('Ascending — lock out hips and knees at the top');
        }
      } else if (cur === 'ASCENDING') {
        if (angle >= 155) {
          if (now - lastRepTimeRef.current >= 700) {
            lastRepTimeRef.current = now;
            if (hadFaultInRepRef.current) {
              wrongRepCountRef.current += 1;
              setWrongRepCount(wrongRepCountRef.current);
              setFormStatus('WRONG');
              setFormFaultReason(hadFaultInRepRef.current);
              setFeedback(`✗ WRONG REP: ${hadFaultInRepRef.current}`);
              repTelemetryRef.current.push({
                rep_number: repCountRef.current + wrongRepCountRef.current,
                verdict: 'incorrect',
                min_knee_angle_deg: minAngleInRepRef.current,
                max_trunk_lean_deg: maxTorsoInRepRef.current || torsoLean,
                detected_errors: [hadFaultInRepRef.current],
                confidence: 0.9,
              });
              speak('Form fault detected. Keep your chest up.');
            } else {
              repCountRef.current += 1;
              const newRep = repCountRef.current;
              setRepCount(newRep);
              setFormStatus('RIGHT');
              setFormFaultReason(null);
              repTelemetryRef.current.push({
                rep_number: repCountRef.current + wrongRepCountRef.current,
                verdict: 'correct',
                min_knee_angle_deg: minAngleInRepRef.current,
                max_trunk_lean_deg: maxTorsoInRepRef.current || torsoLean,
                detected_errors: [],
                confidence: 0.92,
              });
              speak(`Rep ${newRep}, good form`);
              setFeedback(`✓ REP ${newRep} VALIDATED — Right Form & Parallel Depth (${minAngleInRepRef.current}°)`);
            }
          }
          stateRef.current = 'READY';
          setCurrentState('READY');
        }
      }

      ctx.restore();
    },
    [speak, cameraSource, brokerSession.status]
  );

  // Initialize MediaPipe Pose Model
  const initPoseModel = useCallback(() => {
    if (typeof window === 'undefined' || poseInstanceRef.current) return;
    if (!window.Pose) {
      setModelError('Pose model is not loaded. Check the network connection and reload the workout page.');
      return;
    }
    try {
      const pose = new window.Pose({
        locateFile: (file: string) => `https://cdn.jsdelivr.net/npm/@mediapipe/pose/${file}`,
      });
      pose.setOptions({
        modelComplexity: 1,
        smoothLandmarks: true,
        enableSegmentation: false,
        smoothSegmentation: false,
        minDetectionConfidence: 0.5,
        minTrackingConfidence: 0.5,
      });

      pose.onResults(onPoseResults);
      poseInstanceRef.current = pose;
      setModelError(null);
      setModelLoaded(true);
    } catch (e) {
      console.warn('Failed initializing in-browser MediaPipe model:', e);
      setModelError('Pose model failed to initialize in this browser.');
    }
  }, [onPoseResults]);

  const stopLocalCamera = useCallback(() => {
    if (localStreamRef.current) {
      for (const track of localStreamRef.current.getTracks()) {
        try {
          track.stop();
        } catch {
          // ignore error stopping individual track
        }
      }
      localStreamRef.current = null;
    }
    if (videoRef.current) {
      videoRef.current.srcObject = null;
    }
    setLocalCameraActive(false);
  }, []);

  const startLocalCamera = useCallback(async (requestedFacingMode = facingMode) => {
    if (typeof window === 'undefined') return;
    const h = window.location.hostname || 'localhost';

    if (!window.isSecureContext && h !== 'localhost' && h !== '127.0.0.1') {
      throw new Error(`🔒 Insecure Connection: Mobile browsers strictly require HTTPS to access the camera over LAN. Open https://${h}:3001/cert to install the certificate.`);
    }
    if (!navigator.mediaDevices?.getUserMedia) {
      throw new Error('🚫 Camera API Unavailable: This mobile browser does not expose camera devices. Please use mobile Safari (iOS) or Chrome (Android) over HTTPS.');
    }

    stopLocalCamera();

    let stream: MediaStream | null = null;
    try {
      stream = await navigator.mediaDevices.getUserMedia({
        audio: false,
        video: {
          facingMode: { ideal: requestedFacingMode },
          width: { ideal: 1280 },
          height: { ideal: 720 },
        },
      });
    } catch (err: any) {
      // If constraint fails (e.g. device without requested facing mode or resolution), fall back to basic video
      if (err?.name === 'OverconstrainedError' || err?.name === 'ConstraintNotSatisfiedError') {
        try {
          stream = await navigator.mediaDevices.getUserMedia({ audio: false, video: true });
        } catch (fallbackErr: any) {
          throw new Error(parseCameraError(fallbackErr, h));
        }
      } else {
        throw new Error(parseCameraError(err, h));
      }
    }

    localStreamRef.current = stream;
    const video = videoRef.current;
    if (!video) {
      for (const track of stream.getTracks()) {
        try {
          track.stop();
        } catch {}
      }
      localStreamRef.current = null;
      throw new Error('Camera preview element not ready. Please tap Start Camera again.');
    }

    // Critical for iOS Safari: playsinline prevents automatic fullscreen
    video.setAttribute('playsinline', 'true');
    video.setAttribute('webkit-playsinline', 'true');
    video.srcObject = stream;
    try {
      await video.play();
    } catch {
      // Autoplay might need a user gesture on some mobile browsers
    }
    setLocalCameraActive(true);
    setStreamActive(true);
  }, [facingMode, stopLocalCamera]);

  // Start either the phone's own camera or the shared fixed-camera broker.
  // They are completely independent: phone camera does not depend on Edge AI.
  const startCamera = useCallback(async () => {
    setCameraError(null);
    try {
      if (cameraSource === 'phone') {
        if (isBrokerRunning) await brokerSession.stopCamera();
        await startLocalCamera();
      } else {
        stopLocalCamera();
        if (brokerSession.status !== 'running') {
          await brokerSession.startCamera();
        }
        setStreamActive(true);
      }
    } catch (err: any) {
      console.error('Camera access failed:', err);
      const h = typeof window !== 'undefined' ? window.location.hostname : 'localhost';
      if (cameraSource === 'phone') {
        const msg = err?.message || parseCameraError(err, h);
        setCameraError(msg);
      } else {
        const msg = err?.message || 'Edge AI camera service offline';
        let detail = `Central Camera Broker unavailable (${msg}). Ensure Edge AI is running on port 8088.`;
        if (typeof window !== 'undefined' && window.location.protocol === 'https:') {
          detail += ' Note: Over HTTPS, browser blocks direct HTTP to port 8088 (Mixed Content). Use "Phone Camera" mode for mobile devices.';
        }
        setCameraError(detail);
      }
      setStreamActive(false);
      setFeedback('Camera connection standby. Click "تشغيل الكاميرا / Start Camera" to connect.');
    }
  }, [cameraSource, isBrokerRunning, brokerSession, startLocalCamera, stopLocalCamera]);

  const stopCamera = useCallback(async () => {
    stopLocalCamera();
    if (cameraSource === 'broker' && (brokerSession.status === 'running' || brokerSession.status === 'starting')) {
      await brokerSession.stopCamera();
    }
    setStreamActive(false);
  }, [cameraSource, brokerSession, stopLocalCamera]);

  const selectCameraSource = useCallback(
    async (nextSource: 'phone' | 'broker') => {
      if (nextSource === cameraSource) return;
      await stopCamera();
      setCameraError(null);
      setCameraSource(nextSource);
      setPoseFrameCount(0);
    },
    [cameraSource, stopCamera]
  );

  useEffect(() => {
    if (typeof window !== 'undefined' && window.Pose) {
      initPoseModel();
    }
  }, [initPoseModel]);

  useEffect(() => {
    return () => {
      if (rafRef.current) window.clearTimeout(rafRef.current);
      stopLocalCamera();
      // The broker endpoint is idempotent; calling it on unmount also covers
      // a start request that is still in flight.
      void brokerSession.stopCamera();
    };
  }, [brokerSession.stopCamera, stopLocalCamera]); // Camera access starts only after an explicit user gesture.

  // Continuous frame pump into MediaPipe Pose whenever stream + model are active
  useEffect(() => {
    if (!modelLoaded) return;
    let cancelled = false;
    const pump = async () => {
      if (cancelled) return;
      if (poseInstanceRef.current) {
        try {
          if (
            cameraSource === 'broker' &&
            brokerSession.status === 'running' &&
            brokerImgRef.current &&
            brokerImgRef.current.complete &&
            brokerImgRef.current.naturalWidth > 0
          ) {
            await poseInstanceRef.current.send({ image: brokerImgRef.current });
            setStreamActive(true);
          } else if (videoRef.current && videoRef.current.readyState >= 2) {
            await poseInstanceRef.current.send({ image: videoRef.current });
            setStreamActive(true);
          }
          setPoseFrameCount((count) => count + 1);
        } catch {
          // Keep the camera alive; the visible status badge will remain in
          // "waiting" until a frame is actually accepted by the model.
        }
      }
      if (!cancelled) {
        rafRef.current = window.setTimeout(pump, 80);
      }
    };
    void pump();
    return () => {
      cancelled = true;
      if (rafRef.current) window.clearTimeout(rafRef.current);
    };
  }, [modelLoaded, cameraSource, brokerSession.status]);

  const flipCamera = async () => {
    const next = facingMode === 'user' ? 'environment' : 'user';
    setFacingMode(next);
    if (cameraSource === 'phone' && localCameraActive) {
      try {
        setCameraError(null);
        await startLocalCamera(next);
      } catch (err) {
        setCameraError((err as Error)?.message || 'Could not switch the phone camera.');
      }
    }
  };

  // Interactive simulation helpers so the user can also test RIGHT vs WRONG form on a laptop desk
  const triggerTestRep = (type: 'right' | 'wrong_shallow' | 'wrong_lean') => {
    if (!isExercising) {
      setIsExercising(true);
      startTimeRef.current = Date.now();
    }
    if (type === 'right') {
      repCountRef.current += 1;
      const n = repCountRef.current;
      setRepCount(n);
      setCurrentKneeAngle(88);
      setCurrentTorsoAngle(16);
      setCurrentState('BOTTOM');
      setFormStatus('RIGHT');
      setFormFaultReason(null);
      repTelemetryRef.current.push({
        rep_number: repCountRef.current + wrongRepCountRef.current,
        verdict: 'correct',
        min_knee_angle_deg: 88,
        max_trunk_lean_deg: 16,
        detected_errors: [],
        confidence: 0.95,
      });
      setFeedback(`✓ REP ${n} VALIDATED — Right Form: Parallel Depth (88°) & Upright Back (16°)`);
      speak(`Rep ${n}, right form.`);
    } else if (type === 'wrong_shallow') {
      wrongRepCountRef.current += 1;
      setWrongRepCount(wrongRepCountRef.current);
      setCurrentKneeAngle(124);
      setCurrentTorsoAngle(18);
      setCurrentState('DESCENDING');
      setFormStatus('WRONG');
      const reason = 'Shallow Squat (124° knee angle — must reach ≤ 98° parallel)';
      setFormFaultReason(reason);
      repTelemetryRef.current.push({
        rep_number: repCountRef.current + wrongRepCountRef.current,
        verdict: 'incorrect',
        min_knee_angle_deg: 124,
        max_trunk_lean_deg: 18,
        detected_errors: ['shallow_squat'],
        confidence: 0.9,
      });
      setFeedback(`✗ WRONG FORM: ${reason}`);
      speak('Wrong form. Squat deeper to reach parallel.');
    } else {
      wrongRepCountRef.current += 1;
      setWrongRepCount(wrongRepCountRef.current);
      setCurrentKneeAngle(94);
      setCurrentTorsoAngle(46);
      setCurrentState('BOTTOM');
      setFormStatus('WRONG');
      const reason = 'Excessive Forward Back Lean (46° — keep chest upright < 38°)';
      setFormFaultReason(reason);
      repTelemetryRef.current.push({
        rep_number: repCountRef.current + wrongRepCountRef.current,
        verdict: 'incorrect',
        min_knee_angle_deg: 94,
        max_trunk_lean_deg: 46,
        detected_errors: ['excessive_trunk_lean'],
        confidence: 0.9,
      });
      setFeedback(`✗ WRONG FORM: ${reason}`);
      speak('Wrong form. Keep your chest up and back straight.');
    }
  };

  const handleStartWorkout = () => {
    setIsExercising(true);
    setWorkoutFinished(false);
    setSavedSessionId(null);
    setSaveError(null);
    setRepCount(0);
    setWrongRepCount(0);
    repCountRef.current = 0;
    wrongRepCountRef.current = 0;
    repTelemetryRef.current = [];
    stateRef.current = 'READY';
    setCurrentState('READY');
    startTimeRef.current = Date.now();
    lastRepTimeRef.current = 0;
    setFeedback('Camera tracking active. Perform squats in view to see RIGHT vs WRONG form.');
    speak('Starting AI trainer camera. Perform squats in view.');
  };

  const handleFinishWorkout = async () => {
    setIsExercising(false);
    setWorkoutFinished(true);
    setSaveError(null);
    const totalAttempts = repCountRef.current + wrongRepCountRef.current;
    const score = totalAttempts > 0 ? Math.round((repCountRef.current / totalAttempts) * 100) : 0;
    speak(`Set complete. Recorded ${repCountRef.current} valid repetitions.`);

    const durationSec = Math.max(1, Math.round((Date.now() - startTimeRef.current) / 1000));
    setSaving(true);
    try {
      const res = await savePhoneCameraWorkoutSession({
        reps: totalAttempts,
        validReps: repCountRef.current,
        imperfectReps: wrongRepCountRef.current,
        exercise: 'squat',
        durationSeconds: durationSec,
        formScore: score,
        repTelemetry: repTelemetryRef.current,
      });
      if (res.ok && res.data) {
        setSavedSessionId(res.data.sessionId);
      } else if (res.error) {
        setSaveError(res.error);
      }
    } catch (e) {
      console.error('Failed to save session:', e);
      setSaveError((e as Error)?.message ?? 'Failed to save workout session');
    } finally {
      setSaving(false);
    }
  };

  return (
    <div className="space-y-4">
      {/* Load MediaPipe Pose Script */}
      <Script
        src="https://cdn.jsdelivr.net/npm/@mediapipe/pose/pose.js"
        strategy="afterInteractive"
        onLoad={() => initPoseModel()}
        onError={() => setModelError('Could not download the pose model. Check network access, then reload this page.')}
      />

      {/* Top Header Bar */}
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-xl font-bold tracking-tight flex items-center gap-2">
            <Sparkles className="h-5 w-5 text-primary" /> AI Personal Trainer Camera
          </h1>
          <p className="text-xs text-muted-foreground">
            {memberName} · Live Pose Skeleton &amp; Right / Wrong Form Verdict
          </p>
        </div>
        <div className="flex items-center gap-2">
          <Button
            type="button"
            size="sm"
            variant="outline"
            onClick={() => setVoiceEnabled(!voiceEnabled)}
            title={voiceEnabled ? 'Mute voice audio' : 'Enable voice coaching'}
          >
            {voiceEnabled ? <Volume2 className="h-4 w-4 text-primary" /> : <VolumeX className="h-4 w-4 text-muted-foreground" />}
          </Button>
          <Button type="button" size="sm" variant="outline" onClick={flipCamera} title="Switch camera">
            <RotateCcw className="h-4 w-4" />
          </Button>
        </div>
      </div>

      {/* Prominent Live RIGHT / WRONG Form Verdict Banner */}
      <div
        className={`rounded-xl border p-3.5 flex items-center justify-between gap-3 transition-colors ${
          formStatus === 'RIGHT'
            ? 'border-emerald-500/50 bg-emerald-500/10 text-emerald-300'
            : formStatus === 'WRONG'
            ? 'border-red-500/60 bg-red-500/15 text-red-300'
            : 'border-border bg-card text-muted-foreground'
        }`}
      >
        <div className="flex items-center gap-2.5">
          {formStatus === 'RIGHT' ? (
            <CheckCircle2 className="h-6 w-6 text-emerald-400 shrink-0" />
          ) : formStatus === 'WRONG' ? (
            <XCircle className="h-6 w-6 text-red-400 shrink-0" />
          ) : (
            <AlertTriangle className="h-6 w-6 text-amber-400 shrink-0" />
          )}
          <div>
            <p className="text-xs font-bold uppercase tracking-wider">
              {formStatus === 'RIGHT'
                ? '✓ RIGHT EXERCISE FORM'
                : formStatus === 'WRONG'
                ? '✗ WRONG EXERCISE FORM DETECTED'
                : 'LIVE AI FORM EVALUATOR READY'}
            </p>
            <p className="text-sm font-medium text-foreground mt-0.5">
              {formFaultReason ? formFaultReason : feedback}
            </p>
          </div>
        </div>
        <Badge
          variant={formStatus === 'RIGHT' ? 'default' : formStatus === 'WRONG' ? 'destructive' : 'outline'}
          className="shrink-0 text-xs"
        >
          {modelLoaded ? 'Pose AI Active' : 'Camera Active'}
        </Badge>
      </div>

      {/* Camera source and truthful model status */}
      <div className="flex flex-wrap items-center justify-between gap-2 p-2.5 rounded-xl border border-border bg-card/60">
        <div className="flex flex-wrap items-center gap-2">
          <Badge
            variant={isCameraRunning ? 'success' : cameraError ? 'destructive' : 'outline'}
            className="text-[11px] uppercase tracking-wider font-medium"
          >
            <span className={`h-2 w-2 rounded-full mr-1.5 ${isCameraRunning ? 'bg-emerald-400 animate-pulse' : 'bg-zinc-500'}`} />
            Camera: {isCameraRunning ? 'Connected' : cameraSource === 'phone' ? 'Tap Start' : brokerSession.status}
          </Badge>

          <Badge variant={detectionActive ? 'success' : modelLoaded ? 'warning' : 'outline'} className="text-[11px] uppercase tracking-wider font-medium">
            Detection: {detectionActive ? 'Active' : modelLoaded ? 'Waiting for frames' : 'Model offline'}
          </Badge>

          <Badge variant="outline" className="text-[11px] uppercase tracking-wider font-medium bg-blue-950/30 text-blue-300 border-blue-800/40">
            Pose model: {modelLoaded ? 'Ready' : 'Offline'}
          </Badge>

          {cameraSource === 'broker' ? (
            <Badge variant="outline" className="text-[11px] uppercase tracking-wider font-medium text-zinc-300 border-zinc-700">
              Consumers: {brokerSession.activeConsumers}
            </Badge>
          ) : (
            <Badge variant="outline" className="text-[11px] uppercase tracking-wider font-medium text-zinc-300 border-zinc-700">
              Lens: {facingMode === 'user' ? 'Front Selfie' : 'Rear Environment'}
            </Badge>
          )}
        </div>

        {/* Explicit source selection prevents a phone from accidentally trying
            to open the fixed Windows webcam through the broker. */}
        <div className="flex flex-wrap items-center gap-1.5">
          <Button
            type="button"
            size="sm"
            variant={cameraSource === 'phone' ? 'default' : 'outline'}
            onClick={() => void selectCameraSource('phone')}
            className="h-7 text-xs gap-1"
          >
            <Camera className="h-3 w-3" /> Phone Camera
          </Button>
          <Button
            type="button"
            size="sm"
            variant={cameraSource === 'broker' ? 'default' : 'outline'}
            onClick={() => void selectCameraSource('broker')}
            className="h-7 text-xs gap-1"
          >
            <Activity className="h-3 w-3" /> Central Camera
          </Button>
          {!isCameraRunning ? (
            <Button
              type="button"
              size="sm"
              onClick={() => void startCamera()}
              className="h-7 text-xs bg-emerald-600 hover:bg-emerald-500 text-white gap-1"
            >
              <Power className="h-3 w-3" /> تشغيل الكاميرا / Start Camera
            </Button>
          ) : (
            <Button
              type="button"
              size="sm"
              variant="destructive"
              onClick={() => void stopCamera()}
              className="h-7 text-xs gap-1"
            >
              <Power className="h-3 w-3" /> إيقاف الكاميرا / Stop Camera
            </Button>
          )}
          <Button
            type="button"
            size="sm"
            variant="outline"
            onClick={() => (cameraSource === 'broker' ? void brokerSession.reconnect() : void startCamera())}
            className="h-7 text-xs gap-1 border-border text-foreground hover:bg-accent"
          >
            <RefreshCw className="h-3 w-3" /> إعادة الاتصال / Reconnect
          </Button>
        </div>
      </div>

      {(cameraError || modelError) && (
        <div className="rounded-xl border border-red-500/40 bg-red-950/40 p-3.5 text-xs text-red-200 flex flex-col sm:flex-row sm:items-center justify-between gap-3 shadow-md">
          <div className="flex items-start gap-2">
            <AlertTriangle className="h-4 w-4 text-red-400 shrink-0 mt-0.5" />
            <span className="leading-relaxed">{cameraError || modelError}</span>
          </div>
          {(cameraError?.includes('HTTPS') || cameraError?.includes('Insecure') || cameraError?.includes('cert') || cameraError?.includes('Permission')) && (
            <Link href="/cert" className="shrink-0">
              <Button size="sm" variant="outline" className="h-7 text-xs border-red-400/60 text-red-200 hover:bg-red-500/20 whitespace-nowrap">
                Install SSL Cert &amp; Test Handset
              </Button>
            </Link>
          )}
        </div>
      )}

      {/* Main Camera Viewport */}
      <div className="relative aspect-[3/4] sm:aspect-video w-full overflow-hidden rounded-2xl bg-black border border-border shadow-lg flex items-center justify-center">
        {!streamActive && !isCameraRunning && (
          <div className="absolute inset-0 z-20 flex flex-col items-center justify-center gap-3 bg-black/90 p-4 text-center text-muted-foreground">
            {cameraError ? (
              <>
                <Camera className="h-8 w-8 text-red-400" />
                <p className="text-xs text-red-300 max-w-xs">{cameraError}</p>
                <Button size="sm" onClick={() => void startCamera()}>
                  تشغيل الكاميرا / Start Camera
                </Button>
              </>
            ) : (
              <>
                <Loader2 className="h-8 w-8 animate-spin text-primary" />
                <p className="text-xs">Opening live camera stream…</p>
              </>
            )}
          </div>
        )}

        {cameraSource === 'broker' && isBrokerRunning ? (
          <img
            ref={brokerImgRef}
            crossOrigin="anonymous"
            src={brokerSession.streamUrl}
            alt="Central Camera Broker Stream"
            className="h-full w-full object-cover select-none"
            onError={() => {
              // fallback or reconnect
            }}
          />
        ) : (
          <video
            ref={videoRef}
            playsInline
            muted
            autoPlay
            className={`h-full w-full object-cover ${facingMode === 'user' ? '-scale-x-100' : ''}`}
          />
        )}
        <canvas
          ref={canvasRef}
          className={`absolute inset-0 h-full w-full pointer-events-none ${facingMode === 'user' && cameraSource === 'phone' ? '-scale-x-100' : ''}`}
        />

        {/* HUD Overlay: Real Rep Counter (Right vs Wrong) */}
        <div className="absolute top-4 left-4 z-10 flex gap-2">
          <div className="flex flex-col items-start bg-black/70 backdrop-blur-md px-3.5 py-2 rounded-xl border border-emerald-500/30 text-white">
            <span className="text-[10px] font-semibold uppercase tracking-wider text-emerald-400">✓ Right Reps</span>
            <span className="text-3xl font-black tabular-nums text-emerald-400">{repCount}</span>
          </div>
          <div className="flex flex-col items-start bg-black/70 backdrop-blur-md px-3.5 py-2 rounded-xl border border-red-500/30 text-white">
            <span className="text-[10px] font-semibold uppercase tracking-wider text-red-400">✗ Wrong Reps</span>
            <span className="text-3xl font-black tabular-nums text-red-400">{wrongRepCount}</span>
          </div>
        </div>

        {/* HUD Overlay: Real Anatomical Angles */}
        <div className="absolute top-4 right-4 z-10 flex flex-col items-end gap-1.5 bg-black/70 backdrop-blur-md px-3.5 py-2 rounded-xl border border-white/10 text-white">
          <div className="text-right">
            <span className="text-[10px] font-semibold uppercase tracking-wider text-muted-foreground block">Knee Angle</span>
            <span className="text-lg font-bold tabular-nums text-emerald-400">
              {currentKneeAngle > 0 ? `${currentKneeAngle}°` : '—'}
            </span>
          </div>
          <div className="text-right border-t border-white/10 pt-1">
            <span className="text-[10px] font-semibold uppercase tracking-wider text-muted-foreground block">Back Lean</span>
            <span className={`text-sm font-bold tabular-nums ${currentTorsoAngle > 38 ? 'text-red-400' : 'text-sky-400'}`}>
              {currentTorsoAngle > 0 ? `${currentTorsoAngle}°` : '—'}
            </span>
          </div>
        </div>

        {/* HUD Overlay: Biomechanical State Pill */}
        <div className="absolute bottom-4 inset-x-4 z-10 flex flex-col items-center gap-1.5">
          <div className="bg-black/80 backdrop-blur-md px-4 py-2 rounded-full border border-white/15 text-center shadow-lg max-w-md">
            <p className="text-xs font-semibold text-white">{feedback}</p>
          </div>
          <Badge
            variant={currentState === 'BOTTOM' ? 'default' : 'outline'}
            className="text-[10px] uppercase font-mono tracking-wider bg-black/80 text-white border-white/20"
          >
            FSM: {currentState}
          </Badge>
        </div>
      </div>

      {/* Workout Control Buttons */}
      <div className="flex gap-3">
        {!isExercising ? (
          <Button onClick={handleStartWorkout} className="w-full gap-2 text-base h-12 bg-primary hover:bg-primary/90">
            <Play className="h-5 w-5 fill-current" />
            {workoutFinished ? 'Start Another Squat Set' : 'Start AI Squat Set'}
          </Button>
        ) : (
          <Button onClick={handleFinishWorkout} variant="destructive" className="w-full gap-2 text-base h-12">
            <Square className="h-5 w-5 fill-current" />
            Finish &amp; Save Set ({repCount} Valid Reps)
          </Button>
        )}
      </div>

      {/* Interactive Desk Calibration / Form Verdict Tester */}
      <Card className="border-border bg-muted/20">
        <CardHeader className="py-3 px-4">
          <CardTitle className="text-xs font-semibold uppercase tracking-wider text-muted-foreground">
            Biomechanical Form Rules &amp; Desk Verification Controls
          </CardTitle>
        </CardHeader>
        <CardContent className="px-4 pb-4 space-y-3 text-xs">
          <p className="text-muted-foreground">
            Stand in full view of your camera for automatic skeleton tracking, or click a biomechanics test case below if seated at a laptop desk to verify how the AI scores right vs wrong repetitions:
          </p>
          <div className="grid grid-cols-1 sm:grid-cols-3 gap-2">
            <Button
              type="button"
              size="sm"
              variant="outline"
              className="border-emerald-500/40 text-emerald-400 hover:bg-emerald-500/10"
              onClick={() => triggerTestRep('right')}
            >
              ✓ Test Right Rep (88° Parallel)
            </Button>
            <Button
              type="button"
              size="sm"
              variant="outline"
              className="border-amber-500/40 text-amber-400 hover:bg-amber-500/10"
              onClick={() => triggerTestRep('wrong_shallow')}
            >
              ✗ Test Wrong: Shallow (124°)
            </Button>
            <Button
              type="button"
              size="sm"
              variant="outline"
              className="border-red-500/40 text-red-400 hover:bg-red-500/10"
              onClick={() => triggerTestRep('wrong_lean')}
            >
              ✗ Test Wrong: Back Lean (46°)
            </Button>
          </div>
        </CardContent>
      </Card>

      {/* Completion Modal / Summary Card */}
      {workoutFinished && (
        <Card className={saveError ? "border-red-500/40 bg-red-500/10" : "border-emerald-500/30 bg-emerald-500/5"}>
          <CardHeader className="pb-2">
            <div className={`flex items-center gap-2 ${saveError ? "text-red-400" : "text-emerald-400"}`}>
              {saveError ? <AlertTriangle className="h-5 w-5" /> : <CheckCircle2 className="h-5 w-5" />}
              <CardTitle className="text-base text-foreground">
                {saveError ? 'Session Recorded Locally (Sync Error)' : 'Set Recorded to Member Profile'}
              </CardTitle>
            </div>
          </CardHeader>
          <CardContent className="space-y-3 text-sm">
            <div className="grid grid-cols-3 gap-2 text-center pt-1">
              <div className="rounded-lg bg-card/60 p-2.5 border border-border">
                <p className="text-xs text-muted-foreground">✓ Right Reps</p>
                <p className="text-xl font-bold text-emerald-400">{repCount}</p>
              </div>
              <div className="rounded-lg bg-card/60 p-2.5 border border-border">
                <p className="text-xs text-muted-foreground">✗ Wrong Reps</p>
                <p className="text-xl font-bold text-red-400">{wrongRepCount}</p>
              </div>
              <div className="rounded-lg bg-card/60 p-2.5 border border-border">
                <p className="text-xs text-muted-foreground">Technique Score</p>
                <p className="text-xl font-bold text-foreground">
                  {repCount + wrongRepCount > 0
                    ? `${Math.round((repCount / (repCount + wrongRepCount)) * 100)}%`
                    : '0%'}
                </p>
              </div>
            </div>

            {saveError ? (
              <div className="rounded-lg border border-red-500/30 bg-red-950/40 p-3 text-xs text-red-200 space-y-1.5">
                <p className="font-semibold text-red-300">
                  {saveError.includes('AI_ENTITLEMENT_REQUIRED')
                    ? 'AI Personal Trainer Addon Required'
                    : 'Database Persistence Error'}
                </p>
                <p>
                  {saveError.includes('AI_ENTITLEMENT_REQUIRED')
                    ? 'Your current gym membership plan does not have the AI Personal Trainer feature enabled. Please upgrade your plan or ask front desk staff to activate AI workouts on your subscription.'
                    : saveError}
                </p>
                <div className="pt-1 flex gap-2">
                  <Button
                    type="button"
                    size="sm"
                    variant="outline"
                    onClick={handleFinishWorkout}
                    disabled={saving}
                    className="h-7 text-xs border-red-500/50 text-red-200 hover:bg-red-500/20"
                  >
                    {saving ? 'Retrying…' : 'Retry Database Sync'}
                  </Button>
                  <Link href="/membership">
                    <Button size="sm" className="h-7 text-xs bg-red-600 hover:bg-red-500 text-white">
                      View Membership Plans
                    </Button>
                  </Link>
                </div>
              </div>
            ) : (
              <p className="text-xs text-muted-foreground">
                {saving
                  ? 'Syncing workout record with central gym database…'
                  : savedSessionId
                  ? 'Session saved to Supabase Cloud! Review your workout report in your history.'
                  : 'Session completed.'}
              </p>
            )}

            <div className="flex gap-2 pt-1">
              <Link href="/dashboard" className="w-full">
                <Button variant="outline" size="sm" className="w-full">
                  Dashboard
                </Button>
              </Link>
              <Link href="/history" className="w-full">
                <Button size="sm" className="w-full">
                  Workout History
                </Button>
              </Link>
            </div>
          </CardContent>
        </Card>
      )}
    </div>
  );
}
