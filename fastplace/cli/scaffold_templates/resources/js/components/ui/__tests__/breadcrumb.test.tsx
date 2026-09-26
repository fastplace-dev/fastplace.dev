import "@testing-library/jest-dom/vitest";
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";
import React from "react";

import {
  Breadcrumb,
  BreadcrumbEllipsis,
  BreadcrumbItem,
  BreadcrumbLink,
  BreadcrumbList,
  BreadcrumbPage,
  BreadcrumbSeparator,
} from "../breadcrumb";

afterEach(cleanup);

describe("Breadcrumb", () => {
  it("renders a navigation landmark labelled as a breadcrumb trail", () => {
    render(
      <Breadcrumb>
        <BreadcrumbList>
          <BreadcrumbItem>
            <BreadcrumbLink href="/home">Home</BreadcrumbLink>
          </BreadcrumbItem>
        </BreadcrumbList>
      </Breadcrumb>,
    );
    const nav = screen.getByRole("navigation");
    expect(nav).toHaveAttribute("aria-label", "breadcrumb");
    expect(nav).toHaveAttribute("data-slot", "breadcrumb");
  });

  it("renders a full trail with a link, separator, and current page", () => {
    const { container } = render(
      <Breadcrumb>
        <BreadcrumbList>
          <BreadcrumbItem>
            <BreadcrumbLink href="/home">Home</BreadcrumbLink>
          </BreadcrumbItem>
          <BreadcrumbSeparator />
          <BreadcrumbItem>
            <BreadcrumbPage>Settings</BreadcrumbPage>
          </BreadcrumbItem>
        </BreadcrumbList>
      </Breadcrumb>,
    );

    const link = screen.getByRole("link", { name: "Home" });
    expect(link).toHaveAttribute("href", "/home");
    expect(link).toHaveAttribute("data-slot", "breadcrumb-link");
    expect(link.className).toContain("hover:text-foreground");

    const item = container.querySelector('[data-slot="breadcrumb-item"]');
    expect(item).toBeInTheDocument();
    expect(item?.tagName).toBe("LI");

    const separator = container.querySelector('[data-slot="breadcrumb-separator"]');
    expect(separator).toBeInTheDocument();
    expect(separator?.tagName).toBe("LI");
    expect(separator).toHaveAttribute("aria-hidden", "true");
    expect(separator?.querySelector("svg")).toBeInTheDocument();

    const page = screen.getByRole("link", { name: "Settings" });
    expect(page).toHaveAttribute("aria-current", "page");
    expect(page).toHaveAttribute("aria-disabled", "true");
    expect(page).toHaveAttribute("data-slot", "breadcrumb-page");
    expect(page.className).toContain("text-foreground");
  });

  it("renders a custom separator icon when children are provided", () => {
    render(
      <BreadcrumbSeparator>
        <span data-testid="slash">/</span>
      </BreadcrumbSeparator>,
    );
    expect(screen.getByTestId("slash")).toBeInTheDocument();
  });

  it("exposes the ellipsis to screen readers as More", () => {
    const { container } = render(<BreadcrumbEllipsis />);
    const ellipsis = container.querySelector('[data-slot="breadcrumb-ellipsis"]');
    expect(ellipsis).toHaveAttribute("aria-hidden", "true");
    expect(ellipsis?.querySelector("svg")).toBeInTheDocument();
    expect(screen.getByText("More")).toBeInTheDocument();
  });

  it("merges a custom className onto the list", () => {
    render(<BreadcrumbList className="gap-4" />);
    const list = screen.getByRole("list");
    expect(list.className).toContain("gap-4");
    expect(list.className).toContain("text-muted-foreground");
    expect(list.className).toContain("sm:gap-2.5");
    expect(list.className).not.toContain("gap-1.5");
  });

  it("renders its child element instead of an anchor when asChild", () => {
    render(
      <BreadcrumbLink asChild>
        <a href="/projects">Projects</a>
      </BreadcrumbLink>,
    );
    const link = screen.getByRole("link", { name: "Projects" });
    expect(link).toHaveAttribute("href", "/projects");
    expect(link).toHaveAttribute("data-slot", "breadcrumb-link");
    expect(link.className).toContain("hover:text-foreground");
  });
});
