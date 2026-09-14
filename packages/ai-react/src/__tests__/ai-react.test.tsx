/**
 * @fastplace/ai-react — SSE parsing and the streaming hooks. */

import "@testing-library/jest-dom/vitest";
import { act, cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import React from "react";
import { createSSEParser, useAgent, useAIStream } from "../index";

afterEach(cleanup);

/* ------------------------------------------------------------------ *
 * Helpers
 * ------------------------------------------------------------------ */

function sseResponse(events: string[], delayChunks = false): Response {
  const encoder = new TextEncoder();
  const stream = new ReadableStream<Uint8Array>({
    async start(controller) {
      for (const event of events) {
        controller.enqueue(encoder.encode(event));
        if (delayChunks) await new Promise((r) => setTimeout(r, 5));
      }
      controller.close();
    },
  });
  return new Response(stream, {
    status: 200,
    headers: { "content-type": "text/event-stream" },
  });
}

/** A response whose stream never terminates — stays open until cancelled. */
function openResponse(event: string): Response {
  const encoder = new TextEncoder();
  const stream = new ReadableStream<Uint8Array>({
    start(controller) {
      controller.enqueue(encoder.encode(event));
      // deliberately never closes
    },
  });
  return new Response(stream, {
    status: 200,
    headers: { "content-type": "text/event-stream" },
  });
}

const DELTA = (t: string) => `event: delta\ndata: ${JSON.stringify({ token: t })}\n\n`;
const TOOL = (name: string, result: unknown) =>
  `event: tool\ndata: ${JSON.stringify({ name, arguments: {}, result })}\n\n`;
const DONE = (content: string) => `event: done\ndata: ${JSON.stringify({ content })}\n\n`;
const ERROR = (message: string) => `event: error\ndata: ${JSON.stringify({ message })}\n\n`;

/* ------------------------------------------------------------------ *
 * createSSEParser
 * ------------------------------------------------------------------ */

describe("createSSEParser", () => {
  it("parses complete frames", () => {
    const parser = createSSEParser();
    const events = parser.feed(DELTA("hi") + DONE("hi"));
    expect(events).toEqual([
      { event: "delta", data: { token: "hi" } },
      { event: "done", data: { content: "hi" } },
    ]);
  });

  it("buffers partial frames across chunk boundaries", () => {
    const parser = createSSEParser();
    expect(parser.feed('event: delta\ndata: {"to')).toEqual([]);
    expect(parser.feed('ken":"hi"}\n\n')).toEqual([{ event: "delta", data: { token: "hi" } }]);
  });

  it("survives arbitrary TCP-style splits", () => {
    const parser = createSSEParser();
    const wire = DELTA("a") + DELTA("b") + DONE("ab");
    const collected = [];
    // one character at a time — the nastiest possible chunking
    for (const ch of wire) collected.push(...parser.feed(ch));
    expect(collected).toEqual([
      { event: "delta", data: { token: "a" } },
      { event: "delta", data: { token: "b" } },
      { event: "done", data: { content: "ab" } },
    ]);
  });

  it("tolerates crlf line endings and non-json data", () => {
    const parser = createSSEParser();
    const events = parser.feed('event: delta\r\ndata: {"token":"x"}\r\n\r\n');
    expect(events).toEqual([{ event: "delta", data: { token: "x" } }]);

    const fallback = createSSEParser();
    expect(fallback.feed("event: ping\r\ndata: not-json\r\n\r\n")).toEqual([
      { event: "ping", data: { raw: "not-json" } },
    ]);
  });
});

/* ------------------------------------------------------------------ *
 * useAIStream
 * ------------------------------------------------------------------ */

describe("useAIStream", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
    document.head.querySelector('meta[name="csrf-token"]')?.remove();
  });

  it("streams deltas into an assistant message", async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValue(sseResponse([DELTA("Hel"), DELTA("lo!"), DONE("Hello!")]));
    vi.stubGlobal("fetch", fetchMock);

    function Chat() {
      const { messages, input, handleInputChange, handleSubmit, isStreaming } = useAIStream({
        endpoint: "/ai/assistant",
      });
      return (
        <div>
          <ul>
            {messages.map((m) => (
              <li key={m.id} data-testid={`msg-${m.role}`}>
                {m.content}
              </li>
            ))}
          </ul>
          <form onSubmit={handleSubmit}>
            <input aria-label="message" value={input} onChange={handleInputChange} />
            <button type="submit">Send</button>
          </form>
          <span data-testid="streaming">{String(isStreaming)}</span>
        </div>
      );
    }

    render(<Chat />);
    await userEvent.type(await screen.findByLabelText("message"), "hi{Enter}");

    await waitFor(() => expect(screen.getByTestId("msg-assistant")).toHaveTextContent("Hello!"));
    const user = screen.getByTestId("msg-user");
    expect(user).toHaveTextContent("hi");
    expect(fetchMock).toHaveBeenCalledWith(
      "/ai/assistant",
      expect.objectContaining({
        method: "POST",
        body: JSON.stringify({ message: "hi" }),
      }),
    );
  });

  it("sends the CSRF token from the page meta tag", async () => {
    const meta = document.createElement("meta");
    meta.name = "csrf-token";
    meta.content = "tok-123";
    document.head.appendChild(meta);

    const fetchMock = vi.fn().mockResolvedValue(sseResponse([DONE("ok")]));
    vi.stubGlobal("fetch", fetchMock);

    function Chat() {
      const { input, handleInputChange, handleSubmit } = useAIStream({
        endpoint: "/ai/assistant",
      });
      return (
        <form onSubmit={handleSubmit}>
          <input aria-label="message2" value={input} onChange={handleInputChange} />
        </form>
      );
    }

    render(<Chat />);
    await userEvent.type(await screen.findByLabelText("message2"), "x{Enter}");

    await waitFor(() => expect(fetchMock).toHaveBeenCalled());
    const [, init] = fetchMock.mock.calls[0];
    expect(init.headers["X-Fastplace-CSRF-Token"]).toBe("tok-123");
  });

  it("appends tool events as tool messages", async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValue(sseResponse([TOOL("search_docs", ["hit"]), DONE("found")]));
    vi.stubGlobal("fetch", fetchMock);

    function Chat() {
      const { messages, input, handleInputChange, handleSubmit } = useAIStream({
        endpoint: "/ai/x",
      });
      return (
        <div>
          <ul>
            {messages.map((m) => (
              <li key={m.id} data-testid={`msg-${m.role}`}>
                {m.content}
              </li>
            ))}
          </ul>
          <form onSubmit={handleSubmit}>
            <input aria-label="message3" value={input} onChange={handleInputChange} />
          </form>
        </div>
      );
    }

    render(<Chat />);
    await userEvent.type(await screen.findByLabelText("message3"), "q{Enter}");

    await waitFor(() => expect(screen.getByTestId("msg-tool")).toBeTruthy());
    expect(screen.getByTestId("msg-tool").textContent).toContain("search_docs");
  });

  it("surfaces error events as the assistant reply and reports failures", async () => {
    const onError = vi.fn();
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(sseResponse([ERROR("provider down")]))
      .mockResolvedValueOnce(new Response("nope", { status: 503 }));
    vi.stubGlobal("fetch", fetchMock);

    function Chat() {
      const { messages, input, handleInputChange, handleSubmit } = useAIStream({
        endpoint: "/ai/x",
        onError,
      });
      return (
        <div>
          <ul>
            {messages.map((m) => (
              <li key={m.id} data-testid={`msg-${m.role}`}>
                {m.content}
              </li>
            ))}
          </ul>
          <form onSubmit={handleSubmit}>
            <input aria-label="message4" value={input} onChange={handleInputChange} />
          </form>
        </div>
      );
    }

    render(<Chat />);
    await userEvent.type(await screen.findByLabelText("message4"), "a{Enter}");
    await waitFor(() =>
      expect(screen.getByTestId("msg-assistant")).toHaveTextContent("provider down"),
    );

    await userEvent.type(await screen.findByLabelText("message4"), "b{Enter}");
    await waitFor(() => expect(onError).toHaveBeenCalled());
    expect(onError.mock.calls[0][0]).toBeInstanceOf(Error);
  });

  it("fills the assistant placeholder with a visible error when the transport fails", async () => {
    const onError = vi.fn();
    const fetchMock = vi.fn().mockResolvedValue(new Response("nope", { status: 503 }));
    vi.stubGlobal("fetch", fetchMock);

    function Chat() {
      const { messages, input, handleInputChange, handleSubmit } = useAIStream({
        endpoint: "/ai/x",
        onError,
      });
      return (
        <div>
          <ul>
            {messages.map((m) => (
              <li key={m.id} data-testid={`msg-${m.role}`}>
                {m.content}
              </li>
            ))}
          </ul>
          <form onSubmit={handleSubmit}>
            <input aria-label="message5" value={input} onChange={handleInputChange} />
          </form>
        </div>
      );
    }

    render(<Chat />);
    await userEvent.type(await screen.findByLabelText("message5"), "q{Enter}");

    await waitFor(() => expect(onError).toHaveBeenCalled());
    // the placeholder must not stay an empty orphan bubble
    const bubbles = screen.getAllByTestId("msg-assistant");
    expect(bubbles[bubbles.length - 1].textContent).not.toBe("");
  });
});

