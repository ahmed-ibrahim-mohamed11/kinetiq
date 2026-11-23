import type { Metadata } from 'next';
import { Alert, Badge, Card, CardContent, CardDescription, CardHeader, CardTitle, EmptyState, Table, TableBody, TableCell, TableHead, TableHeader, TableRow, formatDateTime } from '@smartgym/ui';
import { createClient } from '@smartgym/supabase-client/server';
import type { Tables } from '@smartgym/supabase-client/types';
import { PageHeader } from '@/components/page-header';

export const metadata: Metadata = { title: 'Audit log' };
export const dynamic = 'force-dynamic';

type AuditRow = Pick<Tables<'audit_logs'>, 'id' | 'occurred_at' | 'actor_user_id' | 'actor_role' | 'action' | 'table_name' | 'row_id'>;

function actionVariant(action: string): 'success' | 'warning' | 'destructive' | 'secondary' {
  switch (action) {
    case 'INSERT':
      return 'success';
    case 'UPDATE':
      return 'warning';
    case 'DELETE':
      return 'destructive';
    default:
      return 'secondary';
  }
}

export default async function AuditPage() {
  const supabase = await createClient();
  const { data, error } = await supabase
    .from('audit_logs')
    .select('id, occurred_at, actor_user_id, actor_role, action, table_name, row_id')
    .order('occurred_at', { ascending: false })
    .limit(100)
    .returns<AuditRow[]>();
  const rows = data ?? [];

  return (
    <>
      <PageHeader
        title="Audit log"
        description="Every change to members, consent, biometric references, staff roles, cameras and device credentials. Visible to organization owners and auditors."
      />
      {error && (
        <div className="mb-4">
          <Alert variant="error" title="Could not load audit log">
            {error.message}
          </Alert>
        </div>
      )}
      <Card>
        <CardHeader>
          <CardTitle>Last 100 entries</CardTitle>
          <CardDescription>Entries are written by database triggers and cannot be edited from this console.</CardDescription>
        </CardHeader>
        <CardContent>
          {rows.length === 0 ? (
            <EmptyState
              title="No audit entries visible"
              description="Either nothing has changed yet, or your role is not owner/auditor for this organization. Managers and front-desk staff do not see the audit log."
            />
          ) : (
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>When</TableHead>
                  <TableHead>Action</TableHead>
                  <TableHead>Table</TableHead>
                  <TableHead>Row</TableHead>
                  <TableHead>Actor</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {rows.map((r) => (
                  <TableRow key={r.id}>
                    <TableCell className="whitespace-nowrap text-sm">{formatDateTime(r.occurred_at)}</TableCell>
                    <TableCell>
                      <Badge variant={actionVariant(r.action)}>{r.action}</Badge>
                    </TableCell>
                    <TableCell className="font-mono text-xs">{r.table_name}</TableCell>
                    <TableCell className="font-mono text-xs text-muted-foreground">{r.row_id ?? 'â€”'}</TableCell>
                    <TableCell className="text-xs">
                      <div>{r.actor_role ?? 'â€”'}</div>
                      {r.actor_user_id && <div className="font-mono text-muted-foreground">{r.actor_user_id.slice(0, 8)}â€¦</div>}
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          )}
        </CardContent>
      </Card>
    </>
  );
}
