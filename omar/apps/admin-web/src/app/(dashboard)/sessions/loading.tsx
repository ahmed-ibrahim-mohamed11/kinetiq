export default function SessionsLoading() {
  return (
    <div className="space-y-6 animate-pulse" aria-busy="true" aria-label="Loading live sessions">
      <div className="space-y-2">
        <div className="h-8 w-44 rounded-md bg-muted/60" />
        <div className="h-4 w-96 max-w-full rounded-md bg-muted/40" />
      </div>

      <div className="grid gap-4 sm:grid-cols-3">
        {[1, 2, 3].map((i) => (
          <div key={i} className="h-28 rounded-xl border border-border/50 bg-card/60 p-4 space-y-3">
            <div className="h-4 w-28 rounded bg-muted/60" />
            <div className="h-7 w-16 rounded bg-muted/80" />
            <div className="h-3 w-36 rounded bg-muted/40" />
          </div>
        ))}
      </div>

      <div className="rounded-xl border border-border/50 bg-card/60 p-5 space-y-4">
        <div className="flex items-center justify-between">
          <div className="h-6 w-36 rounded bg-muted/60" />
          <div className="h-5 w-24 rounded-full bg-muted/50" />
        </div>
        <div className="h-64 rounded-lg bg-muted/30 border border-border/40 flex items-center justify-center">
          <div className="h-6 w-48 rounded bg-muted/50" />
        </div>
      </div>
    </div>
  );
}

