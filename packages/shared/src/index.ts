// Contracts mirrored from the FastAPI pydantic models (apps/api/src/voice_lab).
// Keep in sync by hand for now; generate from /openapi.json once they stabilise.

export type CostStatus = "provider_reported" | "calculated" | "estimated" | "unavailable";
export type Direction = "outbound" | "inbound" | "browser";
export type Role = "user" | "agent" | "system";

export interface ModelCapabilities {
  supports_streaming: boolean;
  supports_tool_calling: boolean;
  supports_audio_input: boolean;
  supports_audio_output: boolean;
  reports_usage: boolean;
}

export interface PriceComponent {
  dimension: string;
  unit: "usd_per_1m_tokens" | "usd_per_minute" | "usd_per_1m_characters";
  price_usd: string;
}

export interface PricingVersion {
  version: string;
  effective_from: string;
  components: PriceComponent[];
  source_url: string | null;
  verified: boolean;
  notes: string | null;
}

export interface CatalogModel {
  key: string;
  provider: string;
  model: string;
  kind: "llm" | "stt" | "tts" | "speech_to_speech" | "telephony";
  display_name: string;
  capabilities: ModelCapabilities;
  deprecated: string | null;
  pricing: PricingVersion[];
  implemented: boolean;
}

export interface Catalog {
  models: CatalogModel[];
  implemented: Record<string, string[]>;
  defaults: Record<string, string>;
  outbound_allowlist: string[];
  voices: string[];
  readiness: Record<string, boolean>;
}

export interface SessionConfig {
  voice_provider: "custom" | "retell";
  engine: "pipeline" | "realtime";
  stt_provider?: string;
  stt_model?: string;
  llm_provider?: string;
  llm_model?: string;
  tts_provider?: string;
  tts_model?: string;
  tts_voice?: string;
  language?: string;
  system_prompt?: string;
  greeting?: string | null;
}

export interface CallSummary {
  id: string;
  direction: Direction;
  status: string;
  to_number: string | null;
  from_number: string | null;
  engine: string | null;
  llm_model: string | null;
  created_at: string;
  duration_seconds: number | null;
  total_cost_usd: string | null;
  error: string | null;
}

export interface CallEventRow {
  id: string;
  name: string;
  correlation_id: string | null;
  provider: string | null;
  model: string | null;
  duration_ms: number | null;
  data: Record<string, unknown>;
  timestamp: string;
}

export interface UsageRow {
  id: string;
  provider: string;
  model: string | null;
  operation_type: "stt" | "llm" | "tts" | "s2s" | "telephony" | "managed";
  input_tokens: number | null;
  output_tokens: number | null;
  cached_tokens: number | null;
  audio_duration_ms: number | null;
  characters: number | null;
  latency_ms: number | null;
  usage_source: string;
  cost_usd: string | null;
  cost_status: CostStatus;
  pricing_version: string | null;
  timestamp: string;
}

export interface MessageRow {
  id: string;
  seq: number;
  role: Role;
  text: string;
  interrupted: boolean;
  created_at: string;
}

export interface CallDetail {
  call: Record<string, unknown> & {
    id: string;
    status: string;
    direction: Direction;
    to_number: string | null;
    config_snapshot: Record<string, string>;
    started_at: string | null;
    answered_at: string | null;
    ended_at: string | null;
    duration_seconds: number | null;
    error: string | null;
  };
  sessions: { id: string; status: string; metrics: Record<string, unknown> }[];
  messages: MessageRow[];
  events: CallEventRow[];
  usage: UsageRow[];
  cost_by_operation: Record<string, { cost_usd: string; input_tokens: number; output_tokens: number; cached_tokens: number; statuses: CostStatus[] }>;
  events_token: string;
}

/** Messages pushed on ws /api/calls/{id}/live */
export type LiveMessage =
  | ({ type: "event" } & Omit<CallEventRow, "id"> & { call_id: string })
  | { type: "transcript"; role: Role; text: string; final: boolean; interrupted?: boolean; correlation_id: string; call_id: string };

export interface DashboardStats {
  calls: Record<Direction, number>;
  total_duration_seconds: number;
  tokens: { input: number; output: number };
  cost_total_usd: string;
  cost_breakdown: { operation: string; cost_status: CostStatus; cost_usd: string | null; events: number }[];
  active_sessions: number;
}
