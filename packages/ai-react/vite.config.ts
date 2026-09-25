import { defineConfig } from "vitest/config";
import react from "@vitejs/plugin-react";

export default defineConfig({
  plugins: [react()],
  test: {
    environment: "jsdom",
    include: ["src/**/__tests__/**/*.test.{ts,tsx}"],
  },
  build: {
    lib: {
      entry: "src/index.ts",
      name: "FastplaceAIReact",
      fileName: "fastplace-ai-react",
      formats: ["es"],
    },
    rollupOptions: {
      // Subpath-aware externals: exact names miss "react/jsx-runtime" and
      // inline a second React copy into dist, which crashes host apps with
      // "Invalid hook call". React comes from the host via peerDependencies.
      external: [/^react($|\/)/, /^scheduler($|\/)/],
    },
    outDir: "dist",
  },
});
