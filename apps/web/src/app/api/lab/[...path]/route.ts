import { NextRequest, NextResponse } from "next/server";
import { apiFetch } from "@/lib/server-api";

// Same-origin proxy so browser code can reach /api/* without holding the admin token.
const ALLOWED = /^(catalog|dashboard|calls(\/[0-9a-f-]{36}(\/hangup)?)?)$/;

async function forward(req: NextRequest, { params }: { params: Promise<{ path: string[] }> }) {
  const path = (await params).path.join("/");
  if (!ALLOWED.test(path)) return NextResponse.json({ detail: "not found" }, { status: 404 });
  try {
    const res = await apiFetch(`/api/${path}${req.nextUrl.search}`, {
      method: req.method,
      body: req.method === "POST" ? await req.text() : undefined,
      headers: { "Content-Type": "application/json" },
    });
    return new NextResponse(await res.text(), { status: res.status, headers: { "Content-Type": "application/json" } });
  } catch {
    return NextResponse.json({ detail: "API unreachable — is `pnpm dev:api` running?" }, { status: 502 });
  }
}

export { forward as GET, forward as POST };
