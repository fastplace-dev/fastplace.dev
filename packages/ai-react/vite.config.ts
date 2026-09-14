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
    external: ["react"],
    outDir: "dist",
  },
});
