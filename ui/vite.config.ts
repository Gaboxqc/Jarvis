import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

export default defineConfig({
  plugins: [react()],
  // Bound to localhost: this UI drives an API that reaches the user's files,
  // mail and calendar, so the dev server must not be reachable from the network.
  server: { host: "127.0.0.1", port: 5173, strictPort: true },
  build: { outDir: "dist", target: "es2022" },
});
