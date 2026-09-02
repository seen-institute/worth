import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

// The console reads everything from worth-api under /api. In development Vite
// proxies that prefix to the backend so nothing in the app knows a host name;
// in production the host does the same with one rewrite rule.
const api = { "/api": "http://localhost:8000" };

export default defineConfig({
  plugins: [react()],
  base: "./",
  server: { proxy: api },
  preview: { proxy: api },
  build: { outDir: "dist" },
});
