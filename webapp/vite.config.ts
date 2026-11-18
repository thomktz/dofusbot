/// <reference types="vitest" />
import { defineConfig, loadEnv } from "vite";
import react from "@vitejs/plugin-react";

export default defineConfig(({ mode }) => {
  const backendPort = loadEnv(mode, ".", "VITE_").VITE_API_PORT || "8765";
  return {
    plugins: [react()],
    server: { port: 5173, strictPort: true, proxy: { "/assets": `http://127.0.0.1:${backendPort}`, "/api": `http://127.0.0.1:${backendPort}` } },
    preview: { port: 4173, strictPort: true },
    test: { environment: "node", include: ["src/**/*.test.ts"] },
  };
});
