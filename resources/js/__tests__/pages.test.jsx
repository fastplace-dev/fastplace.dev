import "@testing-library/jest-dom/vitest";
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";
import React from "react";
import { FastplaceProvider, router } from "@fastplace/react";
import DashboardIndex from "../pages/Dashboard/Index.jsx";
import AboutIndex from "../pages/About/Index.jsx";

// Collocated app tests under resources/js must be discovered by vitest —
// this file proves the include pattern in vite.config.js covers them.

// The page store is module-level; reset it so each render starts fresh.
afterEach(() => {
  cleanup();
  router.reset();
});

describe("app pages render with controller-supplied props", () => {
  it("Dashboard/Index shows the empty state", () => {
    render(
      <FastplaceProvider
        initialPage={{
          component: "Dashboard/Index",
          props: { appName: "Fastplace", projects: [], url: "/" },
          url: "/",
        }}
      >
        <DashboardIndex />
      </FastplaceProvider>,
    );
    expect(screen.getByRole("heading", { name: "Fastplace" })).toBeInTheDocument();
    expect(screen.getByText("No projects yet.")).toBeInTheDocument();
  });

  it("About/Index echoes the framework name", () => {
    render(
      <FastplaceProvider
        initialPage={{
          component: "About/Index",
          props: { url: "/about", framework: "fastplace" },
          url: "/about",
        }}
      >
        <AboutIndex />
      </FastplaceProvider>,
    );
    expect(screen.getByRole("heading", { name: "About/Index" })).toBeInTheDocument();
    // The scaffolded page dumps props as JSON — match within that block.
    expect(screen.getByText(/fastplace/)).toBeInTheDocument();
  });
});
