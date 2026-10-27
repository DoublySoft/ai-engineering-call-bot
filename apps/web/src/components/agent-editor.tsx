"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useEffect, useMemo, useState } from "react";
import type { AgentDetail, AgentMeta, AgentSettings, Catalog } from "@voice-lab/shared";
import { Card } from "@/components/ui";

const EMPTY: AgentSettings = {
  instructions: "",
  knowledge: "",
  greeting: "",
  language: "es",
  stt_provider: null,
  stt_model: null,
  llm_provider: null,
  llm_model: null,
  tts_provider: null,
  tts_model: null,
  tts_voice: null,
  max_output_tokens: 300,
};

const field = "w-full rounded-md border border-border bg-background px-3 py-2 text-sm";

/** "provider:model" <-> two nullable settings fields; "" = server default. */
function modelKey(s: AgentSettings, part: "stt" | "llm" | "tts"): string {
  const provider = s[`${part}_provider`];
  const model = s[`${part}_model`];
  return provider && model ? `${provider}:${model}` : "";
}

export function AgentEditor({ id }: { id?: string }) {
  const router = useRouter();
  const [agent, setAgent] = useState<AgentDetail | null>(null);
  const [catalog, setCatalog] = useState<Catalog | null>(null);
  const [meta, setMeta] = useState<AgentMeta | null>(null);
  const [name, setName] = useState("");
  const [description, setDescription] = useState("");
  const [s, setS] = useState<AgentSettings>(EMPTY);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    const json = (r: Response) => (r.ok ? r.json() : Promise.reject(r.statusText));
    fetch("/api/lab/catalog").then(json).then(setCatalog).catch(() => setError("Could not load the model catalog"));
    fetch("/api/lab/agents/meta").then(json).then(setMeta).catch(() => {});
    if (!id) return;
    fetch(`/api/lab/agents/${id}`)
      .then(json)
      .then((a: AgentDetail) => {
        setAgent(a);
        setName(a.name);
        setDescription(a.description ?? "");
        setS({ ...a.settings, greeting: a.settings.greeting ?? "" });
      })
      .catch(() => setError("Agent not found"));
  }, [id]);

  const models = useMemo(() => {
    const out: Record<string, Catalog["models"]> = { llm: [], stt: [], tts: [] };
    catalog?.models.filter((m) => m.implemented).forEach((m) => out[m.kind]?.push(m));
    return out;
  }, [catalog]);

  function setModel(part: "stt" | "llm" | "tts", key: string) {
    const [provider, model] = key ? key.split(":") : [null, null];
    setS({ ...s, [`${part}_provider`]: provider, [`${part}_model`]: model });
  }

  async function save(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError(null);
    setNotice(null);
    const body = {
      name,
      description: description || null,
      engine: "pipeline",
      voice_provider: "custom",
      settings: { ...s, greeting: s.greeting?.trim() ? s.greeting : null },
    };
    const res = await fetch(id ? `/api/lab/agents/${id}` : "/api/lab/agents", { method: id ? "PUT" : "POST", body: JSON.stringify(body) });
    const data = await res.json();
    setBusy(false);
    if (!res.ok) {
      setError(typeof data.detail === "string" ? data.detail : data.detail.map((d: { loc: string[]; msg: string }) => `${d.loc.at(-1)}: ${d.msg}`).join("; "));
      return;
    }
    if (!id) {
      router.push(`/agents/${data.id}`);
      return;
    }
    setNotice(data.version !== agent?.version ? `Saved as version ${data.version}.` : "Saved (name/description only — no new version).");
    setAgent(data);
  }

  async function action(path: string, method: "POST" | "DELETE", done: string) {
    const res = await fetch(`/api/lab/agents/${id}${path}`, { method });
    const data = await res.json();
    if (!res.ok) return setError(data.detail);
    if (method === "DELETE") return router.push("/agents");
    setNotice(done);
    setAgent(agent && { ...agent, is_default: true });
  }

  async function tryIt() {
    const res = await fetch("/api/lab/browser-sessions", { method: "POST", body: JSON.stringify({ config: { agent_id: id } }) });
    const data = await res.json();
    if (!res.ok) return setError(data.detail);
    router.push(`/calls/${data.id}`);
  }

  if (id && !agent && !error) return <p className="text-sm text-muted">Loading…</p>;

  const defaults = catalog?.defaults ?? {};
  return (
    <form onSubmit={save} className="mx-auto max-w-3xl space-y-4">
      <div className="flex flex-wrap items-center gap-3">
        <Link href="/agents" className="text-sm text-muted hover:text-foreground">
          ← Agents
        </Link>
        <h1 className="text-xl font-semibold">{id ? name || "Agent" : "New agent"}</h1>
        {agent?.is_default && <span className="rounded bg-blue-500/15 px-2 py-0.5 text-xs text-blue-600">default</span>}
        {agent && <span className="text-xs text-muted">v{agent.version} · {agent.calls} calls</span>}
        {agent && (
          <button type="button" onClick={tryIt} className="ml-auto rounded-md border border-border px-3 py-1.5 text-sm">
            Talk to it in the browser
          </button>
        )}
      </div>

      <Card title="Identity">
        <div className="grid gap-3 md:grid-cols-2">
          <label className="text-sm">
            Name
            <input required className={`${field} mt-1`} value={name} onChange={(e) => setName(e.target.value)} placeholder="Recepción clínica" />
          </label>
          <label className="text-sm">
            Description (for you, not the AI)
            <input className={`${field} mt-1`} value={description} onChange={(e) => setDescription(e.target.value)} />
          </label>
        </div>
      </Card>

      <Card title="Behaviour">
        <label className="block text-sm">
          Instructions — who the agent is and what it should do
          <textarea
            required
            rows={7}
            className={`${field} mt-1 font-mono`}
            value={s.instructions}
            onChange={(e) => setS({ ...s, instructions: e.target.value })}
            placeholder="Eres la recepcionista de… Tu objetivo es… Si te piden X, haz Y…"
          />
        </label>
        <label className="mt-3 block text-sm">
          Knowledge — reference facts the agent can use (prices, hours, FAQ…)
          <textarea
            rows={8}
            className={`${field} mt-1 font-mono`}
            value={s.knowledge}
            onChange={(e) => setS({ ...s, knowledge: e.target.value })}
            placeholder={"Horario: lunes a viernes de 9 a 18 h.\nDirección: …\nPreguntas frecuentes: …"}
          />
          <span className="text-xs text-muted">{s.knowledge.length.toLocaleString()} / 30,000 characters</span>
        </label>
        <div className="mt-3 grid gap-3 md:grid-cols-2">
          <label className="text-sm">
            Greeting (empty = wait for the caller to speak)
            <input className={`${field} mt-1`} value={s.greeting ?? ""} onChange={(e) => setS({ ...s, greeting: e.target.value })} />
          </label>
          <label className="text-sm">
            Language
            <select className={`${field} mt-1`} value={s.language} onChange={(e) => setS({ ...s, language: e.target.value })}>
              {Object.entries(meta?.languages ?? { es: "español" }).map(([code, label]) => (
                <option key={code} value={code}>
                  {label}
                </option>
              ))}
              {/* Versions saved earlier may use a language outside the list; keep it visible. */}
              {meta && !(s.language in meta.languages) && <option value={s.language}>{s.language}</option>}
            </select>
          </label>
        </div>
        {meta && (
          <details className="mt-3 text-xs text-muted">
            <summary className="cursor-pointer">Rules every voice agent gets (added before your instructions)</summary>
            <p className="mt-2 whitespace-pre-wrap">{meta.voice_rules}</p>
          </details>
        )}
      </Card>

      <Card title="Default models (can be overridden per call)">
        <div className="grid gap-3 md:grid-cols-2">
          {(["llm", "stt", "tts"] as const).map((part) => (
            <label key={part} className="text-sm">
              {part.toUpperCase()}
              <select className={`${field} mt-1`} value={modelKey(s, part)} onChange={(e) => setModel(part, e.target.value)}>
                <option value="">Server default ({defaults[`${part}_model`]})</option>
                {models[part].map((m) => (
                  <option key={m.key} value={m.key}>
                    {m.display_name}
                    {m.deprecated ? " · deprecated" : ""}
                  </option>
                ))}
              </select>
            </label>
          ))}
          <label className="text-sm">
            Voice
            <select className={`${field} mt-1`} value={s.tts_voice ?? ""} onChange={(e) => setS({ ...s, tts_voice: e.target.value || null })}>
              <option value="">Server default ({defaults.tts_voice})</option>
              {catalog?.voices.map((v) => (
                <option key={v}>{v}</option>
              ))}
            </select>
          </label>
          <label className="text-sm">
            Max reply tokens
            <input
              type="number"
              min={16}
              max={2000}
              className={`${field} mt-1`}
              value={s.max_output_tokens}
              onChange={(e) => setS({ ...s, max_output_tokens: Number(e.target.value) })}
            />
          </label>
        </div>
      </Card>

      {agent?.effective && (
        <Card title={`Final prompt sent to the LLM (v${agent.version})`}>
          {agent.effective.error ? (
            <p className="text-sm text-red-600">{agent.effective.error}</p>
          ) : (
            <pre className="max-h-80 overflow-auto whitespace-pre-wrap text-xs">{agent.effective.system_prompt}</pre>
          )}
        </Card>
      )}

      {error && <p className="rounded-md border border-red-500/40 bg-red-500/10 p-3 text-sm text-red-600">{error}</p>}
      {notice && <p className="rounded-md border border-green-500/40 bg-green-500/10 p-3 text-sm text-green-600">{notice}</p>}

      <div className="flex flex-wrap items-center gap-3">
        <button type="submit" disabled={busy} className="rounded-md bg-foreground px-4 py-2 text-sm font-medium text-background disabled:opacity-40">
          {busy ? "Saving…" : id ? "Save" : "Create agent"}
        </button>
        {agent && !agent.is_default && (
          <>
            <button type="button" onClick={() => action("/default", "POST", "This is now the default agent.")} className="rounded-md border border-border px-3 py-2 text-sm">
              Make default
            </button>
            <button
              type="button"
              onClick={() => confirm(`Archive "${agent.name}"? Past calls keep their history.`) && action("", "DELETE", "")}
              className="rounded-md border border-red-500/50 px-3 py-2 text-sm text-red-600"
            >
              Archive
            </button>
          </>
        )}
      </div>

      {agent && agent.versions.length > 1 && (
        <Card title="Versions">
          <ul className="space-y-1 text-sm">
            {agent.versions.map((v) => (
              <li key={v.configuration_id} className="text-muted">
                v{v.version} — {new Date(v.created_at).toLocaleString()}
              </li>
            ))}
          </ul>
        </Card>
      )}
    </form>
  );
}
