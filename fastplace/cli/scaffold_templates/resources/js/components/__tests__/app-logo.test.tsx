import "@testing-library/jest-dom/vitest";
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";
import React from "react";

import { FastplaceProvider, router } from "@fastplace/react";

import AppLogo from "../app-logo";

// The bridge keeps a module-level current page; reset it so each test's
// initialPage wins instead of whichever provider mounted first.
afterEach(() => {
  cleanup();
  router.reset();
});

function renderWithAppName(name: string) {
  return render(
    <FastplaceProvider initialPage={{ component: "Dashboard/Index", url: "/", props: { name } }}>
      <AppLogo />
    </FastplaceProvider>,
  );
}

describe("AppLogo", () => {
  it("shows the app name from the page props", () => {
    renderWithAppName("Fastplace");

    const label = screen.getByText("Fastplace");
    expect(label.tagName).toBe("SPAN");
    expect(label).toHaveClass("font-semibold", "truncate");
  });

  it("renders the mark inside a sidebar-primary badge", () => {
    const { container } = renderWithAppName("Fastplace");

    const badge = container.querySelector("div.bg-sidebar-primary");
    expect(badge).not.toBeNull();
    expect(badge).toHaveClass("text-sidebar-primary-foreground", "rounded-md");

    const svg = badge?.querySelector("svg");
    expect(svg).not.toBeNull();
    expect(svg).toHaveAttribute("viewBox", "0 0 417.27 535.81");
    expect(svg).toHaveClass("fill-current");
    expect(svg).toHaveClass("size-6");
  });

  it("falls back to appName when the page props carry no name", () => {
    render(
      <FastplaceProvider
        initialPage={{
          component: "Dashboard/Index",
          url: "/",
          props: { appName: "Configured Name" },
        }}
      >
        <AppLogo />
      </FastplaceProvider>,
    );

    expect(screen.getByText("Configured Name")).toBeInTheDocument();
  });

  it("falls back to the brand name when neither name nor appName is present", () => {
    render(
      <FastplaceProvider initialPage={{ component: "Settings/Appearance", url: "/", props: {} }}>
        <AppLogo />
      </FastplaceProvider>,
    );

    expect(screen.getByText("Fastplace")).toBeInTheDocument();
  });

  it("reflects a different app name when the page props change", () => {
    renderWithAppName("Acme Portal");

    expect(screen.getByText("Acme Portal")).toBeInTheDocument();
    expect(screen.queryByText("Fastplace")).not.toBeInTheDocument();
  });
});
