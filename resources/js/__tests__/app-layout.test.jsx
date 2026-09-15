import "@testing-library/jest-dom/vitest";
import { cleanup, render, screen, within } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";
import React from "react";
import { FastplaceProvider, router } from "@fastplace/react";
import AppLayout from "../layouts/AppLayout.jsx";

afterEach(() => {
  cleanup();
  router.reset();
});

function renderAt(url) {
  render(
    <FastplaceProvider initialPage={{ component: "Dashboard/Index", props: {}, url }}>
      <AppLayout>
        <p>content</p>
      </AppLayout>
    </FastplaceProvider>,
  );
}

describe("AppLayout", () => {
  it("renders the brand link and the primary nav", () => {
    renderAt("/");
    expect(screen.getByRole("link", { name: "Fastplace" })).toHaveAttribute("href", "/dashboard");
    const nav = screen.getByRole("navigation", { name: "Primary" });
    for (const label of ["Dashboard", "Projects", "Knowledge", "Assistant", "About"]) {
      expect(within(nav).getByRole("link", { name: label })).toBeInTheDocument();
    }
  });

  it("marks the current section with aria-current", () => {
    renderAt("/projects");
    expect(screen.getByRole("link", { name: "Projects" })).toHaveAttribute("aria-current", "page");
    expect(screen.getByRole("link", { name: "Dashboard" })).not.toHaveAttribute("aria-current");
  });

  it("renders its children as the content slot", () => {
    renderAt("/");
    expect(screen.getByText("content")).toBeInTheDocument();
  });
});
