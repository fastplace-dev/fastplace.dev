import "@testing-library/jest-dom/vitest";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import React from "react";

import TextLink from "../text-link";

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

describe("TextLink", () => {
  it("renders a bridge anchor with the given href and children", () => {
    render(<TextLink href="/settings/profile">Edit profile</TextLink>);

    const link = screen.getByRole("link", { name: "Edit profile" });
    expect(link).toHaveAttribute("href", "/settings/profile");
    expect(link).toHaveAttribute("data-fastplace-link");
  });

  it("applies the ink underline styling", () => {
    render(<TextLink href="/settings/profile">Edit profile</TextLink>);

    const link = screen.getByRole("link", { name: "Edit profile" });
    expect(link).toHaveClass("text-ink", "underline", "underline-offset-4", "decoration-line");
  });

  it("merges the caller's className while keeping the underline treatment", () => {
    render(
      <TextLink href="/settings/profile" className="text-ink-muted font-medium">
        Edit profile
      </TextLink>,
    );

    const link = screen.getByRole("link", { name: "Edit profile" });
    // cn/twMerge resolves the text color conflict in the caller's favor…
    expect(link).toHaveClass("text-ink-muted");
    expect(link).not.toHaveClass("text-ink");
    // …but non-conflicting utilities from both sides survive.
    expect(link).toHaveClass("font-medium", "underline", "decoration-line");
  });

  it("navigates through the bridge on click", async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      new Response(
        JSON.stringify({ component: "Settings/Profile", props: {}, url: "/settings/profile" }),
        {
          headers: { "content-type": "application/json" },
        },
      ),
    );
    vi.stubGlobal("fetch", fetchMock);
    const user = userEvent.setup();

    render(<TextLink href="/settings/profile">Edit profile</TextLink>);

    await user.click(screen.getByRole("link", { name: "Edit profile" }));

    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(1));
    const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(url).toBe("/settings/profile");
    expect(init.method).toBe("GET");
    expect((init.headers as Record<string, string>)["X-Fastplace-Request"]).toBe("true");
  });
});
