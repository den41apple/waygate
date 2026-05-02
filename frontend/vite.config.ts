import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

const proxy = {
  "/api": "http://localhost:8000",
  "/ws": { target: "ws://localhost:8000", ws: true },
};

export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy,
  },
  // preview не наследует server.proxy автоматически — задаём явно. Используется
  // в Playwright e2e (`npm run preview` против запущенного backend на :8000).
  preview: {
    port: 5173,
    proxy,
  },
});
