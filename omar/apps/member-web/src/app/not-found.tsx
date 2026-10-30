import Link from 'next/link';
import { Button } from '@smartgym/ui';

export default function NotFound() {
  return (
    <main className="flex min-h-dvh flex-col items-center justify-center gap-4 px-4 text-center">
      <p className="text-5xl font-semibold tabular-nums">404</p>
      <p className="text-muted-foreground">This page or report does not exist, or the link has expired.</p>
      <Button variant="outline">
        <Link href="/dashboard">Go to Home</Link>
      </Button>
    </main>
  );
}
