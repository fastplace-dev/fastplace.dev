/**
 * Theme contrast guard (audit 2026-09-27, wave W5).
 *
 * Walks the REAL token pairs defined in resources/css/app.css — both light and
 * dark schemes — recomputes WCAG 2.1 contrast from the oklch values, and fails
 * under 4.5:1 for text pairs or 3:1 for UI pairs (focus ring, borders).
 *
 * Audit baseline ratios (fixtures these tests must beat — claim ids from the
 * 2026-09-27 full-framework parity audit):
 *   a11y2-G3  text-success on light surface ............ 3.56:1  (fail)
 *   a11y2-G8  --warning on light surface (latent) ...... 2.43:1  (fail)
 *   a11y2-G6  primary button hover blend, light ........ 4.19:1  (fail)
 *   a11y2-G1  destructive alert text, light ............ 1.00:1  (fail)
 *   a11y1-G5  danger text on destructive/10 tint ....... 4.09:1  (fail)
 *   a11y1-G9  focus ring at ring/50, light ............. 1.84:1  (fail, 3:1 UI bar)
 */
import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";

// Color math and CSS-token parsing live in the shared test support module so
// the brand-anchor test and this guard compute from one implementation.
import {
  blend,
  contrast,
  oklchToSrgb,
  parseBlocks,
  parseDeclarations,
  type Rgb,
  type Scope,
} from "./support/tokens";

// The real stylesheet, read from disk — the Vite pipeline mangles `?raw`
// imports of Tailwind-processed CSS into an empty string and jsdom rewrites
// import.meta.url out of the file scheme, so resolve against vitest's cwd
// (the project root) instead.
const css = readFileSync("resources/css/app.css", "utf8");

const blocks = parseBlocks(css);

function scopeBy(selectorNeedle: string): Scope {
  const block = blocks.find((b) => b.selector.includes(selectorNeedle));
  if (!block) throw new Error(`app.css: no block matches ${JSON.stringify(selectorNeedle)}`);
  return parseDeclarations(block.body);
}

const theme = scopeBy("@theme");
const light = scopeBy(":root");
const dark = scopeBy(".dark,");
// The OS-preference dark block must stay value-identical to the explicit
// .dark block (the file keeps both in sync by hand). The inner
// :root:not([data-theme="light"]) rule lives inside the media query's body.
const darkMedia = parseDeclarations(
  parseBlocks(css).find((b) => b.selector.includes("prefers-color-scheme: dark"))?.body ?? "",
);

type Theme = "light" | "dark";

function scopeOf(themeName: Theme): Scope {
  return themeName === "light" ? light : dark;
}

/** Resolve a custom property to an sRGB triple under a theme's cascade. */
function tokenColor(name: string, themeName: Theme): Rgb {
  const KEYWORDS: Record<string, Rgb> = { white: [1, 1, 1], black: [0, 0, 0] };
  const scope = scopeOf(themeName);
  let current = scope[name] ?? light[name] ?? theme[name];
  for (let hops = 0; current?.startsWith("var(") && hops < 10; hops += 1) {
    const ref = current.slice(4, -1).trim();
    current = scope[ref] ?? light[ref] ?? theme[ref];
  }
  if (current && KEYWORDS[current]) return KEYWORDS[current];
  const match = current?.match(/oklch\(([\d.]+)\s+([\d.]+)\s+([\d.]+)\)/);
  if (!match) throw new Error(`cannot resolve ${name} in ${themeName}: ${current}`);
  return oklchToSrgb(Number(match[1]), Number(match[2]), Number(match[3]));
}

function surfaceOf(themeName: Theme): Rgb {
  return tokenColor("--surface", themeName);
}
function cardOf(themeName: Theme): Rgb {
  return tokenColor("--card", themeName);
}

const THEMES: Theme[] = ["light", "dark"];

/* ------------------------------------------------------------------ *
 * Guards
 * ------------------------------------------------------------------ */

