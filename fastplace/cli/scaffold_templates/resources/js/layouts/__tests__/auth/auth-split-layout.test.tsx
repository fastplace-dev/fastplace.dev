import "@testing-library/jest-dom/vitest";
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";
import React from "react";

import { FastplaceProvider, router } from "@fastplace/react";

import AuthSplitLayout from "@/layouts/auth/auth-split-layout";

// The bridge keeps a module-level current page; reset it so each test's
// initialPage wins instead of whichever provider mounted first.
afterEach(() => {
  cleanup();
  router.reset();
});

function renderLayout(props: React.ComponentProps<typeof AuthSplitLayout>) {
  return render(
    <FastplaceProvider initialPage={{ component: "Auth/Login", url: "/login", props: { name } }}>
      <AuthSplitLayout {...props} />
    </FastplaceProvider>,
  );
}

const name = "Fastplace";

describe("AuthSplitLayout", () => {
  it("shows the app name from page props in the side panel link", () => {
    renderLayout({ title: "Welcome back" });

    const links = screen.getAllByRole("link");
    const panelLink = links.find((link) => link.textContent?.includes("Fastplace"));
    expect(panelLink).toBeDefined();
    expect(panelLink).toHaveAttribute("href", "/");
  });

  it("renders the title as a level-1 heading with the description", () => {
    renderLayout({ title: "Welcome back", description: "Sign in to continue" });

    expect(screen.getByRole("heading", { level: 1, name: "Welcome back" })).toBeInTheDocument();
    expect(screen.getByText("Sign in to continue")).toBeInTheDocument();
  });

  it("renders both desktop and mobile logo links pointing home", () => {
    renderLayout({ title: "Welcome back" });

    const links = screen.getAllByRole("link");
    expect(links).toHaveLength(2);
    links.forEach((link) => expect(link).toHaveAttribute("href", "/"));
    links.forEach((link) => expect(link.querySelector("svg")).not.toBeNull());
  });

  it("renders children in the form column", () => {
    const { container } = renderLayout({
      title: "Welcome back",
      children: <form>split-body</form>,
    });

    const form = container.querySelector("form");
    expect(form).not.toBeNull();
    expect(form).toHaveTextContent("split-body");
  });
});
