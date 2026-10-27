import path from "node:path";
import { loadEnvConfig } from "@next/env";
import type { NextConfig } from "next";

// One .env at the repo root is shared by docker compose, the API and this app.
// forceReload: Next already loaded (empty) apps/web env files before this runs.
loadEnvConfig(path.resolve(__dirname, "../.."), process.env.NODE_ENV !== "production", console, true);

const nextConfig: NextConfig = {
  transpilePackages: ["@voice-lab/shared"],
};

export default nextConfig;
