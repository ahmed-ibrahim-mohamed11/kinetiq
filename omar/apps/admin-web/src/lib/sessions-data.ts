import 'server-only';
import { createClient } from '@smartgym/supabase-client/server';

export interface LiveSetSummary {
  exercise_name: string;
  equipment_name: string | null;
  set_index: number;
  rep_count: number;
  in_progress: boolean;
  verified: boolean;
  started_at: string;
}

export interface LiveSessionRow {
  id: string;
  member_id: string;
  member_name: string;
  member_code: string;
  check_in_at: string;
  check_in_method: string;
  check_in_confidence: number | null;
  sets_total: number;
  reps_total: number;
  latest_set: LiveSetSummary | null;
}

interface SessionRow {
  id: string;
  member_id: string;
  check_in_at: string;
  check_in_method: string;
  check_in_confidence: number | null;
  members: { full_name: string; member_code: string } | null;
}
interface UsageRow {
  id: string;
  attendance_session_id: string | null;
  equipment_id: string;
}
interface ActivityRow {
  id: string;
  equipment_usage_session_id: string;
  exercise_id: string;
}
interface SetRow {
  id: string;
  exercise_activity_session_id: string;
  set_index: number;
  rep_count: number;
  started_at: string;
  ended_at: string | null;
  verified: boolean;
}

/** Active sessions with a compact summary of their latest set. Kept to simple `in()` queries so RLS stays cheap. */
export async function loadLiveSessions(): Promise<{ rows: LiveSessionRow[]; error: string | null }> {
  const supabase = await createClient();
  const { data: sessions, error } = await supabase
    .from('attendance_sessions')
    .select('id, member_id, check_in_at, check_in_method, check_in_confidence, members(full_name, member_code)')
    .eq('status', 'active')
    .order('check_in_at', { ascending: false })
    .returns<SessionRow[]>();
  if (error) return { rows: [], error: error.message };
  if (!sessions || sessions.length === 0) return { rows: [], error: null };

  const sessionIds = sessions.map((s) => s.id);
  const { data: usages } = await supabase
    .from('equipment_usage_sessions')
    .select('id, attendance_session_id, equipment_id')
    .in('attendance_session_id', sessionIds)
    .returns<UsageRow[]>();
  const usageList = usages ?? [];

  const activityList: ActivityRow[] = [];
  const setList: SetRow[] = [];
  if (usageList.length > 0) {
    const { data: activities } = await supabase
      .from('exercise_activity_sessions')
      .select('id, equipment_usage_session_id, exercise_id')
      .in(
        'equipment_usage_session_id',
        usageList.map((u) => u.id),
      )
      .returns<ActivityRow[]>();
    activityList.push(...(activities ?? []));
    if (activityList.length > 0) {
      const { data: sets } = await supabase
        .from('exercise_sets')
        .select('id, exercise_activity_session_id, set_index, rep_count, started_at, ended_at, verified')
        .in(
          'exercise_activity_session_id',
          activityList.map((a) => a.id),
        )
        .order('started_at', { ascending: false })
        .returns<SetRow[]>();
      setList.push(...(sets ?? []));
    }
  }

  const exerciseIds = Array.from(new Set(activityList.map((a) => a.exercise_id)));
  const equipmentIds = Array.from(new Set(usageList.map((u) => u.equipment_id)));
  const [exercises, equipment] = await Promise.all([
    exerciseIds.length ? supabase.from('exercise_catalog').select('id, name').in('id', exerciseIds).returns<{ id: string; name: string }[]>() : Promise.resolve({ data: [] as { id: string; name: string }[] }),
    equipmentIds.length ? supabase.from('equipment').select('id, name').in('id', equipmentIds).returns<{ id: string; name: string }[]>() : Promise.resolve({ data: [] as { id: string; name: string }[] }),
  ]);
  const exerciseName = new Map((exercises.data ?? []).map((e) => [e.id, e.name]));
  const equipmentName = new Map((equipment.data ?? []).map((e) => [e.id, e.name]));
  const usageById = new Map(usageList.map((u) => [u.id, u]));
  const activityById = new Map(activityList.map((a) => [a.id, a]));

  const perSession = new Map<string, { sets: number; reps: number; latest: LiveSetSummary | null }>();
  for (const set of setList) {
    const activity = activityById.get(set.exercise_activity_session_id);
    const usage = activity ? usageById.get(activity.equipment_usage_session_id) : undefined;
    const sessionId = usage?.attendance_session_id;
    if (!activity || !usage || !sessionId) continue;
    const agg = perSession.get(sessionId) ?? { sets: 0, reps: 0, latest: null };
    agg.sets += 1;
    agg.reps += set.rep_count;
    if (!agg.latest) {
      agg.latest = {
        exercise_name: exerciseName.get(activity.exercise_id) ?? 'Unknown exercise',
        equipment_name: equipmentName.get(usage.equipment_id) ?? null,
        set_index: set.set_index,
        rep_count: set.rep_count,
        in_progress: set.ended_at === null,
        verified: set.verified,
        started_at: set.started_at,
      };
    }
    perSession.set(sessionId, agg);
  }

  const rows: LiveSessionRow[] = sessions.map((s) => {
    const agg = perSession.get(s.id);
    return {
      id: s.id,
      member_id: s.member_id,
      member_name: s.members?.full_name ?? 'Unknown member',
      member_code: s.members?.member_code ?? '',
      check_in_at: s.check_in_at,
      check_in_method: s.check_in_method,
      check_in_confidence: s.check_in_confidence,
      sets_total: agg?.sets ?? 0,
      reps_total: agg?.reps ?? 0,
      latest_set: agg?.latest ?? null,
    };
  });
  return { rows, error: null };
}
