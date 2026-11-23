'use client';

import { useActionState, useEffect, useMemo, useRef, useState } from 'react';
import { Badge, Button, Input, Label, Select } from '@smartgym/ui';
import { createCameraZone, deleteCameraZone } from '@/lib/actions';
import type { ActionResult } from '@/lib/action-result';
import { ActionFeedback } from '@/components/action-feedback';

export interface ZoneShape {
  id: string;
  name: string;
  zone_type: string;
  equipment_id: string | null;
  equipment_name: string | null;
  polygon: Array<[number, number]>;
}

type Point = [number, number];

const ZONE_TYPES = [
  { value: 'equipment', label: 'Equipment' },
  { value: 'entrance', label: 'Entrance' },
  { value: 'exit', label: 'Exit' },
  { value: 'exclusion', label: 'Exclusion (never process)' },
  { value: 'free_weights', label: 'Free-weights area' },
] as const;

const ZONE_COLORS: Record<string, string> = {
  equipment: 'hsl(221 83% 53%)',
  entrance: 'hsl(142 71% 45%)',
  exit: 'hsl(38 92% 50%)',
  exclusion: 'hsl(0 84% 60%)',
  free_weights: 'hsl(262 83% 58%)',
};

function centroid(poly: Point[]): Point {
  if (poly.length === 0) return [0, 0];
  const sx = poly.reduce((a, p) => a + p[0], 0);
  const sy = poly.reduce((a, p) => a + p[1], 0);
  return [sx / poly.length, sy / poly.length];
}

