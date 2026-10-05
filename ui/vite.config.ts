import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// Built to ui/dist, which `pheasant-lab serve` serves beside its API. In dev,
// `npm run dev` proxies /api to a console started with `pheasant-lab serve`.
export default defineConfig({
  plugins: [react()],
  server: {
    port: 5174,
    proxy: { "/api": process.env.PHEASANT_LAB_CONSOLE ?? "http://127.0.0.1:8770" },
  },
});
