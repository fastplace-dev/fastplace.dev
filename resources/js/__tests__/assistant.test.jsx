import "@testing-library/jest-dom/vitest";
import { cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import React from "react";
import { FastplaceProvider, router } from "@fastplace/react";

// The page only consumes the hook's shape — its streaming behavior is
// covered by the @fastplace/ai-react package tests, so pin the contract
// with a controllable stand-in.
vi.mock("@fastplace/ai-react", () => ({ useAIStream: vi.fn() }));

import { useAIStream } from "@fastplace/ai-react";
import AssistantChat from "../pages/Assistant/Chat.jsx";

afterEach(() => {
  cleanup();
  router.reset();
  vi.clearAllMocks();
});

function renderChat({ messages = [], isStreaming = false, endpoint = "/ai/assistant" } = {}) {
  const handleSubmit = vi.fn();
  const handleInputChange = vi.fn();
  useAIStream.mockReturnValue({
    messages,
    input: "",
    handleInputChange,
    handleSubmit,
    isStreaming,
  });
  render(
    <FastplaceProvider
      initialPage={{
        component: "Assistant/Chat",
        // The composer renders for authenticated visitors only; the guest
        // login prompt has its own suite (pages/__tests__/AssistantChat).
        props: { endpoint, auth: { user: { id: 1, name: "Jane", email: "jane@example.com" } } },
        url: "/assistant",
      }}
    >
      <AssistantChat />
    </FastplaceProvider>,
  );
  return { handleSubmit, handleInputChange };
}

describe("Assistant/Chat", () => {
  it("renders the page header naming the streaming endpoint", () => {
    renderChat({ endpoint: "/ai/custom-agent" });
    expect(screen.getByRole("heading", { name: "Assistant" })).toBeInTheDocument();
    expect(screen.getByText("/ai/custom-agent")).toBeInTheDocument();
  });

  it("shows the empty-state prompt before any messages", () => {
    renderChat();
    expect(screen.getByText("Ask the Fastplace AI agent…")).toBeInTheDocument();
  });

  it("renders the conversation from the hook", () => {
    renderChat({
      messages: [
        { role: "user", content: "What is Fastplace?" },
        { role: "assistant", content: "A full-stack AI-native framework." },
      ],
    });
    expect(screen.getByText("What is Fastplace?")).toBeInTheDocument();
    expect(screen.getByText("A full-stack AI-native framework.")).toBeInTheDocument();
  });

  it("wires the composer input to the hook", async () => {
    const user = userEvent.setup();
    const { handleSubmit, handleInputChange } = renderChat();

    const input = screen.getByLabelText("Message");
    await user.type(input, "hello");
    expect(handleInputChange).toHaveBeenCalled();

    await user.click(screen.getByRole("button", { name: "Send" }));
    expect(handleSubmit).toHaveBeenCalled();
  });

  it("disables Send while a response streams", () => {
    renderChat({ messages: [{ role: "user", content: "hi" }], isStreaming: true });
    expect(screen.getByTestId("streaming")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Send" })).toBeDisabled();
  });
});
