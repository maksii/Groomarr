import path from "node:path";
import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

// FastAPI serves the built app at "/" and assets at "/assets". During dev,
// proxy the backend routes to a locally-running uvicorn on :8000.
const backend = process.env.GROOMARR_BACKEND ?? "http://localhost:8000";

export default defineConfig({
  plugins: [react()],
  resolve: {
    alias: {
      "@": path.resolve(__dirname, "./src"),
    },
  },
  server: {
    port: 5173,
    proxy: {
      "/api": backend,
      "/health": backend,
      "/webhook": backend,
      "/rename": backend,
      "/find": backend,
      "/reload": backend,
    },
  },
  build: {
    outDir: "dist",
    emptyOutDir: true,
    sourcemap: false,
  },
});
