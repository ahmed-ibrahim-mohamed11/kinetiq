export default function DashboardLoading() {
  return (
    <div className="space-y-6 animate-pulse" aria-busy="true" aria-label="Loading page content">
      <div className="space-y-2">
        <div className="h-8 w-48 rounded-md bg-muted/60" />
        <div className="h-4 w-96 max-w-full rounded-md bg-muted/40" />
      </div>

      <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-4">
        {[1, 2, 3, 4].map((i) => (
          <div key={i} className="h-28 rounded-xl border border-border/50 bg-card/60 p-4 space-y-3">
            <div className="flex items-center justify-between">
              <div className="h-4 w-24 rounded bg-muted/60" />
              <div className="h-4 w-4 rounded-full bg-muted/40" />
            </div>
            <div className="h-7 w-16 rounded bg-muted/80" />
            <div className="h-3 w-32 rounded bg-muted/40" />
          </div>
        ))}
      </div>

      <div className="rounded-xl border border-border/50 bg-card/60 p-6 space-y-4">
        <div className="h-5 w-40 rounded bg-muted/60" />
        <div className="space-y-3 pt-2">
          {[1, 2, 3, 4, 5].map((i) => (
            <div key={i} className="flex items-center justify-between border-b border-border/30 pb-3">
              <div className="h-4 w-36 rounded bg-muted/60" />
              <div className="h-4 w-28 rounded bg-muted/40" />
              <div className="h-4 w-20 rounded bg-muted/50" />
              <div className="h-5 w-16 rounded-full bg-muted/60" />
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}