export function ZoneEditor({
  cameraId,
  width,
  height,
  zones,
  equipment,
}: {
  cameraId: string;
  width: number;
  height: number;
  zones: ZoneShape[];
  equipment: { id: string; name: string; category: string }[];
}) {
  const svgRef = useRef<SVGSVGElement>(null);
  const [points, setPoints] = useState<Point[]>([]);
  const [closed, setClosed] = useState(false);
  const [hover, setHover] = useState<Point | null>(null);
  const [zoneType, setZoneType] = useState<string>('equipment');
  const [state, action, pending] = useActionState<ActionResult | null, FormData>(createCameraZone, null);

  useEffect(() => {
    if (state?.ok) {
      setPoints([]);
      setClosed(false);
    }
  }, [state]);

  const toImage = (evt: React.MouseEvent<SVGSVGElement>): Point | null => {
    const svg = svgRef.current;
    if (!svg) return null;
    const rect = svg.getBoundingClientRect();
    const x = ((evt.clientX - rect.left) / rect.width) * width;
    const y = ((evt.clientY - rect.top) / rect.height) * height;
    return [Math.round(Math.min(width, Math.max(0, x))), Math.round(Math.min(height, Math.max(0, y)))];
  };

  const onClick = (evt: React.MouseEvent<SVGSVGElement>) => {
    if (closed) return;
    const p = toImage(evt);
    if (p) setPoints((prev) => [...prev, p]);
  };

  const undo = () => {
    setClosed(false);
    setPoints((prev) => prev.slice(0, -1));
  };
  const clear = () => {
    setPoints([]);
    setClosed(false);
  };

  const polygonJson = useMemo(() => JSON.stringify(points), [points]);
  const canClose = points.length >= 3 && !closed;
  const strokeW = Math.max(1, width / 640);
  const fontSize = Math.max(12, width / 60);

  return (
    <div className="grid gap-6 lg:grid-cols-[3fr_1fr]">
      <div>
        <div className="mb-2 flex flex-wrap items-center gap-2 text-xs text-muted-foreground">
          <span>
            {points.length} point{points.length === 1 ? '' : 's'}
            {closed ? ' · closed' : points.length >= 3 ? ' · click "Close polygon" when done' : ' · click on the canvas to add points'}
          </span>
          {hover && (
            <span className="ml-auto font-mono">
              x {hover[0]} · y {hover[1]}
            </span>
          )}
        </div>
        <svg
          ref={svgRef}
          viewBox={`0 0 ${width} ${height}`}
          className="w-full cursor-crosshair rounded-md border bg-neutral-900 shadow-inner"
          style={{ aspectRatio: `${width} / ${height}` }}
          onClick={onClick}
          onMouseMove={(e) => setHover(toImage(e))}
          onMouseLeave={() => setHover(null)}
          role="img"
          aria-label="Camera zone editor canvas"
        >
          {/* 10% grid to help place points without a snapshot */}
          {Array.from({ length: 9 }, (_, i) => (
            <g key={i} stroke="white" strokeOpacity="0.08" strokeWidth={strokeW}>
              <line x1={((i + 1) * width) / 10} y1={0} x2={((i + 1) * width) / 10} y2={height} />
              <line x1={0} y1={((i + 1) * height) / 10} x2={width} y2={((i + 1) * height) / 10} />
            </g>
          ))}
          <text x={width / 2} y={height / 2} fontSize={fontSize} fill="white" fillOpacity="0.25" textAnchor="middle">
            Camera snapshot background — Phase 2
          </text>

          {zones.map((z) => {
            const c = centroid(z.polygon);
            const color = ZONE_COLORS[z.zone_type] ?? ZONE_COLORS.equipment!;
            return (
              <g key={z.id}>
                <polygon points={z.polygon.map((p) => p.join(',')).join(' ')} fill={color} fillOpacity="0.25" stroke={color} strokeWidth={strokeW * 1.5} />
                <text x={c[0]} y={c[1]} fontSize={fontSize} fill="white" textAnchor="middle" dominantBaseline="middle" style={{ paintOrder: 'stroke' }} stroke="black" strokeWidth={strokeW}>
                  {z.name}
                </text>
              </g>
            );
          })}

          {points.length > 0 && (
            <g>
              {closed ? (
                <polygon points={points.map((p) => p.join(',')).join(' ')} fill={ZONE_COLORS[zoneType] ?? 'white'} fillOpacity="0.35" stroke="white" strokeWidth={strokeW * 2} />
              ) : (
                <polyline
                  points={[...points, ...(hover ? [hover] : [])].map((p) => p.join(',')).join(' ')}
                  fill="none"
                  stroke="white"
                  strokeWidth={strokeW * 2}
                  strokeDasharray={`${strokeW * 6} ${strokeW * 4}`}
                />
              )}
              {points.map((p, i) => (
                <circle key={i} cx={p[0]} cy={p[1]} r={strokeW * 5} fill={i === 0 ? 'hsl(142 71% 45%)' : 'white'} stroke="black" strokeWidth={strokeW} />
              ))}
            </g>
          )}
        </svg>
        <div className="mt-3 flex flex-wrap gap-2">
          <Button type="button" size="sm" variant="outline" onClick={() => setClosed(true)} disabled={!canClose}>
            Close polygon
          </Button>
          <Button type="button" size="sm" variant="outline" onClick={undo} disabled={points.length === 0}>
            Undo point
          </Button>
          <Button type="button" size="sm" variant="ghost" onClick={clear} disabled={points.length === 0}>
            Clear
          </Button>
        </div>

        <div className="mt-6">
          <p className="mb-2 text-sm font-medium">Existing zones</p>
          {zones.length === 0 ? (
            <p className="text-sm text-muted-foreground">None yet.</p>
          ) : (
            <ul className="divide-y rounded-md border">
              {zones.map((z) => (
                <li key={z.id} className="flex items-center justify-between gap-3 p-3 text-sm">
                  <div className="flex items-center gap-2">
                    <span className="inline-block h-3 w-3 rounded-sm" style={{ background: ZONE_COLORS[z.zone_type] ?? 'gray' }} aria-hidden />
                    <span className="font-medium">{z.name}</span>
                    <Badge variant="outline">{z.zone_type}</Badge>
                    {z.equipment_name && <span className="text-muted-foreground">→ {z.equipment_name}</span>}
                    <span className="text-xs text-muted-foreground">{z.polygon.length} pts</span>
                  </div>
                  <DeleteZoneButton zoneId={z.id} cameraId={cameraId} />
                </li>
              ))}
            </ul>
          )}
        </div>
      </div>

      <form action={action} className="space-y-4 self-start rounded-md border p-4">
        <p className="text-sm font-medium">New zone</p>
        <input type="hidden" name="camera_id" value={cameraId} />
        <input type="hidden" name="polygon" value={polygonJson} />
        <div className="space-y-2">
          <Label htmlFor="zone_name">Name</Label>
          <Input id="zone_name" name="name" required maxLength={100} placeholder="lat-pulldown-1" />
        </div>
        <div className="space-y-2">
          <Label htmlFor="zone_type">Type</Label>
          <Select id="zone_type" name="zone_type" value={zoneType} onChange={(e) => setZoneType(e.target.value)}>
            {ZONE_TYPES.map((t) => (
              <option key={t.value} value={t.value}>
                {t.label}
              </option>
            ))}
          </Select>
        </div>
        <div className="space-y-2">
          <Label htmlFor="zone_equipment">Equipment</Label>
          <Select id="zone_equipment" name="equipment_id" defaultValue="" required={zoneType === 'equipment'} disabled={zoneType !== 'equipment'}>
            <option value="">{zoneType === 'equipment' ? 'Select equipment…' : 'Not applicable'}</option>
            {equipment.map((e) => (
              <option key={e.id} value={e.id}>
                {e.name}
              </option>
            ))}
          </Select>
          {zoneType === 'equipment' && equipment.length === 0 && (
            <p className="text-xs text-warning">No enabled equipment in this branch yet. Add it on the Equipment page first.</p>
          )}
        </div>
        <ActionFeedback state={state} />
        <Button type="submit" className="w-full" disabled={pending || !closed}>
          {pending ? 'Saving…' : closed ? 'Save zone' : 'Close the polygon to save'}
        </Button>
      </form>
    </div>
  );
}

function DeleteZoneButton({ zoneId, cameraId }: { zoneId: string; cameraId: string }) {
  const [state, action, pending] = useActionState<ActionResult | null, FormData>(deleteCameraZone, null);
  return (
    <form
      action={action}
      onSubmit={(e) => {
        if (!window.confirm('Delete this zone? The edge device stops associating this equipment with this camera.')) e.preventDefault();
      }}
      className="flex flex-col items-end"
    >
      <input type="hidden" name="zone_id" value={zoneId} />
      <input type="hidden" name="camera_id" value={cameraId} />
      <Button type="submit" size="sm" variant="ghost" disabled={pending} className="text-destructive hover:text-destructive">
        {pending ? 'Deleting…' : 'Delete'}
      </Button>
      {state && !state.ok && <span className="text-xs text-destructive">{state.error}</span>}
    </form>
  );
}
