import type { Analytics, ArchitectureStats } from "@voice-lab/shared";
import { Card } from "@/components/ui";
import { duration, num, usd } from "@/lib/format";
import { apiJson } from "@/lib/server-api";

export const dynamic = "force-dynamic";

const SOURCE: Record<string, string> = { measured: "measured", provider_reported: "reported by Retell" };

function label(r: ArchitectureStats): string {
  return `${r.architecture} · ${r.model}`;
}

/** One measure, one hue, bars from a zero baseline; values are direct-labeled in text ink. */
function BarChart({
  title,
  rows,
  value,
  format,
  note,
}: {
  title: string;
  rows: ArchitectureStats[];
  value: (r: ArchitectureStats) => number | null;
  format: (v: number, r: ArchitectureStats) => string;
  note: string;
}) {
  const present = rows.filter((r) => value(r) !== null);
  const max = Math.max(...present.map((r) => value(r) as number)) || 1; // scale to the data; costs are often < $0.01
  return (
    <Card title={title}>
      {present.length === 0 ? (
        <p className="text-sm text-muted">No data yet.</p>
      ) : (
        <div className="space-y-3">
          {present.map((r) => {
            const v = value(r) as number;
            return (
              <div key={label(r)} className="text-sm" title={`${label(r)}: ${format(v, r)}`}>
                <div className="flex justify-between gap-3">
                  <span className="truncate text-muted">{label(r)}</span>
                  <span className="whitespace-nowrap tabular-nums">{format(v, r)}</span>
                </div>
                <div className="mt-1 h-2.5 w-full">
                  <div className="h-full rounded-r bg-blue-500/80" style={{ width: `${Math.max(1, (v / max) * 100)}%` }} />
                </div>
              </div>
            );
          })}
        </div>
      )}
      <p className="mt-3 text-xs text-muted">{note}</p>
    </Card>
  );
}

export default async function AnalyticsPage({ searchParams }: { searchParams: Promise<{ days?: string }> }) {
  const days = Number((await searchParams).days ?? 30) || 30;
  const data = await apiJson<Analytics>(`/api/analytics?days=${days}`);
  if (!data) {
    return (
      <Card title="API unreachable">
        <p className="text-sm">Could not reach the API. Is <code>pnpm dev:api</code> running?</p>
      </Card>
    );
  }
  const rows = data.by_architecture;
  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center gap-3">
        <h1 className="text-xl font-semibold">Compare architectures</h1>
        <div className="ml-auto flex gap-1 text-sm">
          {[1, 7, 30, 90].map((d) => (
            <a
              key={d}
              href={`/analytics?days=${d}`}
              className={`rounded-md border px-2 py-1 ${d === days ? "border-foreground" : "border-border text-muted"}`}
            >
              {d === 1 ? "24 h" : `${d} days`}
            </a>
          ))}
        </div>
      </div>

      <div className="grid gap-4 lg:grid-cols-2">
        <BarChart
          title="End of speech → first agent audio (p50)"
          rows={rows}
          value={(r) => r.first_audio_ms.p50}
          format={(v, r) =>
            `${(v / 1000).toFixed(2)} s${r.first_audio_ms.p90 ? ` · p90 ${(r.first_audio_ms.p90 / 1000).toFixed(2)} s` : ""} · ${SOURCE[r.first_audio_ms.source ?? ""] ?? ""} (n=${r.first_audio_ms.samples})`
          }
          note="Lower is better. Server-side measure; network and playback not included."
        />
        <BarChart
          title="Cost per conversational turn"
          rows={rows}
          value={(r) => (r.cost_per_turn_usd ? Number(r.cost_per_turn_usd) : null)}
          format={(v, r) => `${usd(v, 4)}${r.cost_complete ? "" : " · incomplete"}`}
          note="Known cost ÷ user turns. “incomplete” = some usage had no price (see cost table)."
        />
      </div>

      <Card title="All figures">
        <div className="overflow-x-auto">
          <table className="w-full text-sm">
            <thead className="text-left text-xs uppercase text-muted">
              <tr>
                <th className="py-2">Architecture</th>
                <th>Model</th>
                <th className="text-right">Sessions</th>
                <th className="text-right">Turns</th>
                <th className="text-right">Duration</th>
                <th className="text-right">p50 / p90</th>
                <th className="text-right">STT / LLM 1st / TTS 1st</th>
                <th className="text-right">Cost</th>
                <th className="text-right">/ turn</th>
                <th className="text-right">/ min</th>
                <th className="text-right">Errors</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((r) => (
                <tr key={label(r)} className="border-t border-border">
                  <td className="py-2">{r.architecture}</td>
                  <td>{r.model}</td>
                  <td className="text-right tabular-nums">{r.sessions}</td>
                  <td className="text-right tabular-nums">{r.turns}</td>
                  <td className="text-right tabular-nums">{duration(r.duration_seconds)}</td>
                  <td className="text-right tabular-nums">
                    {r.first_audio_ms.p50 ?? "—"} / {r.first_audio_ms.p90 ?? "—"} ms
                  </td>
                  <td className="text-right tabular-nums">
                    {r.stages_ms.avg_stt_ms ?? "—"} / {r.stages_ms.avg_llm_ttft_ms ?? r.stages_ms.avg_llm_ms ?? "—"} / {r.stages_ms.avg_tts_first_byte_ms ?? "—"}
                  </td>
                  <td className="text-right tabular-nums">{usd(r.cost_usd)}</td>
                  <td className="text-right tabular-nums">{usd(r.cost_per_turn_usd)}</td>
                  <td className="text-right tabular-nums">{usd(r.cost_per_minute_usd)}</td>
                  <td className="text-right tabular-nums">{num(r.errors)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </Card>

      <div className="grid gap-4 lg:grid-cols-2">
        <Card title="Cost by provider and operation">
          <table className="w-full text-sm">
            <thead className="text-left text-xs uppercase text-muted">
              <tr>
                <th className="py-2">Provider</th>
                <th>Operation</th>
                <th className="text-right">Items</th>
                <th className="text-right">Cost</th>
                <th className="pl-4">Status</th>
              </tr>
            </thead>
            <tbody>
              {data.cost_by_provider.map((p) => (
                <tr key={`${p.provider}-${p.operation}`} className="border-t border-border">
                  <td className="py-2">{p.provider}</td>
                  <td>{p.operation}</td>
                  <td className="text-right tabular-nums">{p.events}</td>
                  <td className="text-right tabular-nums">{usd(p.cost_usd)}</td>
                  <td className="pl-4 text-xs text-muted">{p.statuses.join(", ")}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </Card>
        <Card title="How to read this">
          <ul className="list-disc space-y-1 pl-5 text-sm text-muted">
            {data.notes.map((n) => (
              <li key={n}>{n}</li>
            ))}
            <li>Spans for every STT/LLM/TTS/S2S step are exported to OpenTelemetry when OTEL_EXPORTER_OTLP_ENDPOINT is set.</li>
          </ul>
        </Card>
      </div>
    </div>
  );
}
