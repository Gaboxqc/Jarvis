import { existsSync, readFileSync } from "node:fs";
import { homedir } from "node:os";
import { join } from "node:path";
import { defineConfig, type Plugin } from "vite";
import pkg from "./package.json";
import react from "@vitejs/plugin-react";

/**
 * Where the backend writes its API token. Mirrors `data_dir()` in
 * backend/app/settings.py; kept in step by the one thing that would notice,
 * which is `npm run dev` failing to reach a backend that is running.
 */
function tokenFile(): string {
  const override = process.env.KAI_DATA_DIR;
  if (override) return join(override, "api-token");
  if (process.platform === "win32") {
    const base = process.env.LOCALAPPDATA ?? join(homedir(), "AppData", "Local");
    return join(base, "Kai", "api-token");
  }
  return join(homedir(), ".local", "share", "kai", "api-token");
}

/**
 * Hand the dev server the backend's API token.
 *
 * The API refuses unauthenticated calls (backend/app/security.py). In the
 * packaged app the desktop shell supplies the token; in a browser at :5173
 * there is no shell, so it is read here from the file the backend wrote and
 * compiled into the dev bundle.
 *
 * `apply: "serve"` is the whole safety argument. `npm run build` runs on a
 * developer's machine, where that file holds *their* token — baking it into a
 * release would ship one machine's credentials to every install. Serving only
 * means the release bundle has no token in it at all, and reads
 * `import.meta.env.VITE_KAI_TOKEN` as undefined, which is correct: inside
 * Tauri the value comes from the shell instead.
 */
function devApiToken(): Plugin {
  return {
    name: "kai-dev-api-token",
    apply: "serve",
    config() {
      const path = tokenFile();
      const token = existsSync(path) ? readFileSync(path, "utf-8").trim() : "";
      if (!token) {
        console.warn(
          `[kai] no API token at ${path} — start the backend once and restart ` +
            "the dev server, or every request will come back 401.",
        );
      }
      return { define: { "import.meta.env.VITE_KAI_TOKEN": JSON.stringify(token) } };
    },
  };
}

export default defineConfig({
  // Taken from package.json so the version on the Updates card cannot
  // drift from the version the installer reports.
  define: { __APP_VERSION__: JSON.stringify(pkg.version) },
  plugins: [react(), devApiToken()],

  // Tauri compiles into ui/src-tauri/target, which sits inside Vite's project
  // root, so the dev server's file watcher tries to watch it. Cargo holds
  // kai.exe locked while linking, the watcher fails with EBUSY, and the crash
  // takes the whole dev server down -- which Tauri then reports as
  // `The "beforeDevCommand" terminated with a non-zero status code`.
  //
  // Nothing under src-tauri should trigger a frontend reload anyway.
  server: {
    // Bound to localhost: this UI drives an API that reaches the user's files,
    // mail and calendar, so the dev server must not be reachable from the network.
    host: "127.0.0.1",
    port: 5173,
    strictPort: true,
    watch: { ignored: ["**/src-tauri/**"] },
  },

  // Keep Rust compiler output on screen; Vite otherwise wipes it on reload and
  // the actual error scrolls away.
  clearScreen: false,

  // Tauri exposes build information through TAURI_* variables.
  envPrefix: ["VITE_", "TAURI_"],

  build: { outDir: "dist", target: "es2022" },
});
