import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

export default defineConfig({
  plugins: [react()],

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
