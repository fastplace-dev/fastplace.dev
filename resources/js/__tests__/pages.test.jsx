import "@testing-library/jest-dom/vitest";
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";
import React from "react";
import { FastplaceProvider, router } from "@fastplace/react";
import AboutIndex from "../pages/About/Index.jsx";

// Collocated app tests under resources/js must be discovered by vitest —
// this file proves the include pattern in vite.config.js covers them.

// The page store is module-level; reset it so each render starts fresh.
afterEach(() => {
  cleanup();
  router.reset();
});

describe("scaffolded pages render with controller-supplied props", () => {
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
