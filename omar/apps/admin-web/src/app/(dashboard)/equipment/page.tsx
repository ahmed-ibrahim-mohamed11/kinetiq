import type { Metadata } from 'next';
import { Alert, Badge, Card, CardContent, CardDescription, CardHeader, CardTitle, EmptyState, Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@smartgym/ui';
import { createClient } from '@smartgym/supabase-client/server';
import type { Tables } from '@smartgym/supabase-client/types';
import { PageHeader } from '@/components/page-header';
import { EquipmentForm } from './equipment-form';
import { FloorMap, type FloorPlan } from './floor-map';

export const metadata: Metadata = { title: 'Equipment' };
export const dynamic = 'force-dynamic';

type Equipment = Tables<'equipment'>;
type Branch = Pick<Tables<'branches'>, 'id' | 'name' | 'floor_plan'>;
type Exercise = Pick<Tables<'exercise_catalog'>, 'id' | 'name' | 'slug'>;

function parseFloorPlan(raw: unknown): FloorPlan | null {
  if (!raw || typeof raw !== 'object') return null;
  const o = raw as Record<string, unknown>;
  const w = Number(o.width_m);
  const h = Number(o.height_m);
  if (!Number.isFinite(w) || !Number.isFinite(h) || w <= 0 || h <= 0) return null;
  return { width_m: w, height_m: h };
}

export default async function EquipmentPage() {
  const supabase = await createClient();
  const [equipmentRes, branchesRes, exercisesRes] = await Promise.all([
    supabase.from('equipment').select('*').order('name').returns<Equipment[]>(),
    supabase.from('branches').select('id, name, floor_plan').order('name').returns<Branch[]>(),
    supabase.from('exercise_catalog').select('id, name, slug').order('name').returns<Exercise[]>(),
  ]);
  const equipment = equipmentRes.data ?? [];
  const branches = branchesRes.data ?? [];
  const exercises = exercisesRes.data ?? [];
  const exerciseName = new Map(exercises.map((e) => [e.id, e.name]));

  return (
    <>
      <PageHeader title="Equipment" description="Machines and stations per branch. Positions are in metres on the branch floor plan; camera zones link cameras to equipment." />
      {equipmentRes.error && (
        <div className="mb-4">
          <Alert variant="error" title="Could not load equipment">
            {equipmentRes.error.message}
          </Alert>
        </div>
      )}

      <div className="grid gap-6 lg:grid-cols-[2fr_1fr]">
        <div className="space-y-6">
          {branches.map((branch) => {
            const items = equipment.filter((e) => e.branch_id === branch.id);
            const plan = parseFloorPlan(branch.floor_plan);
            return (
              <Card key={branch.id}>
                <CardHeader>
                  <CardTitle>{branch.name}</CardTitle>
                  <CardDescription>
                    {items.length} item{items.length === 1 ? '' : 's'}
                    {plan ? ` · floor plan ${plan.width_m} × ${plan.height_m} m` : ' · no floor plan dimensions set on this branch'}
                  </CardDescription>
                </CardHeader>
                <CardContent className="space-y-6">
                  {plan && <FloorMap plan={plan} items={items.map((e) => ({ id: e.id, name: e.name, x: e.position_x_m, y: e.position_y_m, enabled: e.enabled }))} />}
                  {items.length === 0 ? (
                    <EmptyState title="No equipment registered" description="Add machines with the form on the right." />
                  ) : (
                    <Table>
                      <TableHeader>
                        <TableRow>
                          <TableHead>Name</TableHead>
                          <TableHead>Category</TableHead>
                          <TableHead>Default exercise</TableHead>
                          <TableHead>Position (m)</TableHead>
                          <TableHead>Status</TableHead>
                        </TableRow>
                      </TableHeader>
                      <TableBody>
                        {items.map((e) => (
                          <TableRow key={e.id}>
                            <TableCell className="font-medium">{e.name}</TableCell>
                            <TableCell className="text-sm">{e.category.replaceAll('_', ' ')}</TableCell>
                            <TableCell className="text-sm">{e.default_exercise_id ? exerciseName.get(e.default_exercise_id) ?? '—' : '—'}</TableCell>
                            <TableCell className="text-sm tabular-nums">
                              {e.position_x_m !== null && e.position_y_m !== null ? `${e.position_x_m}, ${e.position_y_m}` : '—'}
                            </TableCell>
                            <TableCell>
                              <Badge variant={e.enabled ? 'success' : 'secondary'}>{e.enabled ? 'enabled' : 'disabled'}</Badge>
                            </TableCell>
                          </TableRow>
                        ))}
                      </TableBody>
                    </Table>
                  )}
                </CardContent>
              </Card>
            );
          })}
          {branches.length === 0 && <EmptyState title="No branches accessible" />}
        </div>

        <Card className="self-start">
          <CardHeader>
            <CardTitle>Add equipment</CardTitle>
            <CardDescription>The default exercise is what the edge assumes when a member uses this machine until a classifier is trained.</CardDescription>
          </CardHeader>
          <CardContent>
            {branches.length === 0 ? (
              <p className="text-sm text-muted-foreground">No branch is accessible to your account.</p>
            ) : (
              <EquipmentForm branches={branches.map((b) => ({ id: b.id, name: b.name }))} exercises={exercises} />
            )}
          </CardContent>
        </Card>
      </div>
    </>
  );
}
