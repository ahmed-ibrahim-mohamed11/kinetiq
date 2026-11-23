import type { Metadata } from 'next';
import { Alert, Badge, Card, CardContent, CardDescription, CardHeader, CardTitle, EmptyState, Table, TableBody, TableCell, TableHead, TableHeader, TableRow, formatDateTime, formatDuration, statusVariant } from '@smartgym/ui';
import { createClient } from '@smartgym/supabase-client/server';
import type { Tables } from '@smartgym/supabase-client/types';
import { PageHeader } from '@/components/page-header';
import { IssueSecret } from './issue-secret';

export const metadata: Metadata = { title: 'Edge Devices' };
export const dynamic = 'force-dynamic';

type Device = Tables<'edge_devices'>;
type Health = Tables<'device_health_logs'>;
type Branch = Pick<Tables<'branches'>, 'id' | 'name'>;

export default async function DevicesPage() {
  const supabase = await createClient();
  const [devicesRes, healthRes, branchesRes] = await Promise.all([
    supabase.from('edge_devices').select('*').order('name').returns<Device[]>(),
    // Recent health rows; we keep the newest per device client-side (one query, no window function needed).
    supabase.from('device_health_logs').select('*').order('recorded_at', { ascending: false }).limit(200).returns<Health[]>(),
    supabase.from('branches').select('id, name').returns<Branch[]>(),
  ]);
  const devices = devicesRes.data ?? [];
  const latestHealth = new Map<string, Health>();
  for (const h of healthRes.data ?? []) if (!latestHealth.has(h.edge_device_id)) latestHealth.set(h.edge_device_id, h);
  const branchName = new Map((branchesRes.data ?? []).map((b) => [b.id, b.name]));
  const twoMinutesAgo = Date.now() - 2 * 60_000;

  return (
    <>
      <PageHeader
        title="Edge devices"
        description="GPU servers inside each branch that process camera streams. They authenticate to the cloud with a per-device secret and buffer events while offline."
      />
      {devicesRes.error && (
        <div className="mb-4">
          <Alert variant="error" title="Could not load devices">
            {devicesRes.error.message}
          </Alert>
        </div>
      )}
      <Card>
        <CardHeader>
          <CardTitle>Devices</CardTitle>
          <CardDescription>Health values are self-reported in the device heartbeat (every 30 s by default).</CardDescription>
        </CardHeader>
        <CardContent>
          {devices.length === 0 ? (
            <EmptyState title="No edge devices" description="Devices are registered by an administrator via the database or the provisioning script; then issue a secret here." />
          ) : (
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>Device</TableHead>
                  <TableHead>Status</TableHead>
                  <TableHead>Version</TableHead>
                  <TableHead>Last seen</TableHead>
                  <TableHead>Sequence</TableHead>
                  <TableHead>Cameras</TableHead>
                  <TableHead>Queue</TableHead>
                  <TableHead>Latency</TableHead>
                  <TableHead>GPU</TableHead>
                  <TableHead>Uptime</TableHead>
                  <TableHead className="text-right">Secret</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {devices.map((d) => {
                  const h = latestHealth.get(d.id);
                  const live = d.last_seen_at ? new Date(d.last_seen_at).getTime() >= twoMinutesAgo : false;
                  return (
                    <TableRow key={d.id}>
                      <TableCell>
                        <div className="font-medium">{d.name}</div>
                        <div className="text-xs text-muted-foreground">{branchName.get(d.branch_id) ?? '—'}</div>
                      </TableCell>
                      <TableCell>
                        <Badge variant={live ? 'success' : statusVariant(d.status)}>{live ? 'live' : d.status}</Badge>
                      </TableCell>
                      <TableCell className="font-mono text-xs">{d.software_version ?? '—'}</TableCell>
                      <TableCell className="text-sm">{formatDateTime(d.last_seen_at)}</TableCell>
                      <TableCell className="tabular-nums text-sm">{d.last_sequence < 0 ? '—' : d.last_sequence}</TableCell>
                      <TableCell className="tabular-nums text-sm">{h ? `${h.cameras_online ?? 0} / ${h.cameras_total ?? 0}` : '—'}</TableCell>
                      <TableCell className="tabular-nums text-sm">
                        {h?.queue_depth ?? '—'}
                        {h && (h.queue_depth ?? 0) > 500 && <span className="ml-1 text-xs text-warning">backlog</span>}
                      </TableCell>
                      <TableCell className="tabular-nums text-sm">{h?.avg_pipeline_latency_ms != null ? `${Math.round(h.avg_pipeline_latency_ms)} ms` : '—'}</TableCell>
                      <TableCell className="tabular-nums text-sm">
                        {h?.gpu_utilization != null ? `${Math.round(h.gpu_utilization * 100)}%` : '—'}
                        {h?.gpu_memory_used_mb != null && <div className="text-xs text-muted-foreground">{Math.round(h.gpu_memory_used_mb)} MB</div>}
                      </TableCell>
                      <TableCell className="text-sm">{h?.uptime_seconds != null ? formatDuration(h.uptime_seconds) : '—'}</TableCell>
                      <TableCell className="text-right">
                        <IssueSecret deviceId={d.id} deviceName={d.name} />
                      </TableCell>
                    </TableRow>
                  );
                })}
              </TableBody>
            </Table>
          )}
        </CardContent>
      </Card>
    </>
  );
}
