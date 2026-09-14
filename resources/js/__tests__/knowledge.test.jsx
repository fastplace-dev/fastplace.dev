import "@testing-library/jest-dom/vitest";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import React from "react";
import { FastplaceProvider, router } from "@fastplace/react";
import KnowledgeIndex from "../pages/Knowledge/Index.jsx";

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  window.history.replaceState(null, "", "/");
  router.reset();
});

function renderPage(props) {
  render(
    <FastplaceProvider initialPage={{ component: "Knowledge/Index", props, url: "/knowledge" }}>
      <KnowledgeIndex />
    </FastplaceProvider>,
  );
}

function bridgePayload(props) {
  return {
    ok: true,
    headers: new Headers({ "content-type": "application/json" }),
    redirected: false,
    json: () => Promise.resolve({ component: "Knowledge/Index", props, url: "/knowledge" }),
  };
}

describe("Knowledge/Index", () => {
  it("seeds the search box from the server query", () => {
    renderPage({ q: "postgres", items: [{ id: 1, title: "pgvector intro" }] });
    const input = screen.getByRole("searchbox");
    expect(input).toHaveValue("postgres");
    expect(screen.getByText("pgvector intro")).toBeInTheDocument();
  });

  it("re-syncs the search box when a bridge navigation clears q", async () => {
    // Searching then clicking the nav's Knowledge link navigates to the
    // same component with q="" — the input must follow the new payload,
    // not keep the stale local state.
    renderPage({ q: "postgres", items: [] });
    expect(screen.getByRole("searchbox")).toHaveValue("postgres");

    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(bridgePayload({ q: "", items: [] })));
    await router.visit("/knowledge");

    await waitFor(() => expect(screen.getByRole("searchbox")).toHaveValue(""));
  });
});
