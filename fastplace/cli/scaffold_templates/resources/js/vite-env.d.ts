/// <reference types="vite/client" />

// Ambient shim for the node builtins the theme-contrast guard reads the
// stylesheet with. The app bundle itself never imports these — only the
// colocated vitest file does, and the repo's root tsconfig has no @types/node.
declare module "node:fs" {
  export function readFileSync(path: string, encoding: string): string;
}
