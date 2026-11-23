export default function MemberAppLoading() {
  return (
    <div className="space-y-6 animate-pulse p-4 max-w-3xl mx-auto" aria-busy="true" aria-label="Loading member portal">
      <div className="flex items-center justify-between">
        <div className="space-y-2">
          <div className="h-6 w-36 rounded-md bg-muted/70" />
          <div className="h-3.5 w-48 rounded bg-muted/40" />
        </div>
        <div className="h-10 w-10 rounded-full bg-muted/50" />
      </div>

      <div className="h-48 rounded-2xl border border-border/50 bg-card/60 p-5 space-y-4">
        <div className="flex justify-between items-start">
          <div className="space-y-2">
            <div className="h-5 w-40 rounded bg-muted/80" />
            <div className="h-3.5 w-28 rounded bg-muted/50" />
          </div>
          <div className="h-6 w-20 rounded-full bg-muted/60" />
        </div>
        <div className="flex justify-center py-2">
          <div className="h-24 w-24 rounded-lg bg-muted/50" />
        </div>
      </div>

      <div className="grid gap-3 sm:grid-cols-2">
        <div className="h-32 rounded-xl border border-border/50 bg-card/60 p-4 space-y-3">
          <div className="h-4 w-28 rounded bg-muted/60" />
          <div className="h-7 w-20 rounded bg-muted/80" />
          <div className="h-3 w-36 rounded bg-muted/40" />
        </div>
        <div className="h-32 rounded-xl border border-border/50 bg-card/60 p-4 space-y-3">
          <div className="h-4 w-32 rounded bg-muted/60" />
          <div className="h-7 w-24 rounded bg-muted/80" />
          <div className="h-3 w-40 rounded bg-muted/40" />
        </div>
      </div>
    </div>
  );
}

