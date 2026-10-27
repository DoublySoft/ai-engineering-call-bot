"use client";

import Link from "next/link";
import { useCallback, useEffect, useState } from "react";
import type { TelephonyStatus } from "@voice-lab/shared";
import { Card } from "@/components/ui";

const SCOPE: Record<string, string> = { outbound: "outbound", inbound: "inbound", both: "both", optional: "optional" };

function Mark({ ok }: { ok: boolean | null }) {
  if (ok === null) return <span className="text-muted" aria-label="not checked">○</span>;
  return ok ? <span className="text-green-600" aria-label="ok">✓</span> : <span className="text-red-600" aria-label="failing">✗</span>;
}

export default function TelephonyPage() {
  const [status, setStatus] = useState<TelephonyStatus | null>(null);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState<{ ok: boolean; text: string } | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    const res = await fetch("/api/lab/telephony/status");
    setStatus(res.ok ? await res.json() : null);
    setLoading(false);
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  async function configure() {
    if (!status?.phone_number) return;
    if (!confirm(`Point ${status.phone_number}'s voice and status webhooks at ${status.webhooks.voice_url}?`)) return;
    setBusy(true);
    const res = await fetch("/api/lab/telephony/configure", { method: "POST" });
    const body = await res.json();
    setBusy(false);
    setMessage(res.ok ? { ok: true, text: `Configured ${body.phone_number}.` } : { ok: false, text: body.detail });
    load();
  }

  const canConfigure = !!status && ["twilio_env", "public_url", "credentials", "number"].every((k) => status.checks.find((c) => c.key === k)?.ok);

  return (
    <div className="mx-auto max-w-3xl space-y-4">
      <div className="flex items-center gap-3">
        <h1 className="text-xl font-semibold">Telephony</h1>
        <button onClick={load} disabled={loading} className="ml-auto rounded-md border border-border px-3 py-1.5 text-sm disabled:opacity-40">
          {loading ? "Checking…" : "Re-check"}
        </button>
      </div>

      {status && (
        <div className="grid gap-3 md:grid-cols-2">
          {(["outbound", "inbound"] as const).map((scope) => (
            <Card key={scope}>
              <div className="flex items-center gap-2">
                <Mark ok={status.ready[scope]} />
                <span className="font-semibold">{scope === "outbound" ? "Outbound calls" : "Inbound calls"}</span>
                <span className={`ml-auto text-sm ${status.ready[scope] ? "text-green-600" : "text-muted"}`}>
                  {status.ready[scope] ? "ready" : "not ready"}
                </span>
              </div>
            </Card>
          ))}
        </div>
      )}

      <Card title="Checklist">
        {!status ? (
          <p className="text-sm text-muted">{loading ? "Checking…" : "Could not reach the API."}</p>
        ) : (
          <ul className="space-y-2 text-sm">
            {status.checks.map((c) => (
              <li key={c.key} className="flex gap-3">
                <Mark ok={c.ok} />
                <div className="min-w-0">
                  <div>
                    {c.label} <span className="text-xs text-muted">· {SCOPE[c.needed_for]}</span>
                  </div>
                  {c.detail && <div className="truncate text-xs text-muted">{c.detail}</div>}
                </div>
              </li>
            ))}
          </ul>
        )}
      </Card>

      {status && (
        <Card title="Connect the number">
          <p className="text-sm">
            Sets the number&apos;s incoming-call webhook and call-status webhook in your Twilio account to this lab. Outbound calls don&apos;t need
            it; inbound calls do.
          </p>
          <dl className="mt-3 grid grid-cols-[8rem_1fr] gap-y-1 text-xs">
            <dt className="text-muted">Number</dt>
            <dd>{status.phone_number ?? "—"}</dd>
            <dt className="text-muted">Voice webhook</dt>
            <dd className="break-all font-mono">{status.webhooks.voice_url}</dd>
            <dt className="text-muted">Status webhook</dt>
            <dd className="break-all font-mono">{status.webhooks.status_callback}</dd>
          </dl>
          <button
            onClick={configure}
            disabled={!canConfigure || busy}
            className="mt-3 rounded-md bg-foreground px-4 py-2 text-sm font-medium text-background disabled:opacity-40"
          >
            {busy ? "Configuring…" : "Configure number"}
          </button>
          {!canConfigure && <p className="mt-2 text-xs text-muted">Available once the Twilio variables, credentials, number and https URL check out.</p>}
          {message && <p className={`mt-2 text-sm ${message.ok ? "text-green-600" : "text-red-600"}`}>{message.text}</p>}
        </Card>
      )}

      {status && (
        <Card title="Who answers inbound calls">
          <ul className="space-y-1 text-sm">
            {status.inbound_routing.by_number.map((r) => (
              <li key={r.number}>
                <span className="font-mono">{r.number}</span> →{" "}
                <Link href={`/agents/${r.agent_id}`} className="text-blue-600 hover:underline">
                  {r.agent}
                </Link>
              </li>
            ))}
            <li className="text-muted">
              Any other number → default agent ({status.inbound_routing.default_agent ?? "—"}). Set an agent&apos;s inbound number in its editor.
            </li>
            <li className="text-muted">
              Callers: {status.inbound_routing.allowlist.length ? `only ${status.inbound_routing.allowlist.join(", ")}` : "anyone (set INBOUND_ALLOWLIST to restrict)"}
            </li>
          </ul>
        </Card>
      )}

      <Card title="Steps once you have a number">
        <ol className="list-decimal space-y-1 pl-5 text-sm">
          <li>Buy a voice-capable number in Twilio (trial accounts can only call verified numbers).</li>
          <li>
            Set <code>TWILIO_ACCOUNT_SID</code>, <code>TWILIO_AUTH_TOKEN</code>, <code>TWILIO_PHONE_NUMBER</code> and <code>OUTBOUND_ALLOWLIST</code> in
            <code> .env</code>.
          </li>
          <li>
            Start a tunnel to the API (<code>ngrok http 8000</code>), put its https URL in <code>PUBLIC_BASE_URL</code> and restart the API.
          </li>
          <li>Press “Re-check”, then “Configure number”. Details: docs/telephony.md.</li>
        </ol>
      </Card>
    </div>
  );
}
