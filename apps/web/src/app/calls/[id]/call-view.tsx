"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import type { CallDetail, CallEventRow, LiveMessage, Role } from "@voice-lab/shared";
import { BrowserVoicePanel } from "@/components/browser-voice-panel";
import { Card, StatusBadge } from "@/components/ui";
import { STATUS_LABEL, duration, num, usd } from "@/lib/format";

interface Line {
  key: string;
  role: Role;
  text: string;
  final: boolean;
  interrupted?: boolean;
}

const TERMINAL = new Set(["completed", "failed", "busy", "no-answer", "canceled"]);
const OPS = ["stt", "llm", "tts", "telephony"] as const;

export function CallView({ id, wsBase }: { id: string; wsBase: string }) {
  const [detail, setDetail] = useState<CallDetail | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [live, setLive] = useState<Line[]>([]);
  const [liveEvents, setLiveEvents] = useState<CallEventRow[]>([]);
  const [connected, setConnected] = useState(false);
  const [now, setNow] = useState(() => Date.now());
  const refetchTimer = useRef<ReturnType<typeof setTimeout> | null>(null);

  const load = useCallback(async () => {
    const res = await fetch(`/api/lab/calls/${id}`);
    if (!res.ok) return setError((await res.json()).detail ?? "failed to load");
    const data: CallDetail = await res.json();
    setDetail(data);
    setLiveEvents([]);
  }, [id]);

  const scheduleRefetch = useCallback(() => {
    if (refetchTimer.current) clearTimeout(refetchTimer.current);
    refetchTimer.current = setTimeout(load, 400);
  }, [load]);

  useEffect(() => {
    load();
  }, [load]);

  const token = detail?.events_token;
  useEffect(() => {
    if (!token) return;
    const ws = new WebSocket(`${wsBase}/api/calls/${id}/live?token=${token}`);
    ws.onopen = () => setConnected(true);
    ws.onclose = () => setConnected(false);
    ws.onmessage = (e) => {
      const msg = JSON.parse(e.data) as LiveMessage;
      if (msg.type === "transcript") {
        setLive((lines) => {
          const key = `${msg.correlation_id}:${msg.role}`;
          const next = lines.filter((l) => l.key !== key);
          return [...next, { key, role: msg.role, text: msg.text, final: msg.final, interrupted: msg.interrupted }];
        });
        if (msg.final) scheduleRefetch();
      } else {
        setLiveEvents((ev) => [...ev, { ...msg, id: `${msg.name}-${msg.timestamp}` }]);
        if (msg.name.startsWith("call.") || msg.name.startsWith("session.") || msg.name === "usage.recorded") scheduleRefetch();
      }
    };
    return () => ws.close();
  }, [token, id, wsBase, scheduleRefetch]);

  const call = detail?.call;
  const active = call && !TERMINAL.has(call.status);
  useEffect(() => {
    if (!active) return;
    const t = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(t);
  }, [active]);

  // Persisted messages are the source of truth; live lines fill the gap until refetch.
  const lines: Line[] = useMemo(() => {
    const persisted: Line[] = (detail?.messages ?? []).map((m) => ({ key: m.id, role: m.role, text: m.text, final: true, interrupted: m.interrupted }));
    const known = new Set(persisted.map((p) => `${p.role}:${p.text}`));
    return [...persisted, ...live.filter((l) => !known.has(`${l.role}:${l.text}`))];
  }, [detail, live]);

  const events = useMemo(() => {
    const persisted = detail?.events ?? [];
    const seen = new Set(persisted.map((e) => `${e.name}-${e.timestamp}`));
    return [...persisted, ...liveEvents.filter((e) => !seen.has(`${e.name}-${e.timestamp}`))];
  }, [detail, liveEvents]);

  if (error) return <Card title="Error">{error}</Card>;
  if (!detail || !call) return <p className="text-sm text-muted">Loading…</p>;

  const cfg = call.config_snapshot;
  const elapsed = call.duration_seconds ?? (call.answered_at ? Math.max(0, (new Date(call.ended_at ?? now).getTime() - new Date(call.answered_at).getTime()) / 1000) : null);
  const byOp = detail.cost_by_operation;
  const total = Object.values(byOp).reduce((s, o) => s + Number(o.cost_usd), 0);
  const tokens = byOp.llm ?? { input_tokens: 0, output_tokens: 0, cached_tokens: 0 };
  const metrics = detail.sessions[0]?.metrics as Record<string, number | string | null> | undefined;
  const failures = events.filter((e) => e.name.endsWith(".failed") || e.name.includes("limit_reached"));

  async function hangup() {
    await fetch(`/api/lab/calls/${id}/hangup`, { method: "POST" });
  }

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center gap-3">
        <h1 className="text-xl font-semibold">{call.to_number ?? (call.direction === "browser" ? "Browser session" : call.direction)}</h1>
        <StatusBadge status={call.status} />
        <span className={`text-xs ${connected ? "text-green-600" : "text-muted"}`}>● {connected ? "live" : "offline"}</span>
        {active && call.direction !== "browser" && (
          <button onClick={hangup} className="ml-auto rounded-md border border-red-500/50 px-3 py-1 text-sm text-red-600">
            Hang up
          </button>
        )}
      </div>

      {call.direction === "browser" && (call.status === "ready" || call.status === "in-progress") && (
        <BrowserVoicePanel callId={id} wsBase={wsBase} onEnded={scheduleRefetch} />
      )}

      <div className="grid gap-4 lg:grid-cols-3">
        <Card title="Call" className="lg:col-span-1">
          <dl className="grid grid-cols-2 gap-y-1 text-sm">
            <dt className="text-muted">Duration</dt>
            <dd className="tabular-nums">{duration(elapsed)}</dd>
            <dt className="text-muted">Engine</dt>
            <dd>{cfg.engine}</dd>
            <dt className="text-muted">Voice provider</dt>
            <dd>{cfg.voice_provider}</dd>
            <dt className="text-muted">LLM</dt>
            <dd>
              {cfg.llm_provider}:{cfg.llm_model}
            </dd>
            <dt className="text-muted">STT</dt>
            <dd>{cfg.stt_model}</dd>
            <dt className="text-muted">TTS</dt>
            <dd>
              {cfg.tts_model} ({cfg.tts_voice})
            </dd>
          </dl>
          {call.error && <p className="mt-3 rounded bg-red-500/10 p-2 text-xs text-red-600">{call.error}</p>}
        </Card>

        <Card title="Usage & cost" className="lg:col-span-1">
          <dl className="grid grid-cols-2 gap-y-1 text-sm">
            <dt className="text-muted">Input tokens</dt>
            <dd className="tabular-nums">{num(tokens.input_tokens)}</dd>
            <dt className="text-muted">Cached tokens</dt>
            <dd className="tabular-nums">{num(tokens.cached_tokens)}</dd>
            <dt className="text-muted">Output tokens</dt>
            <dd className="tabular-nums">{num(tokens.output_tokens)}</dd>
            {OPS.map((op) => (
              <div key={op} className="contents">
                <dt className="text-muted">{op.toUpperCase()} cost</dt>
                <dd className="tabular-nums">
                  {byOp[op] ? usd(byOp[op].cost_usd, 5) : "—"}{" "}
                  {byOp[op] && <span className="text-xs text-muted">({byOp[op].statuses.map((s) => STATUS_LABEL[s]).join(", ")})</span>}
                </dd>
              </div>
            ))}
            <dt className="mt-2 font-semibold">Total (known)</dt>
            <dd className="mt-2 font-semibold tabular-nums">{usd(total, 5)}</dd>
          </dl>
        </Card>

        <Card title="Latency (server-side)" className="lg:col-span-1">
          {metrics ? (
            <dl className="grid grid-cols-2 gap-y-1 text-sm">
              {[
                ["Turns", metrics.turns],
                ["STT avg", metrics.avg_stt_ms],
                ["LLM first token", metrics.avg_llm_ttft_ms],
                ["LLM total", metrics.avg_llm_ms],
                ["TTS first byte", metrics.avg_tts_first_byte_ms],
                ["Speech end → first audio", metrics.avg_time_to_first_audio_ms],
              ].map(([label, value]) => (
                <div key={String(label)} className="contents">
                  <dt className="text-muted">{label}</dt>
                  <dd className="tabular-nums">{value ?? "—"}{typeof value === "number" && label !== "Turns" ? " ms" : ""}</dd>
                </div>
              ))}
            </dl>
          ) : (
            <p className="text-sm text-muted">Available when the session ends. Live per-step timings are in the timeline.</p>
          )}
        </Card>
      </div>

      <div className="grid gap-4 lg:grid-cols-2">
        <Card title="Conversation">
          <div className="space-y-2">
            {lines.length === 0 && <p className="text-sm text-muted">Waiting for audio…</p>}
            {lines.map((l) => (
              <div key={l.key} className={`text-sm ${l.final ? "" : "opacity-60"}`}>
                <span className={`mr-2 font-semibold ${l.role === "agent" ? "text-blue-600" : l.role === "user" ? "text-green-600" : "text-muted"}`}>
                  {l.role === "agent" ? "AI" : l.role === "user" ? "User" : "System"}:
                </span>
                {l.text}
                {l.interrupted && <span className="ml-2 text-xs text-amber-600">(interrupted)</span>}
              </div>
            ))}
            {failures.map((f) => (
              <div key={f.id} className="text-xs text-red-600">
                System: {f.name} {JSON.stringify(f.data)}
              </div>
            ))}
          </div>
        </Card>

        <Card title={`Timeline (${events.length} events)`}>
          <ol className="max-h-[32rem] space-y-1 overflow-y-auto font-mono text-xs">
            {events.map((e) => (
              <li key={e.id} className="flex gap-2">
                <span className="text-muted">{new Date(e.timestamp).toLocaleTimeString(undefined, { hour: "2-digit", minute: "2-digit", second: "2-digit", hour12: false, fractionalSecondDigits: 3 })}</span>
                <span className={e.name.endsWith("failed") ? "text-red-600" : ""}>{e.name}</span>
                {e.duration_ms !== null && <span className="text-muted">{e.duration_ms} ms</span>}
                {e.model && <span className="text-muted">{e.model}</span>}
              </li>
            ))}
          </ol>
        </Card>
      </div>
    </div>
  );
}
