"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import type { CallSummary } from "@voice-lab/shared";
import { StatusBadge } from "@/components/ui";
import { duration, usd } from "@/lib/format";

const DIRECTION: Record<string, string> = { outbound: "Outbound", inbound: "Inbound", browser: "Browser" };

/** Whole rows open the call detail; the explicit link keeps it keyboard/middle-click friendly. */
export function CallsTable({ calls }: { calls: CallSummary[] }) {
  const router = useRouter();
  if (calls.length === 0) return <p className="text-sm text-muted">No calls yet.</p>;
  return (
    <div className="overflow-x-auto">
      <table className="w-full text-sm">
        <thead className="text-left text-xs uppercase text-muted">
          <tr>
            <th className="py-2">When</th>
            <th>Type</th>
            <th>Number</th>
            <th>Engine / LLM</th>
            <th>Status</th>
            <th className="text-right">Duration</th>
            <th className="text-right">Cost</th>
            <th />
          </tr>
        </thead>
        <tbody>
          {calls.map((c) => (
            <tr
              key={c.id}
              onClick={() => router.push(`/calls/${c.id}`)}
              className="cursor-pointer border-t border-border hover:bg-foreground/5"
            >
              <td className="py-2">{new Date(c.created_at).toLocaleString()}</td>
              <td>{DIRECTION[c.direction] ?? c.direction}</td>
              <td className="tabular-nums">{c.to_number ?? c.from_number ?? "—"}</td>
              <td>
                {c.engine} / {c.llm_model}
              </td>
              <td>
                <StatusBadge status={c.status} />
              </td>
              <td className="text-right tabular-nums">{duration(c.duration_seconds)}</td>
              <td className="text-right tabular-nums">{usd(c.total_cost_usd)}</td>
              <td className="pl-3 text-right">
                <Link href={`/calls/${c.id}`} onClick={(e) => e.stopPropagation()} className="text-blue-600 hover:underline">
                  View →
                </Link>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
