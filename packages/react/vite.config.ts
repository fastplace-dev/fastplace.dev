import { defineConfig } from "vitest/config";
import react from "@vitejs/plugin-react";

export default defineConfig({
  plugins: [react()],
  test: {
    environment: "jsdom",
    include: ["src/**/*.test.{ts,tsx}"],
  },
  build: {
    lib: {
      entry: "src/index.tsx",
      name: "FastplaceReact",
      fileName: "fastplace-react",
      formats: ["es"],
    },
    rollupOptions: {
      // Subpath-aware externals: exact names miss "react/jsx-runtime" and
      // inline a second React copy into dist, which crashes host apps with
      // "Invalid hook call". React (and its scheduler dep) come from the host.
      external: [/^react($|\/)/, /^react-dom($|\/)/, /^scheduler($|\/)/],
      output: {
        entryFileNames: "fastplace-react.js",
        chunkFileNames: "[name].js",
      },
    },
    outDir: "dist",
  },
});
