import "@testing-library/jest-dom/vitest";
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";
import React from "react";

import { FastplaceProvider, router } from "@fastplace/react";

import AuthSimpleLayout from "@/layouts/auth/auth-simple-layout";

// The bridge keeps a module-level current page; reset it so each test's
// initialPage wins instead of whichever provider mounted first.
afterEach(() => {
  cleanup();
  router.reset();
});

function renderLayout(props: React.ComponentProps<typeof AuthSimpleLayout>) {
  return render(
    <FastplaceProvider
      initialPage={{ component: "Auth/Login", url: "/login", props: { name: "Fastplace" } }}
    >
      <AuthSimpleLayout {...props} />
    </FastplaceProvider>,
  );
}

describe("AuthSimpleLayout", () => {
  it("renders the title as a level-1 heading and the description below it", () => {
    renderLayout({ title: "Create account", description: "Get started with Fastplace" });

    expect(screen.getByRole("heading", { level: 1, name: "Create account" })).toBeInTheDocument();
    expect(screen.getByText("Get started with Fastplace")).toBeInTheDocument();
  });

  it("renders the logo icon inside a link to the home page", () => {
    const { container } = renderLayout({ title: "Sign in" });

    // The logo link's accessible name comes from its sr-only title span —
    // pin it by name so the skip link (also a link) doesn't collide.
    const link = screen.getByRole("link", { name: "Sign in" });
    expect(link).toHaveAttribute("href", "/");
    expect(link.querySelector("svg")).not.toBeNull();
    expect(link.querySelector("span.sr-only")).toHaveTextContent("Sign in");
    expect(container.querySelector(".min-h-svh")).not.toBeNull();
  });

  it("renders children after the heading block", () => {
    const { container } = renderLayout({ title: "Sign in", children: <form>form-body</form> });

    const form = container.querySelector("form");
    expect(form).not.toBeNull();
    expect(form).toHaveTextContent("form-body");
  });

  it("wraps the card in a main landmark targeted by a skip link (a11y1-G2)", () => {
    const { container } = renderLayout({ title: "Sign in", children: <form>form-body</form> });

    const main = container.querySelector("main");
    const form = container.querySelector("form");
    expect(main).not.toBeNull();
    expect(main).toHaveAttribute("id", "main-content");
    expect(main).toContainElement(form as HTMLElement);

    const skip = screen.getByRole("link", { name: "Skip to content" });
    expect(skip).toHaveAttribute("href", "#main-content");
    expect(
      skip.compareDocumentPosition(main as HTMLElement) & Node.DOCUMENT_POSITION_FOLLOWING,
    ).toBeTruthy();
  });
});
