import { afterEach, describe, expect, it, vi } from "vitest";
import React from "react";
import { createFastplaceApp } from "../index";

/* ------------------------------------------------------------------ *
 * Dev-mode CJS interop for the bootstrap's dynamic react-dom/client
 * import. Vite's dependency optimizer pre-bundles the CommonJS
 * react-dom/client with esbuild, and esbuild's facade surfaces it only
 * as `default` — so a plain named destructure of the dynamic import
 * resolves to undefined in dev ("o is not a function" on bootstrap).
 * These tests replay both pre-bundle namespace shapes through the real
 * code path: vi.mock intercepts the dynamic import() inside
 * createFastplaceApp.
 * ------------------------------------------------------------------ */

const defaultOnly = vi.hoisted(() => {
  const render = vi.fn();
  const createRoot = vi.fn(() => ({ render }));
  return { render, createRoot };
});

// esbuild's pre-bundle shape: the CJS facade exports only `default`, so the
// named key reads as undefined (verified against the real artifact in the
// bug report). Declaring it undefined keeps vitest's mock-proxy guard from
// turning the read itself into a throw.
vi.mock("react-dom/client", () => ({
  createRoot: undefined,
  default: { createRoot: defaultOnly.createRoot },
}));

const bothShapes = vi.hoisted(() => {
  const make = () => {
    const render = vi.fn();
    const createRoot = vi.fn(() => ({ render }));
    return { render, createRoot };
  };
  return { named: make(), viaDefault: make() };
});

const StubPage = () => <div>stub</div>;

function seedMountElement(): HTMLElement {
  const el = document.createElement("div");
  el.id = "fastplace";
  el.dataset.page = JSON.stringify({
    component: "Dashboard/Index",
    props: { user: "Firoz" },
    url: "/dashboard",
    version: "v1",
  });
  document.body.appendChild(el);
  return el;
}

describe("bootstrap react-dom/client interop", () => {
  afterEach(() => {
    document.body.innerHTML = "";
    vi.resetModules();
    vi.doUnmock("react-dom/client");
  });

  it("bootstraps when the served namespace is default-only (esbuild pre-bundle shape)", async () => {
    const el = seedMountElement();

    await expect(createFastplaceApp({ resolve: () => StubPage })).resolves.toBeDefined();

    expect(defaultOnly.createRoot).toHaveBeenCalledTimes(1);
    expect(defaultOnly.createRoot).toHaveBeenCalledWith(el);
    expect(defaultOnly.render).toHaveBeenCalledTimes(1);
  });

  it("prefers the named createRoot when both export shapes exist", async () => {
    // Isolated module registry: doMock registers the both-shapes namespace
    // and resetModules forces ../index to re-evaluate against it.
    vi.resetModules();
    vi.doMock("react-dom/client", () => ({
      createRoot: bothShapes.named.createRoot,
      default: { createRoot: bothShapes.viaDefault.createRoot },
    }));
    const { createFastplaceApp: freshApp } = await import("../index");

    const el = seedMountElement();

    await expect(freshApp({ resolve: () => StubPage })).resolves.toBeDefined();

    expect(bothShapes.named.createRoot).toHaveBeenCalledWith(el);
    expect(bothShapes.viaDefault.createRoot).not.toHaveBeenCalled();
    expect(bothShapes.named.render).toHaveBeenCalledTimes(1);
  });
});
