import Link from "next/link";
import type { CallSummary, DashboardStats } from "@voice-lab/shared";
import { Card, Stat, StatusBadge } from "@/components/ui";
import { duration, num, usd } from "@/lib/format";
import { apiJson } from "@/lib/server-api";

export const dynamic = "force-dynamic";

export default async function DashboardPage() {
  const [stats, calls] = await Promise.all([apiJson<DashboardStats>("/api/dashboard"), apiJson<CallSummary[]>("/api/calls?limit=25")]);

  if (!stats || !calls) {
    return (
      <Card title="API unreachable">
        <p className="text-sm">
          Could not reach the API. Start it with <code>pnpm dev:api</code> and check <code>ADMIN_API_TOKEN</code> in <code>.env</code>.
        </p>
      </Card>
    );
  }

  const unpriced = stats.cost_breakdown.filter((c) => c.cost_status === "unavailable").reduce((n, c) => n + c.events, 0);
  const estimated = stats.cost_breakdown.some((c) => c.cost_status === "estimated");

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <h1 className="text-xl font-semibold">Dashboard</h1>
        <Link href="/calls/new" className="rounded-md bg-foreground px-3 py-1.5 text-sm text-background">
          Start a call
        </Link>
      </div>

      <div className="grid grid-cols-2 gap-3 md:grid-cols-4">
        <Stat label="Outbound calls" value={stats.calls.outbound} />
        <Stat label="Inbound calls" value={stats.calls.inbound} hint="Phase 5" />
        <Stat label="Browser sessions" value={stats.calls.browser} hint="Phase 4" />
        <Stat label="Active sessions" value={stats.active_sessions} />
        <Stat label="Total duration" value={duration(stats.total_duration_seconds)} />
        <Stat label="Input tokens" value={num(stats.tokens.input)} />
        <Stat label="Output tokens" value={num(stats.tokens.output)} />
        <Stat
          label="Known cost"
          value={usd(stats.cost_total_usd)}
          hint={[estimated && "includes estimates", unpriced > 0 && `${unpriced} unpriced items`].filter(Boolean).join(" · ") || "all calculated / provider-reported"}
        />
      </div>

      <Card title="Recent calls">
        {calls.length === 0 ? (
          <p className="text-sm text-muted">No calls yet.</p>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead className="text-left text-xs uppercase text-muted">
                <tr>
                  <th className="py-2">When</th>
                  <th>Direction</th>
                  <th>Number</th>
                  <th>Engine / LLM</th>
                  <th>Status</th>
                  <th className="text-right">Duration</th>
                  <th className="text-right">Cost</th>
                </tr>
              </thead>
              <tbody>
                {calls.map((c) => (
                  <tr key={c.id} className="border-t border-border">
                    <td className="py-2">
                      <Link href={`/calls/${c.id}`} className="underline-offset-2 hover:underline">
                        {new Date(c.created_at).toLocaleString()}
                      </Link>
                    </td>
                    <td>{c.direction}</td>
                    <td className="tabular-nums">{c.to_number ?? c.from_number ?? "—"}</td>
                    <td>
                      {c.engine} / {c.llm_model}
                    </td>
                    <td>
                      <StatusBadge status={c.status} />
                    </td>
                    <td className="text-right tabular-nums">{duration(c.duration_seconds)}</td>
                    <td className="text-right tabular-nums">{usd(c.total_cost_usd)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Card>
    </div>
  );
}
