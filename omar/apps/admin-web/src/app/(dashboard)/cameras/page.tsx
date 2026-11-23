import type { Metadata } from 'next';
import Link from 'next/link';
import { Alert, Badge, Card, CardContent, CardDescription, CardHeader, CardTitle, EmptyState, Table, TableBody, TableCell, TableHead, TableHeader, TableRow, buttonVariants, formatDateTime, statusVariant } from '@smartgym/ui';
import { createClient } from '@smartgym/supabase-client/server';
import type { Tables } from '@smartgym/supabase-client/types';
import { PageHeader } from '@/components/page-header';
import { CameraForm } from './camera-form';

export const metadata: Metadata = { title: 'Cameras & Zones' };
export const dynamic = 'force-dynamic';

type Camera = Tables<'cameras'>;
type Branch = Pick<Tables<'branches'>, 'id' | 'name'>;
type Device = Pick<Tables<'edge_devices'>, 'id' | 'name' | 'branch_id'>;

export default async function CamerasPage() {
  const supabase = await createClient();
  const [camerasRes, zonesRes, branchesRes, devicesRes] = await Promise.all([
    supabase.from('cameras').select('*').order('name').returns<Camera[]>(),
    supabase.from('camera_zones').select('camera_id').returns<{ camera_id: string }[]>(),
    supabase.from('branches').select('id, name').order('name').returns<Branch[]>(),
    supabase.from('edge_devices').select('id, name, branch_id').neq('status', 'retired').order('name').returns<Device[]>(),
  ]);
  const cameras = camerasRes.data ?? [];
  const branches = branchesRes.data ?? [];
  const devices = devicesRes.data ?? [];
  const zoneCount = new Map<string, number>();
  for (const z of zonesRes.data ?? []) zoneCount.set(z.camera_id, (zoneCount.get(z.camera_id) ?? 0) + 1);
  const branchName = new Map(branches.map((b) => [b.id, b.name]));
  const deviceName = new Map(devices.map((d) => [d.id, d.name]));

  return (
    <>
      <PageHeader
        title="Cameras & zones"
        description="Existing CCTV/IP cameras registered for AI processing. Stream URLs never contain credentials; those live in the edge device's secrets file."
        breadcrumbs={[{ label: 'Cameras & Zones' }]}
      />
      {camerasRes.error && (
        <div className="mb-4">
          <Alert variant="error" title="Could not load cameras">
            {camerasRes.error.message} — camera configuration is visible to owners, admins, managers and auditors only.
          </Alert>
        </div>
      )}
      <div className="grid gap-6 lg:grid-cols-[2fr_1fr]">
        <Card>
          <CardContent className="pt-6">
            {cameras.length === 0 ? (
              <EmptyState title="No cameras registered" description="Register the first camera with the form on the right, then draw its equipment zones." />
            ) : (
              <Table>
                <TableHeader>
                  <TableRow>
                    <TableHead>Camera</TableHead>
                    <TableHead>Status</TableHead>
                    <TableHead>Purpose</TableHead>
                    <TableHead>Stream</TableHead>
                    <TableHead>Device</TableHead>
                    <TableHead>Zones</TableHead>
                    <TableHead />
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {cameras.map((c) => (
                    <TableRow key={c.id}>
                      <TableCell>
                        <div className="font-medium">{c.name}</div>
                        <div className="text-xs text-muted-foreground">{branchName.get(c.branch_id) ?? '—'}</div>
                      </TableCell>
                      <TableCell>
                        {c.enabled ? (
                          <>
                            <Badge variant={statusVariant(c.last_status ?? 'unknown')}>{c.last_status ?? 'no report'}</Badge>
                            {c.last_status_at && <div className="mt-1 text-xs text-muted-foreground">{formatDateTime(c.last_status_at)}</div>}
                          </>
                        ) : (
                          <Badge variant="secondary">disabled</Badge>
                        )}
                      </TableCell>
                      <TableCell className="text-sm">{c.purpose}</TableCell>
                      <TableCell className="text-xs">
                        <div className="font-mono">{c.protocol}</div>
                        <div className="text-muted-foreground">
                          {c.resolution_w && c.resolution_h ? `${c.resolution_w}×${c.resolution_h}` : 'res. unknown'}
                          {c.fps ? ` @ ${c.fps} fps` : ''}
                        </div>
                      </TableCell>
                      <TableCell className="text-sm">{c.edge_device_id ? deviceName.get(c.edge_device_id) ?? 'unknown' : <span className="text-muted-foreground">unassigned</span>}</TableCell>
                      <TableCell className="tabular-nums">{zoneCount.get(c.id) ?? 0}</TableCell>
                      <TableCell className="text-right">
                        <Link href={`/cameras/${c.id}/zones`} className={buttonVariants({ size: 'sm', variant: 'outline' })}>
                          Zones
                        </Link>
                      </TableCell>
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
            )}
          </CardContent>
        </Card>

        <Card className="self-start">
          <CardHeader>
            <CardTitle>Register camera</CardTitle>
            <CardDescription>Use the camera&apos;s RTSP/ONVIF address without username or password.</CardDescription>
          </CardHeader>
          <CardContent>
            {branches.length === 0 ? (
              <p className="text-sm text-muted-foreground">No branch is accessible to your account.</p>
            ) : (
              <CameraForm branches={branches} devices={devices} />
            )}
          </CardContent>
        </Card>
      </div>
    </>
  );
}
