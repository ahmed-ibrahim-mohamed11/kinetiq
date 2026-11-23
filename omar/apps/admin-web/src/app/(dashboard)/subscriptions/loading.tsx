export default function SubscriptionsLoading() {
  return (
    <div className="space-y-6 animate-pulse" aria-busy="true" aria-label="Loading subscriptions">
      <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
        <div className="space-y-2">
          <div className="h-8 w-56 rounded-md bg-muted/60" />
          <div className="h-4 w-80 rounded-md bg-muted/40" />
        </div>
        <div className="flex gap-2">
          <div className="h-10 w-32 rounded-md bg-muted/70" />
          <div className="h-10 w-32 rounded-md bg-muted/50" />
        </div>
      </div>

      <div className="grid gap-4 sm:grid-cols-3">
        {[1, 2, 3].map((i) => (
          <div key={i} className="h-44 rounded-xl border border-border/50 bg-card/60 p-5 space-y-3">
            <div className="h-5 w-32 rounded bg-muted/70" />
            <div className="h-8 w-24 rounded bg-muted/90" />
            <div className="h-3 w-40 rounded bg-muted/40" />
            <div className="h-3 w-48 rounded bg-muted/40" />
          </div>
        ))}
      </div>

      <div className="rounded-xl border border-border/50 bg-card/60 p-5 space-y-3">
        <div className="h-6 w-40 rounded bg-muted/60 mb-4" />
        {[1, 2, 3, 4].map((i) => (
          <div key={i} className="flex items-center justify-between border-b border-border/30 pb-3">
            <div className="h-4 w-36 rounded bg-muted/60" />
            <div className="h-4 w-28 rounded bg-muted/40" />
            <div className="h-4 w-20 rounded bg-muted/50" />
            <div className="h-5 w-16 rounded-full bg-muted/60" />
          </div>
        ))}
      </div>
    </div>
  );
}