describe("theme contrast guard (WCAG 2.1 AA)", () => {
  describe.each(THEMES)("%s theme — status text on surfaces (≥4.5:1)", (t) => {
    const surface = surfaceOf(t);
    const card = cardOf(t);

    it("success token passes on page surface and card (a11y2-G3)", () => {
      const success = tokenColor("--success", t);
      expect(contrast(success, surface)).toBeGreaterThanOrEqual(4.5);
      expect(contrast(success, card)).toBeGreaterThanOrEqual(4.5);
    });

    it("warning token passes on page surface and card (a11y2-G8, latent)", () => {
      const warning = tokenColor("--warning", t);
      expect(contrast(warning, surface)).toBeGreaterThanOrEqual(4.5);
      expect(contrast(warning, card)).toBeGreaterThanOrEqual(4.5);
    });

    it("destructive token passes as alert/error text on surface and card (a11y2-G1)", () => {
      const destructive = tokenColor("--destructive", t);
      expect(contrast(destructive, surface)).toBeGreaterThanOrEqual(4.5);
      expect(contrast(destructive, card)).toBeGreaterThanOrEqual(4.5);
    });

    it("destructive text passes on the destructive/10 danger-zone tint (a11y1-G5)", () => {
      const destructive = tokenColor("--destructive", t);
      const tint = blend(destructive, 0.1, card);
      expect(contrast(destructive, tint)).toBeGreaterThanOrEqual(4.5);
    });
  });

  describe.each(THEMES)("%s theme — primary button (≥4.5:1)", (t) => {
    it("label passes on base and on the /90 hover blend (a11y2-G6)", () => {
      const fg = tokenColor("--primary-foreground", t);
      const primary = tokenColor("--primary", t);
      const card = cardOf(t);
      expect(contrast(fg, primary)).toBeGreaterThanOrEqual(4.5);
      // hover:bg-primary/90 composites the button fill over the card behind it
      expect(contrast(fg, blend(primary, 0.9, card))).toBeGreaterThanOrEqual(4.5);
    });

    it("focus ring at the shipped opacity clears the 3:1 UI bar (a11y1-G9)", () => {
      const ring = tokenColor("--ring", t);
      const card = cardOf(t);
      const ringOpacity = 0.75; // keep in sync with focus-visible:ring-ring/75
      expect(contrast(blend(ring, ringOpacity, surfaceOf(t)), surfaceOf(t))).toBeGreaterThanOrEqual(
        3,
      );
      expect(contrast(blend(ring, ringOpacity, card), card)).toBeGreaterThanOrEqual(3);
    });
  });

  describe("audit baseline — every fixed pair beats its audit ratio", () => {
    it("light success > 3.56 (a11y2-G3 baseline)", () => {
      expect(contrast(tokenColor("--success", "light"), surfaceOf("light"))).toBeGreaterThan(3.56);
    });

    it("light warning > 2.43 (a11y2-G8 baseline)", () => {
      expect(contrast(tokenColor("--warning", "light"), surfaceOf("light"))).toBeGreaterThan(2.43);
    });

    it("light primary hover blend > 4.19 (a11y2-G6 baseline)", () => {
      const fg = tokenColor("--primary-foreground", "light");
      const hover = blend(tokenColor("--primary", "light"), 0.9, cardOf("light"));
      expect(contrast(fg, hover)).toBeGreaterThan(4.19);
    });

    it("light destructive alert text > 1.0 (a11y2-G1 baseline)", () => {
      expect(contrast(tokenColor("--destructive", "light"), cardOf("light"))).toBeGreaterThan(1.0);
    });

    it("light danger tint pair > 4.09 (a11y1-G5 baseline)", () => {
      const destructive = tokenColor("--destructive", "light");
      expect(contrast(destructive, blend(destructive, 0.1, cardOf("light")))).toBeGreaterThan(4.09);
    });

    it("light focus ring at /75 > the /50 audit state 1.84 (a11y1-G9 baseline)", () => {
      const ring = tokenColor("--ring", "light");
      expect(contrast(blend(ring, 0.75, surfaceOf("light")), surfaceOf("light"))).toBeGreaterThan(
        1.84,
      );
    });
  });

  describe("dark scheme consistency", () => {
    it("media-query dark block matches the explicit .dark block values", () => {
      for (const [name, value] of Object.entries(dark)) {
        expect(darkMedia[name] ?? light[name]).toBe(value);
      }
    });
  });
});

describe("reduced-motion guard (a11y2-G7)", () => {
  it("app.css ships a global prefers-reduced-motion kill switch", () => {
    expect(css).toMatch(
      /@media\s*\(prefers-reduced-motion:\s*reduce\)\s*\{[^}]*animation-duration[^}]*transition-duration/s,
    );
  });
});
