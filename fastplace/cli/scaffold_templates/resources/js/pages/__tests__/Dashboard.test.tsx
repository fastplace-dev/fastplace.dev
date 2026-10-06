import "@testing-library/jest-dom/vitest";
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";
import React from "react";

import { FastplaceProvider, router } from "@fastplace/react";

import Dashboard from "../Dashboard/Index";

function renderDashboard() {
  return render(
    <FastplaceProvider
      initialPage={{
        component: "Dashboard/Index",
        url: "/dashboard",
        props: {},
      }}
    >
      <Dashboard />
    </FastplaceProvider>,
  );
}

afterEach(() => {
  cleanup();
  // The bridge page store is module-level; reset it so each test mounts fresh props.
  router.reset();
});

describe("Dashboard page", () => {
  it("sets the document title", () => {
    renderDashboard();

    expect(document.title).toBe("Dashboard");
  });

  it("renders the welcome card content", () => {
    renderDashboard();

    expect(screen.getByText("Your dashboard is ready")).toBeInTheDocument();
    expect(screen.getByText(/The first account you registered is the admin/)).toBeInTheDocument();
  });

  it("pads the page so the card does not sit flush against the shell edges", () => {
    renderDashboard();

    const card = screen.getByText("Your dashboard is ready").closest('[data-slot="card"]');
    expect(card).not.toBeNull();
    expect(card?.parentElement).toHaveClass("px-4", "py-6");
  });
});
