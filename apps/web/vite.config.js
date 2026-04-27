import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// Vite proxies /api/* to FastAPI on :8000 in dev so the frontend can use
// same-origin fetches (no CORS dance).
export default defineConfig({
  plugins: [react()],
  server: {
    host: "0.0.0.0",
    port: 5173,
    proxy: {
      "/api": {
        target: "http://localhost:8000",
        changeOrigin: true,
        rewrite: (p) => p.replace(/^\/api/, ""),
      },
    },
  },
});
