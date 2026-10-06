/**
 * Shared color + CSS-token math for the stylesheet guard tests.
 *
 * Extracted from theme-contrast.test.ts so the brand-anchor test and the
 * contrast guard compute from one implementation — two copies of WCAG math
 * would drift apart silently.
 */

export type Rgb = [number, number, number];

export function oklchToSrgb(L: number, C: number, H: number): Rgb {
  const a = C * Math.cos((H * Math.PI) / 180);
  const b = C * Math.sin((H * Math.PI) / 180);
  const l_ = L + 0.3963377774 * a + 0.2158037573 * b;
  const m_ = L - 0.1055613458 * a - 0.0638541728 * b;
  const s_ = L - 0.0894841775 * a - 1.291485548 * b;
  const l = l_ ** 3;
  const m = m_ ** 3;
  const s = s_ ** 3;
  const lin: Rgb = [
    4.0767416621 * l - 3.3077115913 * m + 0.2309699292 * s,
    -1.2684380046 * l + 2.6097574011 * m - 0.3413193965 * s,
    -0.0041960863 * l - 0.7034186147 * m + 1.707614701 * s,
  ];
  return lin.map((c) => {
    const clamped = Math.max(0, Math.min(1, c));
    return clamped > 0.0031308 ? 1.055 * clamped ** (1 / 2.4) - 0.055 : 12.92 * clamped;
  }) as Rgb;
}

export function toHex([r, g, b]: Rgb): string {
  const byte = (c: number) =>
    Math.round(c * 255)
      .toString(16)
      .padStart(2, "0");
  return `#${byte(r)}${byte(g)}${byte(b)}`;
}

export function luminance([r, g, b]: Rgb): number {
  const lin = (c: number) => (c <= 0.04045 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4);
  return 0.2126 * lin(r) + 0.7152 * lin(g) + 0.0722 * lin(b);
}

export function contrast(fg: Rgb, bg: Rgb): number {
  const l1 = luminance(fg);
  const l2 = luminance(bg);
  const hi = Math.max(l1, l2);
  const lo = Math.min(l1, l2);
  return (hi + 0.05) / (lo + 0.05);
}

/** Gamma-space alpha blend, the way browsers composite translucent color. */
export function blend(fg: Rgb, alpha: number, bg: Rgb): Rgb {
  return fg.map((c, i) => c * alpha + bg[i] * (1 - alpha)) as Rgb;
}

/* ------------------------------------------------------------------ *
 * CSS parsing: custom-property declarations per selector scope.
 * ------------------------------------------------------------------ */

export type Scope = Record<string, string>;

export function parseBlocks(css: string): Array<{ selector: string; body: string }> {
  // Strip comments first — otherwise their prose ("--surface", ":root")
  // pollutes selectors and declaration bodies.
  const source = css.replace(/\/\*[\s\S]*?\*\//g, "");
  const blocks: Array<{ selector: string; body: string }> = [];
  let depth = 0;
  let selector = "";
  let body = "";
  for (const ch of source) {
    if (ch === "{") {
      if (depth === 0) {
        selector = selector.trim();
        body = "";
      } else {
        body += ch;
      }
      depth += 1;
    } else if (ch === "}") {
      depth -= 1;
      if (depth === 0) {
        blocks.push({ selector, body });
        selector = "";
      } else body += ch;
    } else if (depth === 0) {
      selector += ch;
    } else {
      body += ch;
    }
  }
  return blocks;
}

export function parseDeclarations(body: string): Scope {
  const scope: Scope = {};
  const re = /(--[\w-]+)\s*:\s*([^;]+);/g;
  for (const [, name, value] of body.matchAll(re)) scope[name] = value.trim();
  return scope;
}
