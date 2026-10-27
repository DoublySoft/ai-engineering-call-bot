import "server-only";

// Server-side only: the admin token never reaches the browser.
const API = process.env.API_INTERNAL_URL ?? "http://127.0.0.1:8000";

export async function apiFetch(path: string, init: RequestInit = {}): Promise<Response> {
  return fetch(`${API}${path}`, {
    ...init,
    cache: "no-store",
    headers: {
      ...(init.headers ?? {}),
      Authorization: `Bearer ${process.env.ADMIN_API_TOKEN ?? ""}`,
    },
  });
}

export async function apiJson<T>(path: string): Promise<T | null> {
  try {
    const res = await apiFetch(path);
    return res.ok ? ((await res.json()) as T) : null;
  } catch {
    return null;
  }
}
