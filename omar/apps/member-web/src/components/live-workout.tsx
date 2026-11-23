'use client';

import { useCallback, useEffect, useState } from 'react';
import { createClient } from '@smartgym/supabase-client/browser';
import { Alert, Badge, LiveDot, Table, TableBody, TableCell, TableHead, TableHeader, TableRow, formatTime, percent } from '@smartgym/ui';

interface SetRow {
  id: string;
  set_index: number;
  rep_count: number;
  started_at: string;
  ended_at: string | null;
  confidence: number;
  verified: boolean;
  exerciseName: string;
  equipmentName: string | null;
  activityId: string;
}

interface UsageRow {
  id: string;
  equipment: { name: string } | { name: string }[] | null;
  exercise_activity_sessions:
    | Array<{
        id: string;
        exercise_catalog: { name: string } | { name: string }[] | null;
        exercise_sets: Array<{ id: string; set_index: number; rep_count: number; started_at: string; ended_at: string | null; confidence: number; verified: boolean }>;
      }>
    | null;
}

function one<T>(v: T | T[] | null): T | null {
  if (v == null) return null;
  return Array.isArray(v) ? (v[0] ?? null) : v;
}

export function LiveWorkout({ sessionId }: { sessionId: string }) {
  const [sets, setSets] = useState<SetRow[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [live, setLive] = useState(false);

  const load = useCallback(async () => {
    const supabase = createClient();
    const { data, error: err } = await supabase
      .from('equipment_usage_sessions')
      .select(
        'id, equipment:equipment_id(name), exercise_activity_sessions(id, exercise_catalog:exercise_id(name), exercise_sets(id, set_index, rep_count, started_at, ended_at, confidence, verified))',
      )
      .eq('attendance_session_id', sessionId)
      .order('started_at', { ascending: true });
    if (err) {
      setError(err.message);
      return;
    }
    const rows: SetRow[] = [];
    for (const usage of (data ?? []) as unknown as UsageRow[]) {
      const equipmentName = one(usage.equipment)?.name ?? null;
      for (const activity of usage.exercise_activity_sessions ?? []) {
        const exerciseName = one(activity.exercise_catalog)?.name ?? 'Exercise';
        for (const s of activity.exercise_sets ?? []) {
          rows.push({ ...s, exerciseName, equipmentName, activityId: activity.id });
        }
      }
    }
    rows.sort((a, b) => a.started_at.localeCompare(b.started_at));
    setSets(rows);
    setError(null);
  }, [sessionId]);

  useEffect(() => {
    void load();
    const supabase = createClient();
    const channel = supabase
      .channel(`live-workout-${sessionId}`)
      .on('postgres_changes', { event: '*', schema: 'public', table: 'exercise_sets' }, () => void load())
      .on('postgres_changes', { event: '*', schema: 'public', table: 'repetition_events' }, () => void load())
      .subscribe((status) => setLive(status === 'SUBSCRIBED'));
    // Realtime can miss events while the phone screen is off; poll as a safety net.
    const poll = setInterval(() => void load(), 15_000);
    return () => {
      clearInterval(poll);
      void supabase.removeChannel(channel);
    };
  }, [load, sessionId]);

  if (error) {
    return (
      <Alert variant="error" title="Could not load live workout">
        {error}
      </Alert>
    );
  }
  if (sets === null) return <p className="text-sm text-muted-foreground">Loading your setsâ€¦</p>;

  const byExercise = new Map<string, { exerciseName: string; equipmentName: string | null; sets: SetRow[] }>();
  for (const s of sets) {
    const key = `${s.activityId}`;
    const group = byExercise.get(key) ?? { exerciseName: s.exerciseName, equipmentName: s.equipmentName, sets: [] };
    group.sets.push(s);
    byExercise.set(key, group);
  }
  const totalReps = sets.reduce((acc, s) => acc + s.rep_count, 0);

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between text-sm">
        <span className="flex items-center gap-2 text-muted-foreground">
          <LiveDot active={live} /> {live ? 'Live updates on' : 'Refreshing every 15 s'}
        </span>
        <span className="tabular-nums">
          {sets.length} {sets.length === 1 ? 'set' : 'sets'} Â· {totalReps} reps
        </span>
      </div>
      {sets.length === 0 ? (
        <p className="rounded-md border border-dashed p-4 text-sm text-muted-foreground">
          No sets recorded yet. Sets appear here automatically once a camera sees you working on a machine.
        </p>
      ) : (
        [...byExercise.values()].map((group, i) => (
          <div key={i} className="rounded-lg border">
            <div className="flex items-baseline justify-between px-4 py-3">
              <div>
                <p className="font-medium">{group.exerciseName}</p>
                {group.equipmentName && <p className="text-xs text-muted-foreground">{group.equipmentName}</p>}
              </div>
              <p className="text-sm tabular-nums text-muted-foreground">{group.sets.reduce((a, s) => a + s.rep_count, 0)} reps</p>
            </div>
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead className="h-9">Set</TableHead>
                  <TableHead className="h-9">Reps</TableHead>
                  <TableHead className="h-9">Time</TableHead>
                  <TableHead className="h-9 text-right">Status</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {group.sets.map((s) => {
                  const inProgress = s.ended_at === null;
                  return (
                    <TableRow key={s.id}>
                      <TableCell className="py-2 tabular-nums">{s.set_index}</TableCell>
                      <TableCell className="py-2 text-base font-semibold tabular-nums">{s.rep_count}</TableCell>
                      <TableCell className="py-2 text-muted-foreground">{formatTime(s.started_at)}</TableCell>
                      <TableCell className="py-2 text-right">
                        {inProgress ? (
                          <Badge variant="default">in progress</Badge>
                        ) : s.verified ? (
                          <Badge variant="success" title={`confidence ${percent(s.confidence)}`}>
                            verified Â· {percent(s.confidence)}
                          </Badge>
                        ) : (
                          <Badge variant="warning" title="Tracking was uncertain during this set">
                            unverified
                          </Badge>
                        )}
                      </TableCell>
                    </TableRow>
                  );
                })}
              </TableBody>
            </Table>
          </div>
        ))
      )}
    </div>
  );
}
