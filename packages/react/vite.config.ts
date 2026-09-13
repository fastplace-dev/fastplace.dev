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
    external: ["react", "react-dom"],
    outDir: "dist",
  },
});
