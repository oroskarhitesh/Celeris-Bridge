/**
 * No dev proxy, on purpose.
 *
 * Proxying the API through Vite would make every request same-origin, which
 * is comfortable and hides the one thing most likely to be wrong on the day
 * this is deployed: the backend's admin CORS allow-list is empty by default.
 * Talking to it cross-origin in development means a missing
 * ``CB_ADMIN_ORIGINS`` fails here, on a laptop, instead of in production.
 */
import { fileURLToPath } from "node:url";

import tailwind from "@tailwindcss/vite";
import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

export default defineConfig({
  plugins: [react(), tailwind()],
  resolve: {
    alias: { "@": fileURLToPath(new URL("./src", import.meta.url)) },
  },
  server: { port: 5173, strictPort: true },
});
