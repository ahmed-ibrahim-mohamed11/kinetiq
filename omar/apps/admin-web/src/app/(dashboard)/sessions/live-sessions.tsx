'use client';

import Link from 'next/link';
import { useActionState, useEffect, useRef, useState } from 'react';
import { useRouter } from 'next/navigation';
import {
  Alert,
  Badge,
  Button,
  Card,
  CardContent,
  EmptyState,
  LiveDot,
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
  formatDateTime,
  formatTime,
  percent,
} from '@smartgym/ui';
import { createClient } from '@smartgym/supabase-client/browser';
import { staffCheckOut } from '@/lib/actions';
import type { ActionResult } from '@/lib/action-result';
import type { LiveSessionRow } from '@/lib/sessions-data';
import { CameraVisionMonitor } from './camera-vision-monitor';

type RealtimeState = 'connecting' | 'live' | 'offline';

export function LiveSessions({ initial, loadedAt }: { initial: LiveSessionRow[]; loadedAt: string }) {
  const router = useRouter();
  const [rt, setRt] = useState<RealtimeState>('connecting');
  const refreshTimer = useRef<ReturnType<typeof setTimeout> | null>(null);

  useEffect(() => {
    const supabase = createClient();
    const scheduleRefresh = () => {
      // Debounce bursts of rep events into one server refresh.
      if (refreshTimer.current) clearTimeout(refreshTimer.current);
      refreshTimer.current = setTimeout(() => router.refresh(), 400);
    };
    const channel = supabase
      .channel('admin-live-sessions')
      .on('postgres_changes', { event: '*', schema: 'public', table: 'attendance_sessions' }, scheduleRefresh)
      .on('postgres_changes', { event: '*', schema: 'public', table: 'exercise_sets' }, scheduleRefresh)
      .subscribe((status) => {
        if (status === 'SUBSCRIBED') setRt('live');
        else if (status === 'CHANNEL_ERROR' || status === 'TIMED_OUT' || status === 'CLOSED') setRt('offline');
      });
    // Safety net: periodic refresh in case realtime is unavailable.
    const interval = setInterval(() => router.refresh(), 30_000);
    return () => {
      clearInterval(interval);
      if (refreshTimer.current) clearTimeout(refreshTimer.current);
      void supabase.removeChannel(channel);
    };
  }, [router]);

  return (
    <div className="space-y-6">
      <CameraVisionMonitor activeSessions={initial} />
      <Card>
        <CardContent className="pt-6">
          <div className="mb-4 flex items-center justify-between text-xs text-muted-foreground">
            <span className="flex items-center gap-2">
              <LiveDot active={rt === 'live'} />
              {rt === 'live' ? 'Realtime connected' : rt === 'connecting' ? 'Connecting to realtime…' : 'Realtime unavailable — refreshing every 30 s'}
            </span>
            <span>Updated {formatTime(loadedAt)}</span>
          </div>
          {initial.length === 0 ? (
            <EmptyState title="Nobody is checked in" description="Active sessions appear here the moment a member is identified at the entrance or scans their QR code." />
          ) : (
            <Table>
            <TableHeader>
              <TableRow>
                <TableHead>Member</TableHead>
                <TableHead>Checked in</TableHead>
                <TableHead>Method</TableHead>
                <TableHead>Current / last set</TableHead>
                <TableHead className="text-right">Sets · Reps</TableHead>
                <TableHead className="text-right">Actions</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {initial.map((row) => (
                <SessionRow key={row.id} row={row} />
              ))}
            </TableBody>
          </Table>
        )}
      </CardContent>
    </Card>
  </div>
  );
}

function SessionRow({ row }: { row: LiveSessionRow }) {
  const [state, action, pending] = useActionState<ActionResult | null, FormData>(staffCheckOut, null);
  const latest = row.latest_set;
  return (
    <TableRow>
      <TableCell>
        <Link href={`/members/${row.member_id}`} className="font-medium hover:underline">
          {row.member_name}
        </Link>
        {row.member_code && <div className="text-xs text-muted-foreground">{row.member_code}</div>}
      </TableCell>
      <TableCell>{formatDateTime(row.check_in_at)}</TableCell>
      <TableCell>
        <Badge variant="outline">{row.check_in_method}</Badge>
        {row.check_in_method === 'face' && row.check_in_confidence !== null && (
          <span className="ml-2 text-xs text-muted-foreground">{percent(row.check_in_confidence)}</span>
        )}
      </TableCell>
      <TableCell>
        {latest ? (
          <div className="flex flex-col">
            <span className="flex items-center gap-2 font-medium">
              <LiveDot active={latest.in_progress} />
              {latest.exercise_name}
              {latest.equipment_name && <span className="text-xs font-normal text-muted-foreground">· {latest.equipment_name}</span>}
            </span>
            <span className="text-xs text-muted-foreground">
              Set {latest.set_index} · {latest.rep_count} reps {latest.in_progress ? '(in progress)' : ''}
              {!latest.verified && !latest.in_progress && <span className="ml-1 text-warning">· unverified</span>}
            </span>
          </div>
        ) : (
          <span className="text-sm text-muted-foreground">No exercise detected yet</span>
        )}
      </TableCell>
      <TableCell className="text-right tabular-nums">
        {row.sets_total} · {row.reps_total}
      </TableCell>
      <TableCell className="text-right">
        <form action={action} className="inline-flex flex-col items-end gap-1">
          <input type="hidden" name="attendance_session_id" value={row.id} />
          <input type="hidden" name="member_id" value={row.member_id} />
          <Button type="submit" size="sm" variant="outline" disabled={pending}>
            {pending ? 'Checking out…' : 'Check out'}
          </Button>
          {state && !state.ok && (
            <div className="max-w-xs text-left">
              <Alert variant="error">{state.error}</Alert>
            </div>
          )}
        </form>
      </TableCell>
    </TableRow>
  );
}
