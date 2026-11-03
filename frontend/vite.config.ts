import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// In development the map app runs on :5173 and talks to the API on :8000.
export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      "/api": "http://localhost:8000",
      "/events": { target: "ws://localhost:8000", ws: true },
      "/voice": { target: "ws://localhost:8000", ws: true },
    },
  },
});
