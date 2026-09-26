import "@testing-library/jest-dom/vitest";
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import React from "react";

import { UserInfo } from "../user-info";
import type { User } from "@/types";

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

const user: User = {
  id: 1,
  name: "Firoz Anam",
  email: "firoz@example.com",
  avatar: undefined,
  email_verified_at: null,
  created_at: "2026-01-01T00:00:00Z",
  updated_at: "2026-01-01T00:00:00Z",
};

describe("UserInfo", () => {
  it("renders the user's name with the avatar initials fallback", () => {
    render(<UserInfo user={user} />);

    expect(screen.getByText("Firoz Anam")).toBeInTheDocument();
    // useInitials collapses "Firoz Anam" to first + last initial.
    const fallback = screen.getByText("FA");
    expect(fallback.closest("[data-slot='avatar-fallback']")).not.toBeNull();
  });

  it("hides the email by default and shows it for showEmail", () => {
    const { rerender } = render(<UserInfo user={user} />);
    expect(screen.queryByText("firoz@example.com")).not.toBeInTheDocument();

    rerender(<UserInfo user={user} showEmail={true} />);
    expect(screen.getByText("firoz@example.com")).toBeInTheDocument();
    expect(screen.getByText("firoz@example.com")).toHaveClass("text-ink-muted");
  });

  it("renders the avatar image with src and alt when the user has one", () => {
    vi.spyOn(HTMLImageElement.prototype, "complete", "get").mockReturnValue(true);
    vi.spyOn(HTMLImageElement.prototype, "naturalWidth", "get").mockReturnValue(96);

    render(<UserInfo user={{ ...user, avatar: "https://example.com/photo.png" }} />);

    const image = screen.getByAltText("Firoz Anam");
    expect(image).toHaveAttribute("data-slot", "avatar-image");
    expect(image).toHaveAttribute("src", "https://example.com/photo.png");
    // Loaded image replaces the initials fallback.
    expect(screen.queryByText("FA")).not.toBeInTheDocument();
  });

  it("keeps the compact avatar sizing on the avatar root", () => {
    render(<UserInfo user={user} />);

    const root = screen.getByText("FA").closest("[data-slot='avatar']");
    expect(root).not.toBeNull();
    expect(root).toHaveClass("h-8");
    expect(root).toHaveClass("w-8");
    expect(root).toHaveClass("rounded-full");
  });

  it("derives single-name initials without crashing", () => {
    render(<UserInfo user={{ ...user, name: "Sadia" }} />);

    expect(screen.getByText("S")).toBeInTheDocument();
    expect(screen.queryByText("SA")).not.toBeInTheDocument();
  });
});
