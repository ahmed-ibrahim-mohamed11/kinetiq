export interface FloorPlan {
  width_m: number;
  height_m: number;
}

export interface FloorItem {
  id: string;
  name: string;
  x: number | null;
  y: number | null;
  enabled: boolean;
}

/**
 * Schematic floor map: a 1 m grid in plan units with equipment positions.
 * Pure SVG, server-renderable. Interactive placement arrives with Phase 5 floor mapping.
 */
export function FloorMap({ plan, items }: { plan: FloorPlan; items: FloorItem[] }) {
  const placed = items.filter((i) => i.x !== null && i.y !== null) as Array<FloorItem & { x: number; y: number }>;
  const unplaced = items.length - placed.length;
  const fontSize = Math.max(0.35, Math.min(plan.width_m, plan.height_m) / 40);
  const r = Math.max(0.25, Math.min(plan.width_m, plan.height_m) / 60);

  return (
    <figure className="rounded-md border bg-muted/30 p-3">
      <svg
        viewBox={`-0.5 -0.5 ${plan.width_m + 1} ${plan.height_m + 1}`}
        className="h-auto w-full"
        role="img"
        aria-label={`Floor plan ${plan.width_m} by ${plan.height_m} metres with ${placed.length} placed equipment items`}
      >
        <defs>
          <pattern id="grid" width="1" height="1" patternUnits="userSpaceOnUse">
            <path d="M 1 0 L 0 0 0 1" fill="none" stroke="currentColor" strokeOpacity="0.08" strokeWidth="0.03" />
          </pattern>
        </defs>
        <rect x="0" y="0" width={plan.width_m} height={plan.height_m} fill="url(#grid)" stroke="currentColor" strokeOpacity="0.4" strokeWidth="0.06" />
        {placed.map((item) => (
          <g key={item.id}>
            <circle cx={item.x} cy={item.y} r={r} className={item.enabled ? 'fill-primary' : 'fill-muted-foreground'} fillOpacity="0.9" />
            <text x={item.x + r * 1.4} y={item.y + fontSize * 0.35} fontSize={fontSize} className="fill-foreground">
              {item.name}
            </text>
          </g>
        ))}
      </svg>
      <figcaption className="mt-2 text-xs text-muted-foreground">
        1 m grid · origin top-left · {placed.length} placed{unplaced > 0 ? ` · ${unplaced} without position` : ''}
      </figcaption>
    </figure>
  );
}
