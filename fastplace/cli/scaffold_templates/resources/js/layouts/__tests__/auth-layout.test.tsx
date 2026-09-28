import "@testing-library/jest-dom/vitest";
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";
import React from "react";

import { FastplaceProvider, router } from "@fastplace/react";

import AuthLayout from "../auth-layout";

// The bridge keeps a module-level current page; reset it so each test's
// initialPage wins instead of whichever provider mounted first.
afterEach(() => {
  cleanup();
  router.reset();
});

function renderLayout(
  props: Omit<React.ComponentProps<typeof AuthLayout>, "children">,
  children?: React.ReactNode,
) {
  return render(
    <FastplaceProvider
      initialPage={{ component: "Auth/Login", url: "/login", props: { name: "Fastplace" } }}
    >
      <AuthLayout {...props}>{children}</AuthLayout>
    </FastplaceProvider>,
  );
}

describe("AuthLayout", () => {
  it("delegates to the simple layout and renders the title as a level-1 heading", () => {
    renderLayout({ title: "Sign in", description: "Enter your credentials" });

    const heading = screen.getByRole("heading", { level: 1, name: "Sign in" });
    expect(heading).toHaveTextContent("Sign in");
    expect(screen.getByText("Enter your credentials")).toBeInTheDocument();
  });

  it("renders children inside the centered layout shell", () => {
    renderLayout({ title: "Sign in" }, <button type="submit">Continue</button>);

    expect(screen.getByRole("button", { name: "Continue" })).toBeInTheDocument();
  });

  it("links the logo back to the home page", () => {
    renderLayout({ title: "Sign in" });

    // Pin by accessible name — the skip link is also a link in the tree.
    const link = screen.getByRole("link", { name: "Sign in" });
    expect(link).toHaveAttribute("href", "/");
    expect(link.querySelector("svg")).not.toBeNull();
  });

  it("defaults title and description to empty strings without crashing", () => {
    renderLayout({}, <p>Form body</p>);

    expect(screen.getByText("Form body")).toBeInTheDocument();
  });
});
