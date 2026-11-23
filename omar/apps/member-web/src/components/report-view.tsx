import type { WorkoutReportSummary } from '@smartgym/shared-types';
import { Alert, Badge, Card, CardContent, CardHeader, CardTitle, StatTile, Table, TableBody, TableCell, TableHead, TableHeader, TableRow, formatDateTime, formatDuration, formatTime, percent } from '@smartgym/ui';
import { UNCERTAIN_REASON_LABELS } from '@/lib/labels';

export function isWorkoutReportSummary(value: unknown): value is WorkoutReportSummary {
  if (!value || typeof value !== 'object') return false;
  const v = value as Record<string, unknown>;
  return v.schema_version === 1 && Array.isArray(v.exercises) && typeof v.check_in_at === 'string';
}

export function ReportView({ summary, memberName, generatedAt, timeZone }: { summary: WorkoutReportSummary; memberName: string; generatedAt: string; timeZone?: string }) {
  const tz = timeZone ?? 'Africa/Cairo';
  const uncertain = summary.uncertain_segments ?? [];
  const unverifiedSets = summary.exercises.reduce((acc, ex) => acc + ex.sets.filter((s) => s.confidence < 0.6).length, 0);

  return (
    <article className="space-y-6">
      <header className="space-y-1">
        <p className="text-sm text-muted-foreground">Workout report · {formatDateTime(summary.check_in_at, tz).split(',')[0]}</p>
        <h1 className="text-2xl font-semibold tracking-tight">{memberName}</h1>
        <p className="text-sm text-muted-foreground">
          {formatTime(summary.check_in_at, tz)} – {formatTime(summary.check_out_at, tz)} · generated {formatDateTime(generatedAt, tz)}
        </p>
      </header>

      <section className="grid grid-cols-2 gap-3 md:grid-cols-4" aria-label="Summary">
        <StatTile label="Visit" value={formatDuration(summary.visit_seconds)} hint="check-in to check-out" />
        <StatTile label="Active exercise" value={formatDuration(summary.active_exercise_seconds)} hint="time actually moving" />
        <StatTile label="Sets" value={summary.total_sets} />
        <StatTile label="Total reps" value={summary.total_reps} hint="volume = repetitions (no weights recorded)" data-testid="report-total-reps" />
      </section>

      {summary.exercises.length === 0 ? (
        <Alert variant="info" title="No exercises were recorded">
          The cameras did not observe you on any mapped equipment during this visit. Free-weight areas without zone mapping and cardio machines are counted as visit time only.
        </Alert>
      ) : (
        <section className="space-y-4" aria-label="Exercises">
          {summary.exercises.map((ex, i) => (
            <Card key={`${ex.exercise_slug}-${i}`} className="print:break-inside-avoid">
              <CardHeader className="pb-3">
                <div className="flex flex-wrap items-baseline justify-between gap-2">
                  <CardTitle className="text-base">{ex.exercise_name}</CardTitle>
                  <span className="text-sm tabular-nums text-muted-foreground">
                    {ex.sets.length} {ex.sets.length === 1 ? 'set' : 'sets'} · {ex.total_reps} reps
                  </span>
                </div>
                <dl className="grid grid-cols-3 gap-2 text-xs text-muted-foreground">
                  <div>
                    <dt>Equipment</dt>
                    <dd className="font-medium text-foreground">{ex.equipment_name ?? '—'}</dd>
                  </div>
                  <div>
                    <dt>On equipment</dt>
                    <dd className="font-medium text-foreground">{formatDuration(ex.equipment_seconds)}</dd>
                  </div>
                  <div>
                    <dt>Active</dt>
                    <dd className="font-medium text-foreground">{formatDuration(ex.active_seconds)}</dd>
                  </div>
                </dl>
              </CardHeader>
              <CardContent className="px-0 pb-0">
                <Table>
                  <TableHeader>
                    <TableRow>
                      <TableHead className="h-9 pl-6">Set</TableHead>
                      <TableHead className="h-9">Reps</TableHead>
                      <TableHead className="h-9">Rest before</TableHead>
                      <TableHead className="h-9">Confidence</TableHead>
                      <TableHead className="h-9 pr-6 text-right">Status</TableHead>
                    </TableRow>
                  </TableHeader>
                  <TableBody>
                    {ex.sets.map((s) => {
                      const verified = (s as { verified?: boolean }).verified ?? s.confidence >= 0.6;
                      return (
                        <TableRow key={s.set_index}>
                          <TableCell className="py-2 pl-6 tabular-nums">{s.set_index}</TableCell>
                          <TableCell className="py-2 font-semibold tabular-nums">{s.reps}</TableCell>
                          <TableCell className="py-2 text-muted-foreground">{s.rest_before_seconds == null ? '—' : formatDuration(s.rest_before_seconds)}</TableCell>
                          <TableCell className="py-2 tabular-nums">{percent(s.confidence)}</TableCell>
                          <TableCell className="py-2 pr-6 text-right">
                            {verified ? <Badge variant="success">verified</Badge> : <Badge variant="warning">unverified</Badge>}
                          </TableCell>
                        </TableRow>
                      );
                    })}
                  </TableBody>
                </Table>
              </CardContent>
            </Card>
          ))}
        </section>
      )}

      <section aria-label="Uncertain activity" className="space-y-3 print:break-inside-avoid">
        <h2 className="text-base font-semibold">Uncertain activity</h2>
        {uncertain.length === 0 && unverifiedSets === 0 ? (
          <p className="text-sm text-muted-foreground">The cameras tracked you continuously during this visit. Nothing was flagged.</p>
        ) : (
          <>
            <p className="text-sm text-muted-foreground">
              During the periods below the system could not follow you reliably. Repetitions during these periods were either not counted or are marked
              <span className="mx-1 inline-block align-middle">
                <Badge variant="warning">unverified</Badge>
              </span>
              above. We show them rather than guess.
            </p>
            {uncertain.length > 0 && (
              <ul className="divide-y rounded-lg border text-sm">
                {uncertain.map((seg, i) => (
                  <li key={i} className="flex flex-wrap items-baseline justify-between gap-2 px-4 py-2.5">
                    <span className="tabular-nums">
                      {formatTime(seg.from, tz)} – {formatTime(seg.to, tz)}
                    </span>
                    <span className="text-muted-foreground">{UNCERTAIN_REASON_LABELS[seg.reason] ?? seg.reason}</span>
                  </li>
                ))}
              </ul>
            )}
          </>
        )}
      </section>

      <footer className="text-xs text-muted-foreground">
        Counts are produced automatically from gym cameras. No video is stored and no weights are inferred; total volume is expressed as repetitions only.
      </footer>
    </article>
  );
}
