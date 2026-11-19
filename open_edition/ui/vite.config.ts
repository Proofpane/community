/// <reference types="vitest/config" />
import { createHash } from "node:crypto";
import { readFileSync } from "node:fs";
import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import tailwindcss from "@tailwindcss/vite";
import { thirdPartyLicenses } from "./licenses.ts";

// The ADE app is served by the open edition's own server at /app/. In development, Vite serves the app and
// passes every API and WebSocket path through to a running server (OPEN_EDITION_DEV_SERVER, default :8765).
// `vite build --mode phone` builds it for the phone app instead (../phone/dist, served from the app's own root):
// there the app talks to the server the person names at sign-in (api.ts, server()). `--mode desktop` builds it for
// the desktop app (../desktop/build/ui), which talks to this Mac's server and to hosted ones (places.ts).
const target = process.env.OPEN_EDITION_DEV_SERVER || "http://localhost:8765";
const OUT: Record<string, string> = { phone: "../phone/dist", desktop: "../desktop/build/ui" };
// The sha256 of the link client beside ui/ (open_edition/ade_link.py), which the ADE of the same release serves
// (proofpane_open/ade/link_client.py): the block the page gives a session on claude.ai installs the client the ADE
// serves only where it has this sha256 (src/cloudLink.ts). A build without the file fails here, rather than give a
// block that checks nothing (the image's page stage copies it beside ui/, the Dockerfile).
const CLIENT_SHA256 = createHash("sha256").update(readFileSync(new URL("../ade_link.py", import.meta.url))).digest("hex");

export default defineConfig(({ mode }) => ({
  base: OUT[mode] ? "/" : "/app/",
  // every build writes THIRD_PARTY_LICENSES.txt beside the page: the npm packages it holds and their licenses
  plugins: [react(), tailwindcss(), thirdPartyLicenses()],
  define: { __ADE_LINK_SHA256__: JSON.stringify(CLIENT_SHA256) },
  build: { outDir: OUT[mode] ?? "../web/app", emptyOutDir: true, sourcemap: false, chunkSizeWarningLimit: 900 },
  server: {
    port: 5173,
    proxy: {
      "/api": { target, changeOrigin: false, ws: true },
    },
  },
  test: { environment: "jsdom", globals: true, include: ["src/**/*.test.{ts,tsx}"] },
}));
