import "@testing-library/jest-dom/vitest";
import { act, cleanup, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import React from "react";
import ReactDOMServer from "react-dom/server";
import { useBroadcast, usePresence } from "../index";

/* ------------------------------------------------------------------ *
 * A dumb WebSocket double — the hook under test owns every decision
 * (when to connect, what to send, whether a close is intentional);
 * the double only records and lets the test drive server behavior.
 * ------------------------------------------------------------------ */

class MockWebSocket {
  static instances: MockWebSocket[] = [];
  // Fidelity with the browser constant the hook branches on — without
  // these, `WebSocket.OPEN` reads undefined and every comparison is false.
  static readonly CONNECTING = 0;
  static readonly OPEN = 1;
  static readonly CLOSING = 2;
  static readonly CLOSED = 3;

  static get last(): MockWebSocket {
    return MockWebSocket.instances[MockWebSocket.instances.length - 1];
  }

  url: string;
  sent: string[] = [];
  closed = false;
  readyState = 0; // CONNECTING
  onopen: (() => void) | null = null;
  onmessage: ((event: { data: string }) => void) | null = null;
  onclose: (() => void) | null = null;
  onerror: (() => void) | null = null;

  constructor(url: string) {
    this.url = url;
    MockWebSocket.instances.push(this);
  }

  send(data: string): void {
    this.sent.push(data);
  }

  close(): void {
    this.readyState = 3;
    this.closed = true;
    this.onclose?.();
  }

  // Test-side server actions.
  serverOpen(): void {
    this.readyState = 1;
    this.onopen?.();
  }

  serverMessage(frame: unknown): void {
    this.onmessage?.({ data: JSON.stringify(frame) });
  }

  serverClose(): void {
    this.readyState = 3;
    this.onclose?.();
  }
}

function sentFrames(socket: MockWebSocket): Record<string, unknown>[] {
  return socket.sent.map((raw) => JSON.parse(raw) as Record<string, unknown>);
}

const StatusProbe = ({
  channels,
  onMessage,
  options,
}: {
  channels: string | string[];
  onMessage: (message: { channel: string; payload: unknown }) => void;
  options?: Parameters<typeof useBroadcast>[2];
}) => {
  const status = useBroadcast(channels, onMessage, options);
  return <div data-testid="status">{status}</div>;
};

const PresenceProbe = ({ name }: { name: string }) => {
  const members = usePresence(name);
  return (
    <ul data-testid="members">
      {members.map((member) => (
        <li key={String(member.user_id)}>
          {String(member.user_id)}:{member.connections}
        </li>
      ))}
    </ul>
  );
};

describe("useBroadcast", () => {
  beforeEach(() => {
    MockWebSocket.instances = [];
    vi.stubGlobal("WebSocket", MockWebSocket);
  });

  afterEach(() => {
    cleanup();
    vi.unstubAllGlobals();
    vi.useRealTimers();
  });

  it("connects with the page's scheme and subscribes on open", () => {
    render(<StatusProbe channels="orders.42" onMessage={vi.fn()} />);

    const socket = MockWebSocket.last;
    // jsdom serves the page over http://localhost:3000 — the socket must
    // follow the page's scheme, never hardcode ws://.
    expect(socket.url).toBe("ws://localhost:3000/ws/broadcast");
    expect(socket.sent).toEqual([]); // nothing before the open handshake

    act(() => socket.serverOpen());
    expect(sentFrames(socket)).toEqual([{ type: "subscribe", channel: "orders.42" }]);
  });

  it("subscribes every channel when given a list", () => {
    render(<StatusProbe channels={["orders.1", "orders.2"]} onMessage={vi.fn()} />);

    const socket = MockWebSocket.last;
    act(() => socket.serverOpen());
    expect(sentFrames(socket)).toEqual([
      { type: "subscribe", channel: "orders.1" },
      { type: "subscribe", channel: "orders.2" },
    ]);
  });

  it("delivers message frames to the callback and ignores acks and errors", () => {
    const onMessage = vi.fn();
    render(<StatusProbe channels="orders.42" onMessage={onMessage} />);

    const socket = MockWebSocket.last;
    act(() => socket.serverOpen());
    act(() => {
      socket.serverMessage({
        type: "message",
        channel: "orders.42",
        payload: { status: "shipped" },
      });
      socket.serverMessage({ type: "subscribed", channel: "orders.42" });
      socket.serverMessage({ type: "error", channel: "private.orders.42", error: "unauthorized" });
    });

    expect(onMessage).toHaveBeenCalledTimes(1);
    expect(onMessage).toHaveBeenCalledWith({
      channel: "orders.42",
      payload: { status: "shipped" },
    });
  });

  it("unsubscribes and closes the socket on unmount", () => {
    render(<StatusProbe channels={["orders.1", "orders.2"]} onMessage={vi.fn()} />);
    const socket = MockWebSocket.last;
    act(() => socket.serverOpen());

    cleanup();

    expect(sentFrames(socket)).toEqual([
      { type: "subscribe", channel: "orders.1" },
      { type: "subscribe", channel: "orders.2" },
      { type: "unsubscribe", channel: "orders.1" },
      { type: "unsubscribe", channel: "orders.2" },
    ]);
    expect(socket.closed).toBe(true);
    expect(MockWebSocket.instances).toHaveLength(1); // no zombie reconnect
  });

  it("walks connecting, open, closed as the socket lives", () => {
    render(<StatusProbe channels="orders.42" onMessage={vi.fn()} />);
    expect(screen.getByTestId("status")).toHaveTextContent("connecting");

    const socket = MockWebSocket.last;
    act(() => socket.serverOpen());
    expect(screen.getByTestId("status")).toHaveTextContent("open");

    act(() => socket.serverClose());
    expect(screen.getByTestId("status")).toHaveTextContent("closed");
  });

  describe("reconnection", () => {
    it("waits out the backoff, then resubscribes and reports it", () => {
      vi.useFakeTimers();
      const onResubscribe = vi.fn();
      render(<StatusProbe channels="orders.42" onMessage={vi.fn()} options={{ onResubscribe }} />);
      const first = MockWebSocket.last;
      act(() => first.serverOpen());
      expect(onResubscribe).not.toHaveBeenCalled(); // first connect is not a resubscribe

      act(() => first.serverClose());
      expect(MockWebSocket.instances).toHaveLength(1); // no hot reconnect

      act(() => vi.advanceTimersByTime(999));
      expect(MockWebSocket.instances).toHaveLength(1); // still backing off

      act(() => vi.advanceTimersByTime(1));
      expect(MockWebSocket.instances).toHaveLength(2);
      expect(screen.getByTestId("status")).toHaveTextContent("connecting");

      const second = MockWebSocket.last;
      act(() => second.serverOpen());
      expect(sentFrames(second)).toEqual([{ type: "subscribe", channel: "orders.42" }]);
      expect(onResubscribe).toHaveBeenCalledWith(["orders.42"]);
    });

    it("doubles the wait per failed attempt and resets it after a reopen", () => {
      vi.useFakeTimers();
      render(<StatusProbe channels="orders.42" onMessage={vi.fn()} />);

      // Attempt 1 waits the base delay.
      act(() => MockWebSocket.last.serverClose());
      act(() => vi.advanceTimersByTime(1000));
      expect(MockWebSocket.instances).toHaveLength(2);

      // Attempt 2 never opens, so the wait doubles.
      act(() => MockWebSocket.last.serverClose());
      act(() => vi.advanceTimersByTime(1999));
      expect(MockWebSocket.instances).toHaveLength(2);
      act(() => vi.advanceTimersByTime(1));
      expect(MockWebSocket.instances).toHaveLength(3);

      // A successful reopen resets the sequence to the base delay.
      act(() => MockWebSocket.last.serverOpen());
      act(() => MockWebSocket.last.serverClose());
      act(() => vi.advanceTimersByTime(1000));
      expect(MockWebSocket.instances).toHaveLength(4);
    });
  });

  it("stays dormant during server-side rendering", () => {
    // SSR: no window at all. React DOM cannot render without one, so this
    // goes through renderToString — the real server path. Effects never run
    // server-side; the guard under test is the status initializer plus the
    // absence of any import-time window access.
    vi.stubGlobal("window", undefined);
    try {
      const html = ReactDOMServer.renderToString(
        <StatusProbe channels="orders.42" onMessage={vi.fn()} />,
      );
      expect(html).toContain("closed");
      expect(MockWebSocket.instances).toHaveLength(0);
    } finally {
      vi.unstubAllGlobals();
    }
  });

  it("stays dormant when the environment has no WebSocket", () => {
    vi.stubGlobal("WebSocket", undefined);
    render(<StatusProbe channels="orders.42" onMessage={vi.fn()} />);
    expect(screen.getByTestId("status")).toHaveTextContent("closed");
  });
});

describe("usePresence", () => {
  beforeEach(() => {
    MockWebSocket.instances = [];
    vi.stubGlobal("WebSocket", MockWebSocket);
  });

  afterEach(() => {
    cleanup();
    vi.unstubAllGlobals();
  });

  it("subscribes to presence.<name> and seeds members from the ack", () => {
    render(<PresenceProbe name="orders.9" />);
    const socket = MockWebSocket.last;
    act(() => socket.serverOpen());
    expect(sentFrames(socket)).toEqual([{ type: "subscribe", channel: "presence.orders.9" }]);

    act(() => {
      socket.serverMessage({
        type: "subscribed",
        channel: "presence.orders.9",
        members: [
          { user_id: 7, metadata: null, connections: 1 },
          { user_id: 8, metadata: null, connections: 2 },
        ],
      });
    });
    expect(screen.getByText("7:1")).toBeInTheDocument();
    expect(screen.getByText("8:2")).toBeInTheDocument();
  });

  it("replaces the roster on every presence frame", () => {
    render(<PresenceProbe name="orders.9" />);
    const socket = MockWebSocket.last;
    act(() => socket.serverOpen());
    act(() => {
      socket.serverMessage({
        type: "subscribed",
        channel: "presence.orders.9",
        members: [{ user_id: 7, metadata: null, connections: 1 }],
      });
    });
    expect(screen.getByText("7:1")).toBeInTheDocument();

    act(() => {
      socket.serverMessage({
        type: "presence",
        channel: "presence.orders.9",
        members: [
          { user_id: 7, metadata: null, connections: 1 },
          { user_id: 9, metadata: null, connections: 1 },
        ],
      });
    });
    expect(screen.getByText("9:1")).toBeInTheDocument();

    // A leave is just the next roster — 7 drops out.
    act(() => {
      socket.serverMessage({
        type: "presence",
        channel: "presence.orders.9",
        members: [{ user_id: 9, metadata: null, connections: 1 }],
      });
    });
    expect(screen.queryByText("7:1")).not.toBeInTheDocument();
    expect(screen.getByText("9:1")).toBeInTheDocument();
  });

  it("drops the roster the moment the channel changes", () => {
    const { rerender } = render(<PresenceProbe name="orders.9" />);
    const first = MockWebSocket.last;
    act(() => first.serverOpen());
    act(() => {
      first.serverMessage({
        type: "subscribed",
        channel: "presence.orders.9",
        members: [{ user_id: 7, metadata: null, connections: 1 }],
      });
    });
    expect(screen.getByText("7:1")).toBeInTheDocument();

    rerender(<PresenceProbe name="orders.10" />);

    // Room B has not even acked yet — the roster must already be empty,
    // never room A's members (a denied subscribe would show them forever).
    expect(screen.queryByText("7:1")).not.toBeInTheDocument();
    expect(screen.getByTestId("members")).toBeEmptyDOMElement();

    // The fresh socket for room B seeds its own roster when it acks.
    const second = MockWebSocket.last;
    expect(second).not.toBe(first);
    act(() => second.serverOpen());
    act(() => {
      second.serverMessage({
        type: "subscribed",
        channel: "presence.orders.10",
        members: [{ user_id: 12, metadata: null, connections: 3 }],
      });
    });
    expect(screen.getByText("12:3")).toBeInTheDocument();
    expect(screen.queryByText("7:1")).not.toBeInTheDocument();
  });
});
