import "@testing-library/jest-dom/vitest";
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";
import React from "react";

import { FastplaceProvider, router } from "@fastplace/react";

import type { User } from "@/types";
import Home from "../Home/Index";

/* ------------------------------------------------------------------ *
 * Helpers
 * ------------------------------------------------------------------ */

const testUser: User = {
  id: 1,
  name: "Jane Doe",
  email: "jane@example.com",
  email_verified_at: null,
  created_at: "2026-01-01T00:00:00Z",
  updated_at: "2026-01-01T00:00:00Z",
};

function renderHome(auth?: { user: User }) {
  return render(
    <FastplaceProvider
      initialPage={{
        component: "Home/Index",
        url: "/",
        props: auth ? { auth } : {},
      }}
    >
      <Home />
    </FastplaceProvider>,
  );
}

afterEach(() => {
  cleanup();
  // The bridge page store is module-level; reset it so each test mounts fresh props.
  router.reset();
});

/* ------------------------------------------------------------------ *
 * Home page
 * ------------------------------------------------------------------ */

describe("Home page", () => {
  it("sets the document title", () => {
    renderHome();

    expect(document.title).toBe("Home");
  });

  it("renders the getting-started card with its suggestions", () => {
    renderHome();

    expect(
      screen.getByRole("heading", { level: 1, name: "Let's get started" }),
    ).toBeInTheDocument();
    // The intro paragraph is split by a <br />, so match each line loosely.
    expect(screen.getByText(/has an incredibly rich ecosystem/)).toBeInTheDocument();
    expect(screen.getByText(/We suggest starting with the following/)).toBeInTheDocument();
    expect(screen.getByText("Read the Documentation")).toBeInTheDocument();
    expect(screen.getByText("Watch video tutorials")).toBeInTheDocument();
  });

  it("keeps the suggestions as plain text — no outbound ecosystem links", () => {
    renderHome();

    expect(screen.getByText("Read the Documentation").closest("a")).toBeNull();
    expect(screen.getByText("Watch video tutorials").closest("a")).toBeNull();
  });

  it("shows the framework logo instead of a generic flash icon", () => {
    renderHome();

    const logo = screen.getByRole("img", { name: "Fastplace" });
    expect(logo).toHaveAttribute("src", "/fastplace-logo.svg");
  });

  it("shows log in and register links for guests", () => {
    renderHome();

    expect(screen.getByRole("link", { name: "Log in" })).toHaveAttribute("href", "/login");
    expect(screen.getByRole("link", { name: "Register" })).toHaveAttribute("href", "/register");
    expect(screen.queryByRole("link", { name: "Dashboard" })).not.toBeInTheDocument();
  });

  it("links authenticated users to the dashboard", () => {
    renderHome({ user: testUser });

    expect(screen.getByRole("link", { name: "Dashboard" })).toHaveAttribute("href", "/dashboard");
    expect(screen.queryByRole("link", { name: "Log in" })).not.toBeInTheDocument();
    expect(screen.queryByRole("link", { name: "Register" })).not.toBeInTheDocument();
  });
});
