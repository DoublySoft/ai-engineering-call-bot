export function Card({ title, children, className = "" }: { title?: string; children: React.ReactNode; className?: string }) {
  return (
    <section className={`rounded-lg border border-border bg-card p-4 ${className}`}>
      {title && <h2 className="mb-3 text-xs font-semibold uppercase tracking-wide text-muted">{title}</h2>}
      {children}
    </section>
  );
}

export function Stat({ label, value, hint }: { label: string; value: React.ReactNode; hint?: string }) {
  return (
    <div className="rounded-lg border border-border bg-card p-4">
      <div className="text-xs uppercase tracking-wide text-muted">{label}</div>
      <div className="mt-1 text-2xl font-semibold tabular-nums">{value}</div>
      {hint && <div className="mt-1 text-xs text-muted">{hint}</div>}
    </div>
  );
}

const STATUS_COLOR: Record<string, string> = {
  completed: "bg-neutral-500/15 text-neutral-500",
  "in-progress": "bg-green-500/15 text-green-600",
  ringing: "bg-amber-500/15 text-amber-600",
  initiated: "bg-amber-500/15 text-amber-600",
  queued: "bg-amber-500/15 text-amber-600",
  ready: "bg-blue-500/15 text-blue-600",
  failed: "bg-red-500/15 text-red-600",
  busy: "bg-red-500/15 text-red-600",
  "no-answer": "bg-red-500/15 text-red-600",
  canceled: "bg-red-500/15 text-red-600",
};

export function StatusBadge({ status }: { status: string }) {
  return <span className={`rounded px-2 py-0.5 text-xs font-medium ${STATUS_COLOR[status] ?? "bg-neutral-500/15"}`}>{status}</span>;
}
