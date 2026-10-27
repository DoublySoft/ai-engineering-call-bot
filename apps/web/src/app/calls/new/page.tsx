"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useEffect, useMemo, useState } from "react";
import type { Agent, AgentDetail, Catalog, CatalogModel } from "@voice-lab/shared";
import { Card } from "@/components/ui";

type Modality = "outbound" | "browser";

const ENGINES = [
  { id: "pipeline", label: "STT → LLM → TTS pipeline" },
  { id: "realtime", label: "OpenAI Realtime (speech-to-speech)" },
] as const;

const VOICE_PROVIDERS = [
  { id: "custom", label: "Custom (this lab)" },
  { id: "retell", label: "Retell AI (managed, browser only)" },
] as const;

function priceLabel(m: CatalogModel): string {
  const p = m.pricing[m.pricing.length - 1];
  if (!p) return "no price";
  const price = (dimension: string) => p.components.find((c) => c.dimension === dimension)?.price_usd;
  if (m.kind === "llm") return `$${price("input_text_tokens")} in / $${price("output_text_tokens")} out per 1M tokens`;
  if (m.kind === "speech_to_speech") return `audio $${price("input_audio_tokens")} in / $${price("output_audio_tokens")} out per 1M tokens`;
  const first = p.components[0];
  return `$${first.price_usd}/${first.unit.replace("usd_per_", "").replaceAll("_", " ")}${p.approximate ? " est." : ""}`;
}

function notReady(catalog: Catalog, modality: Modality, voiceProvider = "custom"): string[] {
  const needed = modality === "outbound" ? ["twilio", "public_base_url", "openai"] : voiceProvider === "retell" ? ["retell"] : ["openai"];
  return needed.filter((k) => !catalog.readiness[k]);
}

