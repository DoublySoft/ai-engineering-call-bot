"use client";

import { useEffect, useRef, useState } from "react";
import { BrowserVoiceClient, type VoiceState } from "@/lib/voice-client";
import { Card } from "@/components/ui";

const LABEL: Record<VoiceState, string> = {
  idle: "Ready",
  connecting: "Connecting…",
  listening: "Listening — speak now",
  "agent-speaking": "Agent speaking",
  ended: "Session ended",
  error: "Error",
};

export function BrowserVoicePanel({ callId, wsBase, onEnded }: { callId: string; wsBase: string; onEnded: () => void }) {
  const client = useRef<BrowserVoiceClient | null>(null);
  const [state, setState] = useState<VoiceState>("idle");
  const [detail, setDetail] = useState<string | undefined>();
  const [level, setLevel] = useState(0);
  const [bargeIn, setBargeIn] = useState(false);

  useEffect(() => () => client.current?.stop(), []);

  async function start() {
    setDetail(undefined);
    const res = await fetch(`/api/lab/calls/${callId}/browser-token`, { method: "POST" });
    const body = await res.json();
    if (!res.ok) {
      setState("error");
      setDetail(body.detail);
      return;
    }
    client.current = new BrowserVoiceClient({
      url: `${wsBase}${body.path}`,
      outputSampleRate: body.output_sample_rate,
      allowBargeIn: bargeIn,
      onState: (s, d) => {
        setState(s);
        if (d) setDetail(d);
        if (s === "ended") onEnded();
      },
      onLevel: setLevel,
    });
    await client.current.start();
  }

  const active = state === "connecting" || state === "listening" || state === "agent-speaking";

  return (
    <Card title="Browser voice">
      <div className="flex flex-wrap items-center gap-4">
        {!active ? (
          <button
            onClick={start}
            disabled={state === "ended"}
            className="rounded-md bg-foreground px-4 py-2 text-sm font-medium text-background disabled:opacity-40"
          >
            Start talking
          </button>
        ) : (
          <button onClick={() => client.current?.stop()} className="rounded-md border border-red-500/50 px-4 py-2 text-sm text-red-600">
            End session
          </button>
        )}
        <span className={`text-sm ${state === "error" ? "text-red-600" : state === "agent-speaking" ? "text-blue-600" : ""}`}>
          {LABEL[state]}
          {detail ? ` — ${detail}` : ""}
        </span>
        <div className="h-2 w-32 overflow-hidden rounded bg-border" aria-label="microphone level">
          <div className="h-full bg-green-500 transition-[width]" style={{ width: `${Math.min(100, level * 400)}%` }} />
        </div>
        <label className="flex items-center gap-2 text-xs text-muted">
          <input type="checkbox" checked={bargeIn} disabled={active} onChange={(e) => setBargeIn(e.target.checked)} />
          Allow interrupting the agent (use headphones)
        </label>
      </div>
      <p className="mt-2 text-xs text-muted">
        One connection per session. The agent replies after ~0.7 s of silence. Without headphones, your mic is muted while the agent speaks so it
        doesn&apos;t hear itself.
      </p>
    </Card>
  );
}