/* ------------------------------------------------------------------ *
 * useAgent
 * ------------------------------------------------------------------ */

describe("useAgent", () => {
  beforeEach(() => vi.restoreAllMocks());

  it("sends programmatically and resets", async () => {
    const fetchMock = vi.fn().mockResolvedValue(sseResponse([DELTA("ok"), DONE("ok")]));
    vi.stubGlobal("fetch", fetchMock);

    let api: ReturnType<typeof useAgent>;
    function Host() {
      api = useAgent({ endpoint: "/ai/bot" });
      return (
        <div>
          <ul>
            {api.messages.map((m) => (
              <li key={m.id} data-testid={`msg-${m.role}`}>
                {m.content}
              </li>
            ))}
          </ul>
          <button onClick={() => api.send("run")}>Run</button>
          <button onClick={() => api.reset()}>Reset</button>
        </div>
      );
    }

    render(<Host />);
    await userEvent.click(screen.getByText("Run"));

    await waitFor(() => expect(screen.getByTestId("msg-assistant")).toHaveTextContent("ok"));
    expect(screen.getByTestId("msg-user")).toHaveTextContent("run");

    await act(async () => {});
    await userEvent.click(screen.getByText("Reset"));
    await waitFor(() => expect(screen.queryByTestId("msg-user")).not.toBeInTheDocument());
  });

  it("drops a second send fired in the same tick while a stream is active", async () => {
    const fetchMock = vi.fn().mockImplementation(() => openResponse(DELTA("x")));
    vi.stubGlobal("fetch", fetchMock);

    let api: ReturnType<typeof useAgent>;
    function Host() {
      api = useAgent({ endpoint: "/ai/bot" });
      // both sends land in the same synchronous tick — a stale isStreaming
      // closure would let both through; the ref guard must drop the second
      return (
        <button
          onClick={() => {
            api.send("one");
            api.send("two");
          }}
        >
          Double
        </button>
      );
    }

    render(<Host />);
    await userEvent.click(screen.getByText("Double"));

    expect(fetchMock).toHaveBeenCalledTimes(1);
  });

  it("aborts the in-flight request when the component unmounts", async () => {
    const fetchMock = vi.fn().mockImplementation(() => openResponse(DELTA("x")));
    vi.stubGlobal("fetch", fetchMock);

    let api: ReturnType<typeof useAgent>;
    function Host() {
      api = useAgent({ endpoint: "/ai/bot" });
      return <button onClick={() => api.send("go")}>Run</button>;
    }

    const { unmount } = render(<Host />);
    await userEvent.click(screen.getByText("Run"));
    expect(fetchMock).toHaveBeenCalledTimes(1);

    const [, init] = fetchMock.mock.calls[0];
    expect(init.signal).toBeInstanceOf(AbortSignal);

    unmount();
    expect(init.signal.aborted).toBe(true);
  });

  it("reset aborts the active stream and accepts a fresh send", async () => {
    const fetchMock = vi.fn().mockImplementation(() => openResponse(DELTA("x")));
    vi.stubGlobal("fetch", fetchMock);

    let api: ReturnType<typeof useAgent>;
    function Host() {
      api = useAgent({ endpoint: "/ai/bot" });
      return (
        <div>
          <button onClick={() => api.send("first")}>Run</button>
          <button onClick={() => api.reset()}>Reset</button>
          <span data-testid="streaming">{String(api.isStreaming)}</span>
        </div>
      );
    }

    render(<Host />);
    await userEvent.click(screen.getByText("Run"));
    await waitFor(() => expect(screen.getByTestId("streaming")).toHaveTextContent("true"));

    await userEvent.click(screen.getByText("Reset"));
    await waitFor(() => expect(screen.getByTestId("streaming")).toHaveTextContent("false"));

    await userEvent.click(screen.getByText("Run"));
    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(2));
  });
});
