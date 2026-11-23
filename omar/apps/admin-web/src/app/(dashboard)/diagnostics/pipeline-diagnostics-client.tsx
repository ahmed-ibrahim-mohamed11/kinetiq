'use client';

import { useEffect, useRef, useState } from 'react';
import { Badge, Button, Card, CardContent, CardHeader, CardTitle, CameraSessionView, useCameraSession } from '@smartgym/ui';
import { Camera, RotateCcw, AlertTriangle, CheckCircle2 } from 'lucide-react';

interface SimulatedPerson {
  type: 'trainee' | 'seated_operator' | 'passerby';
  kneeAngle: number;
  valgus: boolean;
  state: 'STANDING_READY' | 'DESCENDING' | 'BOTTOM_CONFIRMED' | 'ASCENDING' | 'REP_COMPLETED' | 'PAUSED';
  reps: number;
}

export function PipelineDiagnosticsClient() {
  const [traineeMode, setTraineeMode] = useState<'trainee' | 'seated_operator' | 'passerby'>('trainee');
  const canvasRef = useRef<HTMLCanvasElement | null>(null);

  // Biomechanical State
  const [kneeAngle, setKneeAngle] = useState(175);
  const [biomechState, setBiomechState] = useState<SimulatedPerson['state']>('STANDING_READY');
  const [repCount, setRepCount] = useState(0);
  const [fault, setFault] = useState<'VALID' | 'SHALLOW' | 'VALGUS' | 'FILTERED'>('VALID');

  useEffect(() => {
    let frameId: number;
    let t = 0;
    let localReps = 0;
    let lastRepT = 0;

    const canvas = canvasRef.current;
    if (!canvas) return;
    const ctx = canvas.getContext('2d');
    if (!ctx) return;

    const render = () => {
      t += 0.035;

      const w = canvas.width;
      const h = canvas.height;

      ctx.fillStyle = '#0f172a';
      ctx.fillRect(0, 0, w, h);

      ctx.strokeStyle = 'rgba(255, 255, 255, 0.05)';
      ctx.lineWidth = 1;
      for (let x = 0; x < w; x += 40) {
        ctx.beginPath();
        ctx.moveTo(x, 0);
        ctx.lineTo(x, h);
        ctx.stroke();
      }
      for (let y = 0; y < h; y += 40) {
        ctx.beginPath();
        ctx.moveTo(0, y);
        ctx.lineTo(w, y);
        ctx.stroke();
      }

      ctx.fillStyle = 'rgba(255, 255, 255, 0.4)';
      ctx.font = '10px monospace';
      ctx.fillText('DIAGNOSTICS LAB · 6-STATE FSM HARNESS · CALIBRATION TOOL', 15, 20);
      ctx.fillText(`TIMESTAMP: ${new Date().toISOString()}`, 15, 34);

      if (traineeMode === 'trainee') {
        const cycle = (Math.sin(t) + 1) / 2;
        const currentAngle = Math.round(175 - cycle * 90);
        setKneeAngle(currentAngle);

        let curState: SimulatedPerson['state'] = 'STANDING_READY';
        let curFault: 'VALID' | 'SHALLOW' | 'VALGUS' | 'FILTERED' = 'VALID';

        if (currentAngle < 155 && currentAngle > 100) {
          curState = 'DESCENDING';
        } else if (currentAngle <= 100) {
          curState = 'BOTTOM_CONFIRMED';
          curFault = currentAngle <= 92 ? 'VALID' : 'SHALLOW';
        } else if (currentAngle >= 155) {
          if (t - lastRepT > 2.0 && cycle < 0.1) {
            localReps += 1;
            lastRepT = t;
            setRepCount(localReps);
          }
          curState = 'STANDING_READY';
        }
        setBiomechState(curState);
        setFault(curFault);

        const bx = w * 0.35;
        const by = h * 0.15 + cycle * 40;
        const bw = w * 0.3;
        const bh = h * 0.7 - cycle * 40;

        ctx.strokeStyle = '#10b981';
        ctx.lineWidth = 2;
        ctx.strokeRect(bx, by, bw, bh);

        ctx.fillStyle = '#10b981';
        ctx.fillRect(bx, by - 22, 160, 20);
        ctx.fillStyle = '#ffffff';
        ctx.font = 'bold 10px sans-serif';
        ctx.fillText('TRK#01 · ACTIVE SQUAT TRAINEE', bx + 6, by - 8);

        const headX = bx + bw / 2;
        const headY = by + 25;
        const hipX = bx + bw / 2;
        const hipY = by + bh * 0.45;
        const kneeX = bx + bw * 0.45;
        const kneeY = by + bh * 0.72;
        const ankleX = bx + bw * 0.45;
        const ankleY = by + bh - 10;

        ctx.strokeStyle = '#38bdf8';
        ctx.lineWidth = 3;
        ctx.beginPath();
        ctx.moveTo(headX, headY);
        ctx.lineTo(hipX, hipY);
        ctx.lineTo(kneeX, kneeY);
        ctx.lineTo(ankleX, ankleY);
        ctx.stroke();

        const pts: [number, number][] = [
          [headX, headY],
          [hipX, hipY],
          [kneeX, kneeY],
          [ankleX, ankleY],
        ];
        ctx.fillStyle = '#fbbf24';
        pts.forEach(([px, py]) => {
          ctx.beginPath();
          ctx.arc(px, py, 5, 0, Math.PI * 2);
          ctx.fill();
        });

        ctx.fillStyle = '#10b981';
        ctx.font = 'bold 12px monospace';
        ctx.fillText(`Knee: ${currentAngle}°`, kneeX + 15, kneeY);
      } else if (traineeMode === 'seated_operator') {
        setKneeAngle(90);
        setBiomechState('PAUSED');
        setFault('FILTERED');

        const bx = w * 0.4;
        const by = h * 0.35;
        const bw = w * 0.25;
        const bh = h * 0.5;

        ctx.strokeStyle = '#94a3b8';
        ctx.lineWidth = 1.5;
        ctx.strokeRect(bx, by, bw, bh);

        ctx.fillStyle = '#475569';
        ctx.fillRect(bx, by - 22, 160, 20);
        ctx.fillStyle = '#ffffff';
        ctx.font = 'bold 10px sans-serif';
        ctx.fillText('TRK#09 · SEATED DESK OPERATOR', bx + 6, by - 8);

        ctx.fillStyle = '#ef4444';
        ctx.font = 'bold 12px sans-serif';
        ctx.fillText('FILTERED: STATIONARY KNEE (0 REPS)', bx - 30, by + bh + 25);
      } else if (traineeMode === 'passerby') {
        const posX = ((t * 120) % (w + 100)) - 50;
        setKneeAngle(168);
        setBiomechState('PAUSED');
        setFault('FILTERED');

        const by = h * 0.2;
        const bw = w * 0.2;
        const bh = h * 0.65;

        ctx.strokeStyle = '#94a3b8';
        ctx.lineWidth = 1.5;
        ctx.strokeRect(posX, by, bw, bh);

        ctx.fillStyle = '#475569';
        ctx.fillRect(posX, by - 22, 170, 20);
        ctx.fillStyle = '#ffffff';
        ctx.font = 'bold 10px sans-serif';
        ctx.fillText('TRK#12 · WALKING PASSERBY', posX + 6, by - 8);

        ctx.fillStyle = '#ef4444';
        ctx.font = 'bold 12px sans-serif';
        ctx.fillText('FILTERED: HORIZONTAL SPEED > 220px/s', posX, by + bh + 25);
      }

      frameId = requestAnimationFrame(render);
    };

    frameId = requestAnimationFrame(render);
    return () => cancelAnimationFrame(frameId);
  }, [traineeMode]);

  return (
    <Card className="border-border">
      <CardHeader className="py-3 px-4 flex flex-row items-center justify-between">
        <div className="flex items-center gap-2">
          <Camera className="h-4 w-4 text-primary" />
          <CardTitle className="text-sm font-semibold">
            Biomechanical Engine Calibrator
          </CardTitle>
          <Badge variant="outline" className="text-[10px] bg-amber-500/10 text-amber-500 border-amber-500/30">
            Diagnostics Mode
          </Badge>
        </div>
      </CardHeader>

      <CardContent className="space-y-4 pt-2">
        <div className="flex flex-wrap items-center justify-between gap-3 border-b border-border pb-3">
          <div className="flex items-center gap-1.5 text-xs">
            <span className="text-muted-foreground mr-1">Input Scenarios:</span>
            <button
              type="button"
              onClick={() => setTraineeMode('trainee')}
              className={`px-3 py-1 rounded text-xs font-medium transition-colors ${
                traineeMode === 'trainee' ? 'bg-emerald-600 text-white' : 'bg-muted hover:bg-muted/80'
              }`}
            >
              1. Full Range Squat (Trainee)
            </button>
            <button
              type="button"
              onClick={() => setTraineeMode('seated_operator')}
              className={`px-3 py-1 rounded text-xs font-medium transition-colors ${
                traineeMode === 'seated_operator' ? 'bg-amber-600 text-white' : 'bg-muted hover:bg-muted/80'
              }`}
            >
              2. Seated Desk Operator (0 Reps)
            </button>
            <button
              type="button"
              onClick={() => setTraineeMode('passerby')}
              className={`px-3 py-1 rounded text-xs font-medium transition-colors ${
                traineeMode === 'passerby' ? 'bg-amber-600 text-white' : 'bg-muted hover:bg-muted/80'
              }`}
            >
              3. Walking Passerby (0 Reps)
            </button>
          </div>

          <Button size="sm" variant="outline" onClick={() => setRepCount(0)} className="h-7 text-xs">
            <RotateCcw className="h-3 w-3 mr-1" /> Reset Counter
          </Button>
        </div>

        <div className="relative aspect-video w-full overflow-hidden rounded-xl border border-border bg-slate-950">
          <canvas ref={canvasRef} width={640} height={360} className="h-full w-full object-contain" />
        </div>

        <div className="grid grid-cols-2 sm:grid-cols-4 gap-3 text-sm">
          <div className="rounded-lg border border-border p-3 bg-muted/20">
            <p className="text-xs text-muted-foreground">FSM State</p>
            <p className="text-sm font-bold text-foreground mt-0.5">{biomechState}</p>
          </div>
          <div className="rounded-lg border border-border p-3 bg-muted/20">
            <p className="text-xs text-muted-foreground">Interior Knee Angle</p>
            <p className="text-xl font-bold text-emerald-400 mt-0.5 tabular-nums">{kneeAngle}°</p>
          </div>
          <div className="rounded-lg border border-border p-3 bg-muted/20">
            <p className="text-xs text-muted-foreground">Form Diagnostic</p>
            <p
              className={`text-sm font-bold mt-0.5 ${
                fault === 'VALID'
                  ? 'text-emerald-500'
                  : fault === 'SHALLOW'
                  ? 'text-amber-500'
                  : 'text-muted-foreground'
              }`}
            >
              {fault === 'VALID' ? '✓ VALID (<=92°)' : fault === 'SHALLOW' ? '⚠ SHALLOW' : 'FILTERED OUT (0 REPS)'}
            </p>
          </div>
          <div className="rounded-lg border border-border p-3 bg-muted/20">
            <p className="text-xs text-muted-foreground">Rep Counter</p>
            <p className="text-xl font-bold text-primary mt-0.5 tabular-nums">{repCount}</p>
          </div>
        </div>

        {/* System Health Status & Diagnostic Guide */}
        <div className="rounded-xl border border-border/70 bg-card/60 p-4 space-y-2.5">
          <h4 className="text-xs font-semibold uppercase tracking-wider text-muted-foreground">
            Operational Diagnostics &amp; Helper Rules
          </h4>
          <div className="grid grid-cols-1 md:grid-cols-2 gap-2 text-xs">
            <div className="rounded-lg border border-border/50 bg-background/50 p-2.5 space-y-1">
              <p className="font-semibold text-foreground flex items-center gap-1.5">
                <span className="h-2 w-2 rounded-full bg-amber-500" />
                Backend Sync Buffering State
              </p>
              <p className="text-muted-foreground text-[11px]">
                &quot;Edge AI is running, but the database sync service is unavailable. Pending events are stored locally in SQLite and will be retried automatically.&quot;
              </p>
            </div>
            <div className="rounded-lg border border-border/50 bg-background/50 p-2.5 space-y-1">
              <p className="font-semibold text-foreground flex items-center gap-1.5">
                <span className="h-2 w-2 rounded-full bg-cyan-500" />
                Biometric Identification Fallback
              </p>
              <p className="text-muted-foreground text-[11px]">
                &quot;Facial identification is unavailable when model configuration is incomplete. Members check in using QR code or Member ID without biometric delay.&quot;
              </p>
            </div>
          </div>
        </div>

        {/* Central Camera Broker Live Hardware & Multiplexing Diagnostics */}
        <div className="pt-2">
          <CameraSessionView
            consumerId="admin-diagnostics"
            title="Central Camera Broker Live Diagnostics"
            subtitle="Multiplexed hardware camera health, consumer lease monitoring, and frame distribution"
            autoStart={true}
          />
        </div>
      </CardContent>
    </Card>
  );
}

