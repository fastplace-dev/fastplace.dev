import "@testing-library/jest-dom/vitest";
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import React from "react";

import { Avatar, AvatarFallback, AvatarImage } from "../avatar";

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

describe("Avatar", () => {
  it("shows the fallback while the image has not loaded", () => {
    render(
      <Avatar>
        <AvatarImage src="https://example.com/photo.png" alt="Photo" />
        <AvatarFallback>AB</AvatarFallback>
      </Avatar>,
    );
    expect(screen.getByText("AB")).toBeVisible();
  });

  it("renders the root with the avatar slot and a circular shape", () => {
    render(
      <Avatar>
        <AvatarFallback>AB</AvatarFallback>
      </Avatar>,
    );
    const root = screen.getByText("AB").closest("[data-slot='avatar']");
    expect(root).not.toBeNull();
    expect(root).toHaveClass("rounded-full");
  });

  it("passes src and alt through to the image once it has loaded", () => {
    vi.spyOn(HTMLImageElement.prototype, "complete", "get").mockReturnValue(true);
    vi.spyOn(HTMLImageElement.prototype, "naturalWidth", "get").mockReturnValue(96);

    render(
      <Avatar>
        <AvatarImage src="https://example.com/photo.png" alt="Amara Baker" />
        <AvatarFallback>AB</AvatarFallback>
      </Avatar>,
    );
    const image = screen.getByAltText("Amara Baker");
    expect(image).toHaveAttribute("data-slot", "avatar-image");
    expect(image).toHaveAttribute("src", "https://example.com/photo.png");
    expect(screen.queryByText("AB")).not.toBeInTheDocument();
  });

  it("merges a caller className onto the root", () => {
    render(
      <Avatar className="size-10">
        <AvatarFallback>AB</AvatarFallback>
      </Avatar>,
    );
    const root = screen.getByText("AB").closest("[data-slot='avatar']");
    expect(root).toHaveClass("size-10");
    expect(root).not.toHaveClass("size-8");
  });
});