export default function NewCallPage() {
  const router = useRouter();
  const [catalog, setCatalog] = useState<Catalog | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [modality, setModality] = useState<Modality>("outbound");
  const [agents, setAgents] = useState<Agent[]>([]);
  const [agent, setAgent] = useState<AgentDetail | null>(null);
  // Fields the user changed for this call only; everything else comes from the agent.
  const [overridden, setOverridden] = useState<Set<string>>(new Set());
  const [form, setForm] = useState({
    to: "",
    voice_provider: "custom",
    engine: "pipeline",
    llm: "",
    stt: "",
    tts: "",
    tts_voice: "",
    s2s: "",
    retell_voice_id: "",
  });

  useEffect(() => {
    const json = async (r: Response) => (r.ok ? r.json() : Promise.reject((await r.json()).detail));
    fetch("/api/lab/catalog")
      .then(json)
      .then((c: Catalog) => {
        setCatalog(c);
        setForm((f) => ({ ...f, to: c.outbound_allowlist[0] ?? "" }));
        if (!c.readiness.twilio) setModality("browser");
      })
      .catch((e) => setError(String(e)));
    fetch("/api/lab/agents")
      .then(json)
      .then((list: Agent[]) => {
        setAgents(list);
        const fromUrl = new URLSearchParams(window.location.search).get("agent");
        const initial = list.find((a) => a.id === fromUrl) ?? list.find((a) => a.is_default) ?? list[0];
        if (initial) selectAgent(initial.id);
      })
      .catch((e) => setError(String(e)));
  }, []);

  async function selectAgent(agentId: string) {
    const res = await fetch(`/api/lab/agents/${agentId}`);
    if (!res.ok) return setError("Could not load the agent");
    const detail: AgentDetail = await res.json();
    const eff = detail.effective;
    setAgent(detail);
    setOverridden(new Set());
    setForm((f) => ({
      ...f,
      engine: detail.engine,
      voice_provider: detail.voice_provider,
      llm: `${eff.llm_provider}:${eff.llm_model}`,
      stt: `${eff.stt_provider}:${eff.stt_model}`,
      tts: `${eff.tts_provider}:${eff.tts_model}`,
      tts_voice: String(eff.tts_voice ?? ""),
      s2s: String(eff.s2s_model ?? ""),
      retell_voice_id: String(eff.retell_voice_id ?? ""),
    }));
  }

  function change(key: keyof typeof form, value: string) {
    setForm((f) => ({ ...f, [key]: value }));
    if (key !== "to") setOverridden((o) => new Set(o).add(key));
  }

  const byKind = useMemo(() => {
    const out: Record<string, CatalogModel[]> = { llm: [], stt: [], tts: [] };
    catalog?.models.forEach((m) => out[m.kind]?.push(m));
    return out;
  }, [catalog]);

  const llmProviders = useMemo(() => [...new Set(byKind.llm.map((m) => m.provider)).add("anthropic")], [byKind]);
  const [llmProvider] = form.llm.split(":");
  const retell = form.voice_provider === "retell";
  const realtime = !retell && form.engine === "realtime";
  const pipeline = !retell && form.engine === "pipeline";
  const retellModels = new Set(catalog?.retell.llm_models ?? []);

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setError(null);
    setBusy(true);
    // Send only what differs from the agent, so the call records real overrides.
    const config: Record<string, string> = { agent_id: agent?.id ?? "" };
    for (const part of ["llm", "stt", "tts"] as const) {
      if (!overridden.has(part)) continue;
      const [provider, model] = form[part].split(":");
      config[`${part}_provider`] = provider;
      config[`${part}_model`] = model;
    }
    if (overridden.has("tts_voice")) config.tts_voice = form.tts_voice;
    if (overridden.has("s2s")) config.s2s_model = form.s2s;
    if (overridden.has("retell_voice_id")) config.retell_voice_id = form.retell_voice_id;
    if (overridden.has("engine")) config.engine = form.engine;
    if (overridden.has("voice_provider")) config.voice_provider = form.voice_provider;
    const res = await fetch(modality === "browser" ? "/api/lab/browser-sessions" : "/api/lab/calls", {
      method: "POST",
      body: JSON.stringify({ ...(modality === "outbound" ? { to: form.to } : {}), config }),
    });
    const body = await res.json();
    setBusy(false);
    if (!res.ok) {
      setError(typeof body.detail === "string" ? body.detail : JSON.stringify(body.detail));
      return;
    }
    router.push(`/calls/${body.id}`);
  }

  const field = "w-full rounded-md border border-border bg-background px-3 py-2 text-sm";

  return (
    <form onSubmit={submit} className="mx-auto max-w-2xl space-y-4">
      <h1 className="text-xl font-semibold">New call</h1>

      {catalog && notReady(catalog, modality, form.voice_provider).length > 0 && (
        <Card className="border-amber-500/50">
          <p className="text-sm">
            Not ready for {modality === "outbound" ? "phone calls" : "browser sessions"}: {notReady(catalog, modality, form.voice_provider).join(", ")}. See{" "}
            <code>docs/first-call.md</code>.
          </p>
        </Card>
      )}

      <Card title="Modality">
        <div className="flex gap-2">
          {(["outbound", "browser"] as const).map((m) => (
            <button
              type="button"
              key={m}
              onClick={() => setModality(m)}
              className={`rounded-md border px-3 py-1.5 text-sm ${modality === m ? "border-foreground" : "border-border text-muted"}`}
            >
              {m === "outbound" ? "Outbound phone call" : "Browser (microphone)"}
            </button>
          ))}
        </div>
        {modality === "outbound" && (
          <label className="mt-4 block text-sm">
            Number (must be in OUTBOUND_ALLOWLIST)
            <select className={`${field} mt-1`} value={form.to} onChange={(e) => setForm({ ...form, to: e.target.value })}>
              {catalog?.outbound_allowlist.map((n) => (
                <option key={n}>{n}</option>
              ))}
            </select>
          </label>
        )}
      </Card>

      <Card title="Agent">
        <div className="flex flex-wrap items-center gap-3">
          <select className={`${field} flex-1`} value={agent?.id ?? ""} onChange={(e) => selectAgent(e.target.value)}>
            {agents.map((a) => (
              <option key={a.id} value={a.id}>
                {a.name} (v{a.version}){a.is_default ? " — default" : ""}
              </option>
            ))}
          </select>
          {agent && (
            <Link href={`/agents/${agent.id}`} className="text-sm text-blue-600 hover:underline">
              Edit agent →
            </Link>
          )}
        </div>
        {agent && (
          <div className="mt-3 space-y-1 text-sm">
            {agent.description && <p className="text-muted">{agent.description}</p>}
            <p className="line-clamp-3">{agent.settings.instructions}</p>
            <p className="text-xs text-muted">
              Greeting: {agent.settings.greeting ? `“${agent.settings.greeting}”` : "none (waits for the caller)"} · Knowledge:{" "}
              {agent.settings.knowledge ? `${agent.settings.knowledge.length.toLocaleString()} chars` : "none"}
            </p>
          </div>
        )}
      </Card>

      <Card title="Architecture">
        <div className="grid gap-3 md:grid-cols-2">
          <label className="text-sm">
            Voice provider
            <select className={`${field} mt-1`} value={form.voice_provider} onChange={(e) => change("voice_provider", e.target.value)}>
              {VOICE_PROVIDERS.map((p) => (
                <option key={p.id} value={p.id} disabled={p.id === "retell" && modality === "outbound"}>
                  {p.label}
                </option>
              ))}
            </select>
          </label>
          <label className="text-sm">
            Conversation engine
            <select
              className={`${field} mt-1`}
              value={retell ? "" : form.engine}
              disabled={retell}
              onChange={(e) => change("engine", e.target.value)}
            >
              {retell && <option value="">Managed by Retell</option>}
              {ENGINES.map((p) => (
                <option key={p.id} value={p.id}>
                  {p.label}
                </option>
              ))}
            </select>
          </label>
        </div>
      </Card>

      {pipeline && (
        <Card title="Models (pre-filled from the agent; changes apply to this call only)">
          <div className="grid gap-3 md:grid-cols-2">
            <label className="text-sm">
              LLM provider
              <select
                className={`${field} mt-1`}
                value={llmProvider}
                onChange={(e) => {
                  const first = byKind.llm.find((m) => m.provider === e.target.value);
                  if (first) change("llm", first.key);
                }}
              >
                {llmProviders.map((p) => (
                  <option key={p} value={p} disabled={!catalog?.implemented.llm.includes(p)}>
                    {p}
                    {!catalog?.implemented.llm.includes(p) ? " — Phase 3" : ""}
                  </option>
                ))}
              </select>
            </label>
            <label className="text-sm">
              LLM model
              <select className={`${field} mt-1`} value={form.llm} onChange={(e) => change("llm", e.target.value)}>
                {byKind.llm
                  .filter((m) => m.provider === llmProvider)
                  .map((m) => (
                    <option key={m.key} value={m.key}>
                      {m.display_name} ({priceLabel(m)})
                    </option>
                  ))}
              </select>
            </label>
            <label className="text-sm">
              STT model
              <select className={`${field} mt-1`} value={form.stt} onChange={(e) => change("stt", e.target.value)}>
                {byKind.stt.map((m) => (
                  <option key={m.key} value={m.key} disabled={!m.implemented}>
                    {m.display_name} ({priceLabel(m)}){m.deprecated ? " · deprecated" : ""}
                  </option>
                ))}
              </select>
            </label>
            <label className="text-sm">
              TTS model
              <select className={`${field} mt-1`} value={form.tts} onChange={(e) => change("tts", e.target.value)}>
                {byKind.tts.map((m) => (
                  <option key={m.key} value={m.key} disabled={!m.implemented}>
                    {m.display_name}
                    {m.deprecated ? " · deprecated" : ""}
                  </option>
                ))}
              </select>
            </label>
            <label className="text-sm">
              Voice
              <select className={`${field} mt-1`} value={form.tts_voice} onChange={(e) => change("tts_voice", e.target.value)}>
                {catalog?.voices.map((v) => (
                  <option key={v}>{v}</option>
                ))}
              </select>
            </label>
          </div>
        </Card>
      )}

      {realtime && (
        <Card title="Speech-to-speech (pre-filled from the agent; changes apply to this call only)">
          <div className="grid gap-3 md:grid-cols-2">
            <label className="text-sm">
              Realtime model
              <select className={`${field} mt-1`} value={form.s2s} onChange={(e) => change("s2s", e.target.value)}>
                {catalog?.models
                  .filter((m) => m.kind === "speech_to_speech")
                  .map((m) => (
                    <option key={m.key} value={m.model}>
                      {m.display_name} ({priceLabel(m)})
                    </option>
                  ))}
              </select>
            </label>
            <label className="text-sm">
              Input transcription (for the transcript)
              <select className={`${field} mt-1`} value={form.stt} onChange={(e) => change("stt", e.target.value)}>
                {byKind.stt.map((m) => (
                  <option key={m.key} value={m.key}>
                    {m.display_name} ({priceLabel(m)})
                  </option>
                ))}
              </select>
            </label>
            <label className="text-sm">
              Voice
              <select className={`${field} mt-1`} value={form.tts_voice} onChange={(e) => change("tts_voice", e.target.value)}>
                {!catalog?.realtime_voices.includes(form.tts_voice) && <option value={form.tts_voice}>{form.tts_voice} (not a Realtime voice → marin)</option>}
                {catalog?.realtime_voices.map((v) => (
                  <option key={v}>{v}</option>
                ))}
              </select>
            </label>
          </div>
          <p className="mt-2 text-xs text-muted">OpenAI does turn detection, transcription, reasoning and voice in one session. Anthropic can&apos;t run here.</p>
        </Card>
      )}

      {retell && (
        <Card title="Retell (pre-filled from the agent; changes apply to this call only)">
          <div className="grid gap-3 md:grid-cols-2">
            <label className="text-sm">
              LLM (run and billed by Retell)
              <select className={`${field} mt-1`} value={form.llm} onChange={(e) => change("llm", e.target.value)}>
                {!retellModels.has(form.llm.split(":")[1]) && <option value={form.llm}>{form.llm.split(":")[1]} — not available on Retell</option>}
                {byKind.llm
                  .filter((m) => retellModels.has(m.model))
                  .map((m) => (
                    <option key={m.key} value={m.key}>
                      {m.display_name}
                    </option>
                  ))}
              </select>
            </label>
            <label className="text-sm">
              Retell voice id
              <input className={`${field} mt-1`} value={form.retell_voice_id} onChange={(e) => change("retell_voice_id", e.target.value)} />
            </label>
          </div>
          <p className="mt-2 text-xs text-muted">
            Retell handles STT, LLM, voice and audio. The lab creates a Retell agent from this agent (reused while nothing changes) and imports
            Retell&apos;s reported cost and latency after the call. Data storage at Retell: {catalog?.retell.data_storage}.
          </p>
        </Card>
      )}

      {error && <p className="rounded-md border border-red-500/40 bg-red-500/10 p-3 text-sm text-red-600">{error}</p>}

      <button
        type="submit"
        disabled={busy || !catalog || (modality === "outbound" && !form.to)}
        className="rounded-md bg-foreground px-4 py-2 text-sm font-medium text-background disabled:opacity-40"
      >
        {busy ? "Starting…" : modality === "browser" ? "Create browser session" : "Start call"}
      </button>
    </form>
  );
}
