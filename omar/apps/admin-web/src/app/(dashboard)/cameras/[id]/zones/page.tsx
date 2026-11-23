import type { Metadata } from 'next';
import Link from 'next/link';
import { notFound } from 'next/navigation';
import { Alert, Badge, Card, CardContent, CardDescription, CardHeader, CardTitle } from '@smartgym/ui';
import { createClient } from '@smartgym/supabase-client/server';
import type { Tables } from '@smartgym/supabase-client/types';
import { PageHeader } from '@/components/page-header';
import { ZoneEditor, type ZoneShape } from './zone-editor';

export const dynamic = 'force-dynamic';

type Camera = Tables<'cameras'>;
type Zone = Tables<'camera_zones'>;
type Equipment = Pick<Tables<'equipment'>, 'id' | 'name' | 'category'>;

export async function generateMetadata({ params }: { params: Promise<{ id: string }> }): Promise<Metadata> {
  const { id } = await params;
  const supabase = await createClient();
  const { data } = await supabase.from('cameras').select('name').eq('id', id).maybeSingle();
  return { title: `Zones · ${(data as { name: string } | null)?.name ?? 'Camera'}` };
}

function parsePolygon(raw: unknown): Array<[number, number]> {
  if (!Array.isArray(raw)) return [];
  const pts: Array<[number, number]> = [];
  for (const p of raw) {
    if (Array.isArray(p) && p.length >= 2 && typeof p[0] === 'number' && typeof p[1] === 'number') pts.push([p[0], p[1]]);
  }
  return pts;
}

export default async function CameraZonesPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  const supabase = await createClient();
  const { data: cameraData, error } = await supabase.from('cameras').select('*').eq('id', id).maybeSingle();
  if (error) {
    return (
      <Alert variant="error" title="Could not load camera">
        {error.message}
      </Alert>
    );
  }
  const camera = cameraData as Camera | null;
  if (!camera) notFound();

  const [zonesRes, equipmentRes] = await Promise.all([
    supabase.from('camera_zones').select('*').eq('camera_id', id).order('created_at').returns<Zone[]>(),
    supabase.from('equipment').select('id, name, category').eq('branch_id', camera.branch_id).eq('enabled', true).order('name').returns<Equipment[]>(),
  ]);
  const equipment = equipmentRes.data ?? [];
  const equipmentName = new Map(equipment.map((e) => [e.id, e.name]));
  const zones: ZoneShape[] = (zonesRes.data ?? []).map((z) => ({
    id: z.id,
    name: z.name,
    zone_type: z.zone_type,
    equipment_id: z.equipment_id,
    equipment_name: z.equipment_id ? equipmentName.get(z.equipment_id) ?? null : null,
    polygon: parsePolygon(z.polygon),
  }));

  return (
    <>
      <PageHeader
        title={`Zones · ${camera.name}`}
        description="Draw the image-space region of each machine (and entrance/exit/exclusion areas). The edge associates a person with equipment when their foot point stays inside a zone."
        actions={
          <Link href="/cameras" className="self-center text-sm text-muted-foreground hover:underline">
            ← All cameras
          </Link>
        }
      />
      <div className="mb-4 flex flex-wrap gap-2 text-sm">
        <Badge variant="outline">{camera.purpose}</Badge>
        <Badge variant="outline">{camera.protocol}</Badge>
        <Badge variant="outline">{camera.resolution_w && camera.resolution_h ? `${camera.resolution_w} × ${camera.resolution_h}` : 'resolution unknown — using 1920 × 1080'}</Badge>
        <Badge variant="outline">{zones.length} zone{zones.length === 1 ? '' : 's'}</Badge>
      </div>
      <Card>
        <CardHeader>
          <CardTitle>Zone editor</CardTitle>
          <CardDescription>
            Click to add polygon points in camera pixel coordinates, then close the shape and save. A live camera snapshot behind the canvas arrives with Phase 2
            camera integration; until then use the camera&apos;s own viewer to read off coordinates.
          </CardDescription>
        </CardHeader>
        <CardContent>
          <ZoneEditor
            cameraId={camera.id}
            width={camera.resolution_w ?? 1920}
            height={camera.resolution_h ?? 1080}
            zones={zones}
            equipment={equipment}
          />
        </CardContent>
      </Card>
    </>
  );
}
