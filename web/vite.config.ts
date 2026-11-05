import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import tailwindcss from "@tailwindcss/vite";

// Builds into the Python app (assistant/web/dist), which serves it at "/". `npm run dev` proxies the API to a
// running Convo on port 8765.
export default defineConfig({
  plugins: [react(), tailwindcss()],
  build: {
    outDir: "../assistant/web/dist", emptyOutDir: true, chunkSizeWarningLimit: 600,
    rollupOptions: { output: { manualChunks: { react: ["react", "react-dom", "react-router-dom", "@tanstack/react-query"], charts: ["recharts"] } } },
  },
  server: { proxy: { "/api": "http://127.0.0.1:8765", "/classic": "http://127.0.0.1:8765" } },
});
