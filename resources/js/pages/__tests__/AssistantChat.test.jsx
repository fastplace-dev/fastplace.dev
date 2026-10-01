import "@testing-library/jest-dom/vitest";
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";
import React from "react";

import { FastplaceProvider, router } from "@fastplace/react";

import AssistantChat from "../Assistant/Chat";

const testUser = {
  id: 1,
  name: "Jane Doe",
  email: "jane@example.com",
  email_verified_at: null,
  created_at: "2026-01-01T00:00:00Z",
  updated_at: "2026-01-01T00:00:00Z",
};

function renderChat(auth) {
  return render(
    <FastplaceProvider
      initialPage={{ component: "Assistant/Chat", url: "/assistant", props: auth ? { auth } : {} }}
    >
      <AssistantChat />
    </FastplaceProvider>,
  );
}

afterEach(() => {
  cleanup();
  router.reset();
});

describe("Assistant chat", () => {
  it("shows the composer to an authenticated visitor", () => {
    renderChat({ user: testUser });

    expect(screen.getByRole("button", { name: "Send" })).toBeInTheDocument();
  });

  it("replaces the composer with a login prompt for guests", () => {
    renderChat();

    expect(screen.queryByRole("button", { name: "Send" })).not.toBeInTheDocument();
    expect(screen.getByRole("link", { name: /log in to chat/i })).toHaveAttribute(
      "href",
      "/login",
    );
  });
});
