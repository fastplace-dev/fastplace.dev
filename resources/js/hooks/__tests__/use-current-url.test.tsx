import "@testing-library/jest-dom/vitest";
import { cleanup, renderHook } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";
import React from "react";
import { FastplaceProvider, router, type Page } from "@fastplace/react";
import { useCurrentUrl } from "../use-current-url";

// The bridge page store is module-global — reset it between tests so each
// provider render adopts its own initialPage instead of the previous one.
// (router.reset exists exactly for this test-time purpose.)
function renderAtUrl(url: string) {
  const initialPage: Page = { component: "Settings/Security", props: {}, url };
  const wrapper = ({ children }: { children: React.ReactNode }) => (
    <FastplaceProvider initialPage={initialPage}>{children}</FastplaceProvider>
  );
  return renderHook(() => useCurrentUrl(), { wrapper });
}

afterEach(() => {
  cleanup();
  router.reset();
});

describe("useCurrentUrl", () => {
  it("exposes the current page's pathname", () => {
    const { result } = renderAtUrl("/settings/profile");

    expect(result.current.currentUrl).toBe("/settings/profile");
  });

  it("strips the query string before comparing", () => {
    const { result } = renderAtUrl("/settings?tab=security");

    expect(result.current.currentUrl).toBe("/settings");
    expect(result.current.isCurrentUrl("/settings")).toBe(true);
  });

  it("matches an exact url and rejects a non-match", () => {
    const { result } = renderAtUrl("/settings");

    expect(result.current.isCurrentUrl("/settings")).toBe(true);
    expect(result.current.isCurrentUrl("/projects")).toBe(false);
  });

  it("treats a url as current when it prefixes the page url", () => {
    const { result } = renderAtUrl("/settings/profile");

    expect(result.current.isCurrentOrParentUrl("/settings")).toBe(true);
    expect(result.current.isCurrentOrParentUrl("/settings/profile")).toBe(true);
    expect(result.current.isCurrentOrParentUrl("/dashboard")).toBe(false);
  });

  it("compares the pathname of an absolute url", () => {
    const { result } = renderAtUrl("/settings");

    expect(result.current.isCurrentUrl("http://localhost:3000/settings")).toBe(true);
    expect(result.current.isCurrentUrl("http://localhost:3000/elsewhere")).toBe(false);
  });

  it("honors an explicit currentUrl override", () => {
    const { result } = renderAtUrl("/settings");

    expect(result.current.isCurrentUrl("/settings", "/projects")).toBe(false);
    expect(result.current.isCurrentUrl("/projects", "/projects")).toBe(true);
  });

  it("branches on the current url", () => {
    const { result } = renderAtUrl("/settings");

    expect(result.current.whenCurrentUrl("/settings", "active", "inactive")).toBe("active");
    expect(result.current.whenCurrentUrl("/projects", "active", "inactive")).toBe("inactive");
    expect(result.current.whenCurrentUrl("/projects", "active")).toBeNull();
  });
});
