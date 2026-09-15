import "@testing-library/jest-dom/vitest";
import { cleanup, render, screen, within } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";
import React from "react";

import { Breadcrumbs } from "../breadcrumbs";

afterEach(() => {
  cleanup();
});

describe("Breadcrumbs", () => {
  it("renders nothing for an empty list", () => {
    const { container } = render(<Breadcrumbs breadcrumbs={[]} />);

    expect(screen.queryByRole("navigation")).toBeNull();
    expect(container).toBeEmptyDOMElement();
  });

  it("renders a breadcrumb navigation landmark wrapping the trail", () => {
    render(
      <Breadcrumbs
        breadcrumbs={[
          { title: "Dashboard", href: "/dashboard" },
          { title: "Projects", href: "/projects" },
        ]}
      />,
    );

    expect(screen.getByRole("navigation", { name: "breadcrumb" })).toBeInTheDocument();
  });

  it("links every crumb except the last, which is marked as the current page", () => {
    render(
      <Breadcrumbs
        breadcrumbs={[
          { title: "Dashboard", href: "/dashboard" },
          { title: "Projects", href: "/projects" },
          { title: "Fastplace", href: "/projects/fastplace" },
        ]}
      />,
    );

    const nav = screen.getByRole("navigation", { name: "breadcrumb" });

    // Leading crumbs are bridge links carrying their href.
    const dashboard = within(nav).getByRole("link", { name: "Dashboard" });
    expect(dashboard).toHaveAttribute("href", "/dashboard");
    expect(dashboard).toHaveAttribute("data-fastplace-link");
    const projects = within(nav).getByRole("link", { name: "Projects" });
    expect(projects).toHaveAttribute("href", "/projects");

    // The final crumb is the current page — a disabled, non-navigable marker,
    // never an anchor (the ui primitive uses role="link" + aria-disabled).
    const current = within(nav).getByText("Fastplace");
    expect(current).toHaveAttribute("aria-current", "page");
    expect(current).toHaveAttribute("aria-disabled", "true");
    expect(current.tagName).toBe("SPAN");
    expect(current.closest("a")).toBeNull();
    expect(nav.querySelectorAll("a[data-fastplace-link]")).toHaveLength(2);
  });

  it("places a separator between each pair of crumbs only", () => {
    render(
      <Breadcrumbs
        breadcrumbs={[
          { title: "Dashboard", href: "/dashboard" },
          { title: "Projects", href: "/projects" },
          { title: "Fastplace", href: "/projects/fastplace" },
        ]}
      />,
    );

    const list = screen.getByRole("navigation", { name: "breadcrumb" }).firstElementChild;
    expect(list?.tagName).toBe("OL");
    // Separators are aria-hidden presentation markers — query by slot.
    expect(list?.querySelectorAll("[data-slot='breadcrumb-separator']")).toHaveLength(2);
  });

  it("renders a single crumb as the current page with no links or separators", () => {
    render(<Breadcrumbs breadcrumbs={[{ title: "Settings", href: "/settings" }]} />);

    const nav = screen.getByRole("navigation", { name: "breadcrumb" });
    expect(screen.getByText("Settings")).toHaveAttribute("aria-current", "page");
    expect(nav.querySelector("a")).toBeNull();
    expect(nav.querySelector("[data-slot='breadcrumb-separator']")).toBeNull();
  });
});
