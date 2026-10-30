import Link from 'next/link';
import { buttonVariants } from '@smartgym/ui';

export default function NotFound() {
  return (
    <main className="flex min-h-[60vh] flex-col items-center justify-center gap-4 p-8 text-center">
      <p className="text-5xl font-semibold tabular-nums">404</p>
      <p className="text-muted-foreground">That record does not exist or is outside your branch access.</p>
      <Link href="/dashboard" className={buttonVariants({ variant: 'outline' })}>
        Back to dashboard
      </Link>
    </main>
  );
}
