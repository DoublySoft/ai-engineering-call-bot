import type { CallSummary } from "@voice-lab/shared";
import { CallsTable } from "@/components/calls-table";
import { Card } from "@/components/ui";
import { apiJson } from "@/lib/server-api";

export const dynamic = "force-dynamic";

export default async function CallsPage() {
  const calls = await apiJson<CallSummary[]>("/api/calls?limit=200");
  return (
    <div className="space-y-4">
      <h1 className="text-xl font-semibold">Call history</h1>
      <Card>
        {calls ? <CallsTable calls={calls} /> : <p className="text-sm">Could not reach the API. Is <code>pnpm dev:api</code> running?</p>}
        {calls && calls.length === 200 && <p className="mt-3 text-xs text-muted">Showing the latest 200 calls.</p>}
      </Card>
    </div>
  );
}
