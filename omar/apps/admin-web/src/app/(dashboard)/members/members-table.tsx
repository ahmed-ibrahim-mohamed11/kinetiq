'use client';

import Link from 'next/link';
import { useMemo, useState } from 'react';
import { Badge, Card, CardContent, EmptyState, Input, Table, TableBody, TableCell, TableHead, TableHeader, TableRow, formatDateTime, statusVariant } from '@smartgym/ui';
import type { MemberListRow } from './types';

type Row = MemberListRow & { branch_name: string };

export function MembersTable({ members }: { members: Row[] }) {
  const [query, setQuery] = useState('');
  const filtered = useMemo(() => {
    const q = query.trim().toLowerCase();
    if (!q) return members;
    return members.filter(
      (m) => m.full_name.toLowerCase().includes(q) || m.member_code.toLowerCase().includes(q) || (m.phone ?? '').includes(q) || (m.email ?? '').toLowerCase().includes(q),
    );
  }, [members, query]);

  return (
    <Card>
      <CardContent className="pt-6">
        <div className="mb-4 flex items-center gap-3">
          <Input
            type="search"
            placeholder="Search by name, member code, phone or email"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            aria-label="Search members"
            className="max-w-md"
          />
          <span className="text-xs text-muted-foreground">
            {filtered.length} of {members.length}
          </span>
        </div>
        {members.length === 0 ? (
          <EmptyState title="No members yet" description="Create the first member with the form on the right." />
        ) : filtered.length === 0 ? (
          <EmptyState title="No matches" description="Try a different name or member code." />
        ) : (
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>Name</TableHead>
                <TableHead>Code</TableHead>
                <TableHead>Branch</TableHead>
                <TableHead>Contact</TableHead>
                <TableHead>Status</TableHead>
                <TableHead>Joined</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {filtered.map((m) => (
                <TableRow key={m.id}>
                  <TableCell>
                    <Link href={`/members/${m.id}`} className="font-medium hover:underline">
                      {m.full_name}
                    </Link>
                  </TableCell>
                  <TableCell className="font-mono text-xs">{m.member_code}</TableCell>
                  <TableCell>{m.branch_name}</TableCell>
                  <TableCell className="text-sm text-muted-foreground">
                    {m.phone ?? '—'}
                    {m.email && <div className="truncate text-xs">{m.email}</div>}
                  </TableCell>
                  <TableCell>
                    <Badge variant={statusVariant(m.status)}>{m.status}</Badge>
                  </TableCell>
                  <TableCell className="text-sm text-muted-foreground">{formatDateTime(m.created_at)}</TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        )}
      </CardContent>
    </Card>
  );
}
