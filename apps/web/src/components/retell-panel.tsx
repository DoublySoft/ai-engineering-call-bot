"use client";

import { useEffect, useRef, useState } from "react";
import type { RetellWebCall } from "@voice-lab/shared";
import { Card } from "@/components/ui";

type State = "idle" | "connecting" | "live" | "agent-speaking" | "syncing" | "ended" | "error";
type Utterance = { role: "agent" | "user"; content: string };

const LABEL: Record<State, string> = {
  idle: "Ready",
  connecting: "Connecting to Retell…",
  live: "Live — speak now",
  "agent-speaking": "Agent speaking",
  syncing: "Call ended — importing Retell's report…",
  ended: "Session ended",
  error: "Error",
};

// Minimal surface of the (deprecated, 3.x) RetellWebClient we rely on; it joins with a
// server-issued access token, so the secret API key never reaches the browser.
interface WebClient {
  startCall(config: { accessToken: string; callId?: string; transport?: string; iceServers?: RTCIceServer[] }): Promise<void>;
  stopCall(): void;
  on(event: string, fn: (...args: never[]) => void): void;
  removeAllListeners(): void;
}

export function RetellPanel({ callId, onChanged }: { callId: string; onChanged: () => void }) {
  const client = useRef<WebClient | null>(null);
  const transcript = useRef<Utterance[]>([]);
  const started = useRef(false);
  const synced = useRef(false);
  const [state, setState] = useState<State>("idle");
  const [detail, setDetail] = useState<string>();
  const [live, setLive] = useState<Utterance[]>([]);

  useEffect(
    () => () => {
      client.current?.removeAllListeners();
      client.current?.stopCall();
      // Leaving the page mid-call: hand the live transcript to the server. keepalive lets the
      // request outlive the page; the server also finalises the call on its own regardless.
      if (started.current && !synced.current) {
        void fetch(`/api/lab/calls/${callId}/retell-sync`, {
          method: "POST",
          body: JSON.stringify({ transcript: transcript.current }),
          keepalive: true,
        });
      }
    },
    [callId],
  );

  async function sync() {
    synced.current = true;
    setState("syncing");
    const res = await fetch(`/api/lab/calls/${callId}/retell-sync`, { method: "POST", body: JSON.stringify({ transcript: transcript.current }) });
    const body = await res.json();
    setState(res.ok ? "ended" : "error");
    if (!res.ok) setDetail(body.detail);
    else if (body.background_sync) setDetail("Retell is still finalising the cost; it will appear shortly.");
    onChanged();
  }

  async function start() {
    setDetail(undefined);
    setState("connecting");
    const res = await fetch(`/api/lab/calls/${callId}/retell-web-call`, { method: "POST" });
    const web: RetellWebCall & { detail?: string } = await res.json();
    if (!res.ok) {
      setState("error");
      setDetail(web.detail);
      return;
    }
    started.current = true;
    const { RetellWebClient } = await import("retell-client-js-sdk");
    const c = new RetellWebClient() as unknown as WebClient;
    client.current = c;
    c.on("call_started", () => setState("live"));
    c.on("agent_start_talking", () => setState("agent-speaking"));
    c.on("agent_stop_talking", () => setState("live"));
    c.on("update", (update: { transcript?: Utterance[] }) => {
      if (!update?.transcript) return;
      transcript.current = update.transcript.filter((u) => u.role === "agent" || u.role === "user");
      setLive(transcript.current);
    });
    c.on("call_ended", () => void sync());
    c.on("error", (err: unknown) => {
      setState("error");
      setDetail(String((err as Error)?.message ?? err));
      c.stopCall();
    });
    try {
      await c.startCall({ accessToken: web.access_token, callId: web.call_id, transport: web.transport ?? undefined, iceServers: web.ice_servers });
    } catch (e) {
      setState("error");
      setDetail(`Could not start the Retell call: ${(e as Error).message}`);
    }
  }

  const active = state === "connecting" || state === "live" || state === "agent-speaking";
  return (
    <Card title="Retell voice (managed)">
      <div className="flex flex-wrap items-center gap-4">
        {!active ? (
          <button
            onClick={start}
            disabled={state === "ended" || state === "syncing"}
            className="rounded-md bg-foreground px-4 py-2 text-sm font-medium text-background disabled:opacity-40"
          >
            Start talking
          </button>
        ) : (
          <button onClick={() => client.current?.stopCall()} className="rounded-md border border-red-500/50 px-4 py-2 text-sm text-red-600">
            End call
          </button>
        )}
        <span className={`text-sm ${state === "error" ? "text-red-600" : state === "agent-speaking" ? "text-blue-600" : ""}`}>
          {LABEL[state]}
          {detail ? ` — ${detail}` : ""}
        </span>
        {(state === "ended" || state === "error") && (
          <button onClick={sync} className="text-sm text-blue-600 hover:underline">
            Re-sync from Retell
          </button>
        )}
      </div>
      {live.length > 0 && active && (
        <div className="mt-3 space-y-1 text-sm">
          {live.slice(-4).map((u, i) => (
            <p key={i}>
              <span className={`mr-2 font-semibold ${u.role === "agent" ? "text-blue-600" : "text-green-600"}`}>{u.role === "agent" ? "AI" : "User"}:</span>
              {u.content}
            </p>
          ))}
        </div>
      )}
      <p className="mt-2 text-xs text-muted">
        Retell handles the audio, turn-taking, STT, LLM and voice. Interruptions work natively. Cost, latency and transcript are imported from
        Retell when the call ends.
      </p>
    </Card>
  );
}
