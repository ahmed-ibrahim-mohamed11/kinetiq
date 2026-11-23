export default function MembersLoading() {
  return (
    <div className="space-y-6 animate-pulse" aria-busy="true" aria-label="Loading members">
      <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
        <div className="space-y-2">
          <div className="h-8 w-44 rounded-md bg-muted/60" />
          <div className="h-4 w-72 rounded-md bg-muted/40" />
        </div>
        <div className="h-10 w-36 rounded-md bg-muted/70" />
      </div>

      <div className="flex gap-3">
        <div className="h-10 flex-1 rounded-md bg-muted/50" />
        <div className="h-10 w-28 rounded-md bg-muted/50" />
      </div>

      <div className="rounded-xl border border-border/50 bg-card/60 p-5 space-y-3">
        <div className="h-6 w-32 rounded bg-muted/60 mb-4" />
        {[1, 2, 3, 4, 6].map((i) => (
          <div key={i} className="flex items-center justify-between border-b border-border/30 pb-3.5 pt-1">
            <div className="space-y-1.5">
              <div className="h-4 w-40 rounded bg-muted/70" />
              <div className="h-3 w-24 rounded bg-muted/40" />
            </div>
            <div className="h-4 w-32 rounded bg-muted/50 hidden md:block" />
            <div className="h-5 w-20 rounded-full bg-muted/60" />
            <div className="h-8 w-20 rounded-md bg-muted/40" />
          </div>
        ))}
      </div>
    </div>
  );
}

