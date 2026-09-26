import "@testing-library/jest-dom/vitest";
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";
import React from "react";

import { FastplaceProvider, router } from "@fastplace/react";

import AuthCardLayout from "@/layouts/auth/auth-card-layout";

// The bridge keeps a module-level current page; reset it so each test's
// initialPage wins instead of whichever provider mounted first.
afterEach(() => {
  cleanup();
  router.reset();
});

function renderLayout(props: React.ComponentProps<typeof AuthCardLayout>) {
  return render(
    <FastplaceProvider
      initialPage={{ component: "Auth/Login", url: "/login", props: { name: "Fastplace" } }}
    >
      <AuthCardLayout {...props} />
    </FastplaceProvider>,
  );
}

describe("AuthCardLayout", () => {
  it("renders the title and description inside the card header", () => {
    renderLayout({ title: "Confirm password", description: "This is a secure area" });

    expect(screen.getByText("Confirm password")).toBeInTheDocument();
    expect(screen.getByText("This is a secure area")).toBeInTheDocument();
  });

  it("composes the ui card with header and content slots", () => {
    const { container } = renderLayout({ title: "Sign in" });

    const card = container.querySelector('[data-slot="card"]');
    expect(card).not.toBeNull();
    expect(card?.querySelector('[data-slot="card-header"]')).not.toBeNull();
    expect(card?.querySelector('[data-slot="card-content"]')).not.toBeNull();
    expect(container.querySelector(".min-h-svh")).not.toBeNull();
  });

  it("renders the logo icon inside a link to the home page", () => {
    renderLayout({ title: "Sign in" });

    const link = screen.getByRole("link");
    expect(link).toHaveAttribute("href", "/");
    expect(link.querySelector("svg")).not.toBeNull();
  });

  it("renders children inside the card content", () => {
    const { container } = renderLayout({
      title: "Sign in",
      children: <div>card-body</div>,
    });

    const content = container.querySelector('[data-slot="card-content"]');
    expect(content).not.toBeNull();
    expect(content).toHaveTextContent("card-body");
  });
});
