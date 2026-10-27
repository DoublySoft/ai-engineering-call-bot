export function usd(value: string | number | null | undefined, digits = 4): string {
  if (value === null || value === undefined) return "—";
  return `$${Number(value).toFixed(digits)}`;
}

export function duration(seconds: number | null | undefined): string {
  if (seconds === null || seconds === undefined) return "—";
  const h = Math.floor(seconds / 3600);
  const m = Math.floor((seconds % 3600) / 60);
  const s = Math.floor(seconds % 60);
  return [h, m, s].map((n) => String(n).padStart(2, "0")).join(":");
}

export function num(value: number | null | undefined): string {
  return value === null || value === undefined ? "—" : value.toLocaleString("en-US");
}

export const STATUS_LABEL: Record<string, string> = {
  calculated: "calculated",
  provider_reported: "provider",
  estimated: "estimated",
  unavailable: "n/a",
};
