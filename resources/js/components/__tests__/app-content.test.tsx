import "@testing-library/jest-dom/vitest";
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";
import React from "react";

import { AppContent } from "../app-content";

afterEach(() => {
  cleanup();
});

describe("AppContent", () => {
  it("renders children inside SidebarInset (main[data-slot='sidebar-inset']) by default", () => {
    render(<AppContent>curve</AppContent>);

    const inset = document.querySelector("main[data-slot='sidebar-inset']");
    expect(inset).not.toBeNull();
    expect(inset).toContainElement(screen.getByText("curve"));
  });

  it("renders a centered plain main for the header variant", () => {
    render(<AppContent variant="header">plain</AppContent>);

    expect(document.querySelector("[data-slot='sidebar-inset']")).toBeNull();
    const main = screen.getByRole("main");
    expect(main).toHaveTextContent("plain");
    expect(main.className).toContain("max-w-7xl");
    expect(main.className).toContain("flex-1");
  });

  it("spreads extra props onto the rendered main in both variants", () => {
    const { rerender } = render(
      <AppContent id="content-slot" className="gap-8">
        one
      </AppContent>,
    );

    let main = screen.getByRole("main");
    expect(main).toHaveAttribute("id", "content-slot");
    expect(main.className).toContain("gap-8");

    rerender(
      <AppContent variant="header" id="content-slot" className="gap-8">
        two
      </AppContent>,
    );

    main = screen.getByRole("main");
    expect(main).toHaveAttribute("id", "content-slot");
    expect(main.className).toContain("gap-8");
    expect(main).toHaveTextContent("two");
  });
});
