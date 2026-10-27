import Link from "next/link";
import type { Agent } from "@voice-lab/shared";
import { Card } from "@/components/ui";
import { apiJson } from "@/lib/server-api";

export const dynamic = "force-dynamic";

export default async function AgentsPage() {
  const agents = await apiJson<Agent[]>("/api/agents");
  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <h1 className="text-xl font-semibold">Agents</h1>
        <Link href="/agents/new" className="rounded-md bg-foreground px-3 py-1.5 text-sm text-background">
          New agent
        </Link>
      </div>
      <p className="text-sm text-muted">
        An agent is what the AI is told to be: its role, what it knows, how it greets and which models it uses by default. Every edit creates a
        new version; each call records the version it used.
      </p>
      {!agents ? (
        <Card>
          <p className="text-sm">Could not reach the API. Is <code>pnpm dev:api</code> running?</p>
        </Card>
      ) : (
        <div className="grid gap-3 md:grid-cols-2">
          {agents.map((a) => (
            <Link key={a.id} href={`/agents/${a.id}`} className="block rounded-lg border border-border bg-card p-4 hover:border-foreground/40">
              <div className="flex items-center gap-2">
                <span className="font-semibold">{a.name}</span>
                {a.is_default && <span className="rounded bg-blue-500/15 px-2 py-0.5 text-xs text-blue-600">default</span>}
                <span className="ml-auto text-xs text-muted">v{a.version}</span>
              </div>
              {a.description && <p className="mt-1 text-sm text-muted">{a.description}</p>}
              <p className="mt-2 line-clamp-2 text-sm">{a.settings.instructions}</p>
              <div className="mt-3 flex flex-wrap gap-x-4 gap-y-1 text-xs text-muted">
                <span>LLM: {a.settings.llm_model ?? "server default"}</span>
                <span>Voice: {a.settings.tts_voice ?? "server default"}</span>
                <span>Knowledge: {a.settings.knowledge ? `${a.settings.knowledge.length.toLocaleString()} chars` : "none"}</span>
                <span>{a.calls} calls</span>
              </div>
            </Link>
          ))}
        </div>
      )}
    </div>
  );
}
