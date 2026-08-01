import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// Relative base so the built bundle works when served from FastAPI's mount.
// In dev, proxy /api to the FastAPI process (default port 7860).
export default defineConfig({
  plugins: [react()],
  base: "./",
  server: {
    port: 5173,
    proxy: {
      "/api": "http://localhost:7860",
    },
  },
  build: {
    outDir: "dist",
    emptyOutDir: true,
  },
});
