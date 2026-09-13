import path from "node:path";
import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import tailwindcss from "@tailwindcss/vite";

// Fastplace Vite contract (blueprint §7):
// - dev: HMR server — the bridge shell references these dev-server URLs
//   directly (no proxy hop; CORS-open while developing)
// - build: hashed assets + manifest.json into public/build/
export default defineConfig({
  plugins: [react(), tailwindcss()],
  // Workspace packages resolve from source in dev/test/build; their dist/
  // exports stay for publishing.
  resolve: {
    alias: {
      "@fastplace/react": path.resolve(__dirname, "packages/react/src/index.tsx"),
    },
  },
  root: ".",
  publicDir: "public",
  build: {
    outDir: "public/build",
    emptyOutDir: true,
    manifest: true,
    rollupOptions: {
      input: "resources/js/main.jsx",
    },
  },
  server: {
    port: Number(process.env.VITE_PORT || 5173),
    strictPort: true,
    // The dev shell points straight at this server (see fastplace/http/assets.py);
    // no reverse proxy is needed.
    proxy: {},
  },
  test: {
    environment: "jsdom",
    include: [
      "packages/*/src/**/__tests__/**/*.test.{ts,tsx}",
      "resources/js/**/__tests__/**/*.{test,spec}.{ts,tsx,js,jsx}",
    ],
  },
});
