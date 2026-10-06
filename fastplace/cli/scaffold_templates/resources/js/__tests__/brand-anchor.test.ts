/**
 * Brand anchor (logo + theme swap to #ff4d00, 2026-10-06).
 *
 * The brand color used to live only as a visual decision; after the swap it
 * is a contract three artifacts must agree on: the app.css brand scale (500
 * IS the mark's color), the two shipped public SVGs (every painted shape in
 * exactly the brand color, nothing else), and the React mark (the shipped
 * icon ported byte-for-byte). The scaffold corpus byte-sync test pins the
 * corpus twins to these repo copies; this file pins the repo copies.
 */
import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";

import { oklchToSrgb, parseBlocks, parseDeclarations, toHex } from "./support/tokens";

const BRAND_HEX = "#ff4d00";
const BRAND_HUE = 37;

// Same read-from-disk approach as theme-contrast.test.ts: vitest's cwd is the
// project root, and `?raw` imports of Tailwind-processed CSS don't survive the
// Vite pipeline under jsdom.
const css = readFileSync("resources/css/app.css", "utf8");
const themeBlock = parseBlocks(css).find((b) => b.selector.includes("@theme"));
if (!themeBlock) throw new Error("app.css: no @theme block found");
const themeTokens = parseDeclarations(themeBlock.body);

const brandTokens = Object.entries(themeTokens).filter(([name]) =>
  name.startsWith("--color-brand-"),
);

function oklchParts(value: string): [number, number, number] {
  const match = value.match(/oklch\(([\d.]+)\s+([\d.]+)\s+([\d.]+)\)/);
  if (!match) throw new Error(`not a plain oklch value: ${value}`);
  return [Number(match[1]), Number(match[2]), Number(match[3])];
}

describe("brand anchor — #ff4d00", () => {
  it("brand-500 is the exact brand color, down to the byte", () => {
    const raw = themeTokens["--color-brand-500"];
    expect(raw).toBeDefined();
    const [L, C, H] = oklchParts(raw ?? "");
    expect(toHex(oklchToSrgb(L, C, H))).toBe(BRAND_HEX);
  });

  it("every brand step stays in the mark's hue family", () => {
    expect(brandTokens.length).toBeGreaterThanOrEqual(5);
    for (const [name, value] of brandTokens) {
      const [, , H] = oklchParts(value);
      expect(H, `${name} must stay on the mark's hue`).toBe(BRAND_HUE);
    }
  });

  it.each([["public/fastplace-icon.svg"], ["public/fastplace-logo.svg"]] as const)(
    "every painted shape in %s is exactly the brand color",
    (file) => {
      const svg = readFileSync(file, "utf8");
      const colors = new Set((svg.match(/#[0-9a-fA-F]{3,8}\b/g) ?? []).map((c) => c.toLowerCase()));
      expect([...colors]).toEqual([BRAND_HEX]);
    },
  );

  it.each([["public/fastplace-icon.svg"], ["public/fastplace-logo.svg"]] as const)(
    "%s ships inert (no script, event handlers, or remote refs)",
    (file) => {
      // These SVGs are served raw from public/; they must stay pure geometry.
      const svg = readFileSync(file, "utf8");
      expect(svg).not.toMatch(/<script|foreignObject|on\w+\s*=|javascript:/i);
      expect(svg).not.toMatch(/href\s*=\s*["']https?:/i);
    },
  );

  it("AppLogoIcon ports the shipped mark byte-for-byte", () => {
    const iconSvg = readFileSync("public/fastplace-icon.svg", "utf8");
    const componentSource = readFileSync("resources/js/components/app-logo-icon.tsx", "utf8");
    const svgPaths = [...iconSvg.matchAll(/\sd="([^"]+)"/g)].map((m) => m[1]);
    const componentPaths = [...componentSource.matchAll(/\sd="([^"]+)"/g)].map((m) => m[1]);
    expect(svgPaths.length).toBe(3);
    expect(componentPaths).toEqual(svgPaths);
  });

  it("AppLogoIcon matches the shipped mark's viewBox", () => {
    const iconSvg = readFileSync("public/fastplace-icon.svg", "utf8");
    const componentSource = readFileSync("resources/js/components/app-logo-icon.tsx", "utf8");
    const viewBox = iconSvg.match(/viewBox="([^"]+)"/)?.[1];
    expect(viewBox).toBeDefined();
    expect(componentSource).toContain(`viewBox="${viewBox}"`);
  });
});
