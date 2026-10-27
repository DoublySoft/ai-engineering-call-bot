"use client";

import { useRouter } from "next/navigation";
import { useEffect, useMemo, useState } from "react";
import type { Catalog, CatalogModel } from "@voice-lab/shared";
import { Card } from "@/components/ui";

type Modality = "outbound" | "browser";

const ENGINES = [
  { id: "pipeline", label: "STT → LLM → TTS pipeline", phase: null },
  { id: "realtime", label: "OpenAI Realtime (speech-to-speech)", phase: "Phase 6" },
] as const;

const VOICE_PROVIDERS = [
  { id: "custom", label: "Custom (this lab)", phase: null },
  { id: "retell", label: "Retell AI (managed)", phase: "Phase 7" },
] as const;

function priceLabel(m: CatalogModel): string {
  const p = m.pricing[m.pricing.length - 1];
  if (!p) return "no price";
  const price = (dimension: string) => p.components.find((c) => c.dimension === dimension)?.price_usd;
  if (m.kind === "llm") return `$${price("input_text_tokens")} in / $${price("output_text_tokens")} out per 1M tokens`;
  const first = p.components[0];
  return `$${first.price_usd}/${first.unit.replace("usd_per_", "").replaceAll("_", " ")}${p.approximate ? " est." : ""}`;
}

function notReady(catalog: Catalog, modality: Modality): string[] {
  const needed = modality === "outbound" ? ["twilio", "public_base_url", "openai"] : ["openai"];
  return needed.filter((k) => !catalog.readiness[k]);
}

export default function NewCallPage() {
  const router = useRouter();
  const [catalog, setCatalog] = useState<Catalog | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [modality, setModality] = useState<Modality>("outbound");
  const [form, setForm] = useState({
    to: "",
    voice_provider: "custom",
    engine: "pipeline",
    llm: "",
    stt: "",
    tts: "",
    tts_voice: "",
    greeting: "Hola, soy el asistente del laboratorio de voz. ¿En qué puedo ayudarte?",
  });

  useEffect(() => {
    fetch("/api/lab/catalog")
      .then(async (r) => (r.ok ? r.json() : Promise.reject((await r.json()).detail)))
      .then((c: Catalog) => {
        setCatalog(c);
        setForm((f) => ({
          ...f,
          to: c.outbound_allowlist[0] ?? "",
          llm: `${c.defaults.llm_provider}:${c.defaults.llm_model}`,
          stt: `${c.defaults.stt_provider}:${c.defaults.stt_model}`,
          tts: `${c.defaults.tts_provider}:${c.defaults.tts_model}`,
          tts_voice: c.defaults.tts_voice,
        }));
      })
      .catch((e) => setError(String(e)));
  }, []);

  const byKind = useMemo(() => {
    const out: Record<string, CatalogModel[]> = { llm: [], stt: [], tts: [] };
    catalog?.models.forEach((m) => out[m.kind]?.push(m));
    return out;
  }, [catalog]);

  const llmProviders = useMemo(() => [...new Set(byKind.llm.map((m) => m.provider)).add("anthropic")], [byKind]);
  const [llmProvider, llmModel] = form.llm.split(":");
  const pipeline = form.engine === "pipeline";

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setError(null);
    setBusy(true);
    const [stt_provider, stt_model] = form.stt.split(":");
    const [tts_provider, tts_model] = form.tts.split(":");
    const res = await fetch(modality === "browser" ? "/api/lab/browser-sessions" : "/api/lab/calls", {
      method: "POST",
      body: JSON.stringify({
        ...(modality === "outbound" ? { to: form.to } : {}),
        config: {
          voice_provider: form.voice_provider,
          engine: form.engine,
          llm_provider: llmProvider,
          llm_model: llmModel,
          stt_provider,
          stt_model,
          tts_provider,
          tts_model,
          tts_voice: form.tts_voice,
          greeting: form.greeting || null,
        },
      }),
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

      {catalog && notReady(catalog, modality).length > 0 && (
        <Card className="border-amber-500/50">
          <p className="text-sm">
            Not ready for {modality === "outbound" ? "phone calls" : "browser sessions"}: {notReady(catalog, modality).join(", ")}. See{" "}
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

      <Card title="Architecture">
        <div className="grid gap-3 md:grid-cols-2">
          <label className="text-sm">
            Voice provider
            <select className={`${field} mt-1`} value={form.voice_provider} onChange={(e) => setForm({ ...form, voice_provider: e.target.value })}>
              {VOICE_PROVIDERS.map((p) => (
                <option key={p.id} value={p.id} disabled={!!p.phase}>
                  {p.label}
                  {p.phase ? ` — ${p.phase}` : ""}
                </option>
              ))}
            </select>
          </label>
          <label className="text-sm">
            Conversation engine
            <select className={`${field} mt-1`} value={form.engine} onChange={(e) => setForm({ ...form, engine: e.target.value })}>
              {ENGINES.map((p) => (
                <option key={p.id} value={p.id} disabled={!!p.phase}>
                  {p.label}
                  {p.phase ? ` — ${p.phase}` : ""}
                </option>
              ))}
            </select>
          </label>
        </div>
      </Card>

      {pipeline && (
        <Card title="Models">
          <div className="grid gap-3 md:grid-cols-2">
            <label className="text-sm">
              LLM provider
              <select
                className={`${field} mt-1`}
                value={llmProvider}
                onChange={(e) => {
                  const first = byKind.llm.find((m) => m.provider === e.target.value);
                  if (first) setForm({ ...form, llm: first.key });
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
              <select className={`${field} mt-1`} value={form.llm} onChange={(e) => setForm({ ...form, llm: e.target.value })}>
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
              <select className={`${field} mt-1`} value={form.stt} onChange={(e) => setForm({ ...form, stt: e.target.value })}>
                {byKind.stt.map((m) => (
                  <option key={m.key} value={m.key} disabled={!m.implemented}>
                    {m.display_name} ({priceLabel(m)}){m.deprecated ? " · deprecated" : ""}
                  </option>
                ))}
              </select>
            </label>
            <label className="text-sm">
              TTS model
              <select className={`${field} mt-1`} value={form.tts} onChange={(e) => setForm({ ...form, tts: e.target.value })}>
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
              <select className={`${field} mt-1`} value={form.tts_voice} onChange={(e) => setForm({ ...form, tts_voice: e.target.value })}>
                {catalog?.voices.map((v) => (
                  <option key={v}>{v}</option>
                ))}
              </select>
            </label>
          </div>
          <label className="mt-3 block text-sm">
            Greeting (spoken when the call connects; empty = wait for the caller)
            <input className={`${field} mt-1`} value={form.greeting} onChange={(e) => setForm({ ...form, greeting: e.target.value })} />
          </label>
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
