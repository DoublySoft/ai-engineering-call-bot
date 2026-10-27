import { NextRequest, NextResponse } from "next/server";

// The /api/lab proxy attaches the API admin token, so the web app itself must be
// protected: HTTP Basic auth with LAB_WEB_PASSWORD. Without a password the dev
// server only listens on 127.0.0.1 (see package.json); production refuses to serve.
export const config = { matcher: ["/((?!_next/static|_next/image|favicon.ico).*)"] };

function safeEqual(a: string, b: string): boolean {
  if (a.length !== b.length) return false;
  let diff = 0;
  for (let i = 0; i < a.length; i++) diff |= a.charCodeAt(i) ^ b.charCodeAt(i);
  return diff === 0;
}

export function middleware(req: NextRequest) {
  const password = process.env.LAB_WEB_PASSWORD;
  if (!password) {
    if (process.env.NODE_ENV === "production") {
      return new NextResponse("LAB_WEB_PASSWORD must be set to serve the lab outside local development", { status: 503 });
    }
    return NextResponse.next();
  }
  const header = req.headers.get("authorization") ?? "";
  if (header.startsWith("Basic ")) {
    const decoded = atob(header.slice(6));
    const supplied = decoded.slice(decoded.indexOf(":") + 1);
    if (safeEqual(supplied, password)) return NextResponse.next();
  }
  return new NextResponse("Authentication required", {
    status: 401,
    headers: { "WWW-Authenticate": 'Basic realm="Voice AI Lab", charset="UTF-8"' },
  });
}
