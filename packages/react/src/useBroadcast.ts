import { useEffect, useRef, useState } from "react";

/**
 * Client side of the framework's `/ws/broadcast` endpoint: subscribe to
 * channels, receive `message` frames through a callback, and survive
 * dropped sockets with exponential-backoff reconnect + resubscribe.
 * On the server (SSR) the hook is dormant — no window, no socket.
 */

/** Mirror of the endpoint's `message` frame: the channel plus its payload. */
export interface BroadcastMessage {
  channel: string;
  payload: unknown;
}

export type BroadcastStatus = "connecting" | "open" | "closed";

export interface UseBroadcastOptions {
  /** Endpoint path (default: "/ws/broadcast"). */
  path?: string;
  /**
   * Called once per reconnect with the channels that were just
   * resubscribed — the natural hook for refetching state the client
   * missed while disconnected.
   */
  onResubscribe?: (channels: string[]) => void;
  /** First reconnect wait; doubles per failed attempt (default: 1000ms). */
  retryBaseMs?: number;
  /** Backoff ceiling (default: 30000ms). */
  retryMaxMs?: number;
}

interface FrameHandlers {
  onMessage?: (message: BroadcastMessage) => void;
  /** `subscribed` acks — presence channels carry the current roster here. */
  onSubscribed?: (frame: { channel: string; members?: unknown }) => void;
  /** `presence` roster updates. */
  onPresence?: (frame: { channel: string; members?: unknown }) => void;
}

/** A member of a presence roster, as the endpoint reports it. */
export interface PresenceMember {
  user_id: number | string;
  metadata: Record<string, unknown> | null;
  connections: number;
}

/** Both SSR guards in one: no window (server) or no WebSocket support. */
function canConnect(): boolean {
  return typeof window !== "undefined" && typeof window.WebSocket !== "undefined";
}

function socketUrl(path: string): string {
  const scheme = window.location.protocol === "https:" ? "wss" : "ws";
  return `${scheme}://${window.location.host}${path}`;
}

const DEFAULT_PATH = "/ws/broadcast";
const DEFAULT_RETRY_BASE_MS = 1000;
const DEFAULT_RETRY_MAX_MS = 30000;

/**
 * One socket per mounting hook. Handlers live in a ref so a caller's
 * inline arrow neither reconnects the socket nor misses frames; only the
 * channel set and the tuning options are connection-identity deps —
 * changing the channels closes and reopens with the new set.
 */
function useBroadcastConnection(
  channels: string[],
  handlers: FrameHandlers,
  options: UseBroadcastOptions = {},
): BroadcastStatus {
  const [status, setStatus] = useState<BroadcastStatus>(() =>
    canConnect() ? "connecting" : "closed",
  );

  const handlersRef = useRef(handlers);
  const resubscribeRef = useRef(options.onResubscribe);
  useEffect(() => {
    handlersRef.current = handlers;
    resubscribeRef.current = options.onResubscribe;
  });

  const path = options.path ?? DEFAULT_PATH;
  const retryBaseMs = options.retryBaseMs ?? DEFAULT_RETRY_BASE_MS;
  const retryMaxMs = options.retryMaxMs ?? DEFAULT_RETRY_MAX_MS;
  // The channel set is the effect's identity — collapse it to a stable
  // primitive so callers may pass a fresh array literal every render.
  const channelKey = channels.join("\u0000");

  useEffect(() => {
    if (!canConnect()) {
      setStatus("closed");
      return;
    }

    let socket: WebSocket | null = null;
    let reconnectTimer: ReturnType<typeof setTimeout> | null = null;
    let failedAttempts = 0;
    let reconnects = 0;
    let closingIntentionally = false;

    const connect = () => {
      setStatus("connecting");
      socket = new WebSocket(socketUrl(path));
      socket.onopen = () => {
        failedAttempts = 0;
        setStatus("open");
        for (const channel of channels) {
          socket?.send(JSON.stringify({ type: "subscribe", channel }));
        }
        if (reconnects > 0) resubscribeRef.current?.(channels);
      };
      socket.onmessage = (event: MessageEvent) => {
        let frame: { type?: string; channel?: string; payload?: unknown; members?: unknown };
        try {
          frame = JSON.parse(event.data as string);
        } catch {
          return; // a non-JSON frame is not ours to act on
        }
        if (frame?.type === "message") {
          handlersRef.current.onMessage?.({ channel: frame.channel ?? "", payload: frame.payload });
        } else if (frame?.type === "subscribed") {
          handlersRef.current.onSubscribed?.({
            channel: frame.channel ?? "",
            members: frame.members,
          });
        } else if (frame?.type === "presence") {
          handlersRef.current.onPresence?.({
            channel: frame.channel ?? "",
            members: frame.members,
          });
        }
        // Ack, error, and ping frames carry nothing a v1 consumer acts on —
        // a denied subscribe already arrives as an error frame on a live socket.
      };
      socket.onclose = () => {
        socket = null;
        if (closingIntentionally) return;
        setStatus("closed");
        const wait = Math.min(retryBaseMs * 2 ** failedAttempts, retryMaxMs);
        failedAttempts += 1;
        reconnects += 1;
        reconnectTimer = setTimeout(connect, wait);
      };
    };

    connect();

    return () => {
      closingIntentionally = true;
      if (reconnectTimer !== null) clearTimeout(reconnectTimer);
      if (socket) {
        if (socket.readyState === WebSocket.OPEN) {
          // Tell the server we are gone — it drops presence memberships and
          // roster slots immediately instead of waiting for the TCP close.
          for (const channel of channels) {
            socket.send(JSON.stringify({ type: "unsubscribe", channel }));
          }
        }
        socket.close();
      }
    };
    // Deps: the channel set travels as `channelKey` (a stable primitive),
    // and handlers/options are read through refs — neither reconnects.
  }, [channelKey, path, retryBaseMs, retryMaxMs]);

  return status;
}

/**
 * Subscribe to broadcast channels over the framework's WebSocket endpoint.
 * `onMessage` receives every `message` frame delivered for the subscribed
 * channels; a dropped socket reconnects with exponential backoff, resends
 * the subscriptions, then calls `options.onResubscribe(channels)`.
 *
 * Changing the channel set closes and reopens the socket with the new
 * subscriptions. Returns the connection status for indicator UI.
 */
export function useBroadcast(
  channels: string | string[],
  onMessage: (message: BroadcastMessage) => void,
  options?: UseBroadcastOptions,
): BroadcastStatus {
  const list = Array.isArray(channels) ? channels : [channels];
  return useBroadcastConnection(list, { onMessage }, options);
}

/**
 * Track the live roster of a `presence.<name>` channel. The roster seeds
 * from the subscribe ack and replaces itself on every join/leave frame —
 * the server derives it, the client just mirrors the latest snapshot.
 * Returns `[]` until the first ack arrives.
 */
export function usePresence(name: string, options?: UseBroadcastOptions): PresenceMember[] {
  const [members, setMembers] = useState<PresenceMember[]>([]);
  // A new room is a new roster — clear the moment the channel changes so a
  // slow or denied subscribe never serves the previous room's members.
  useEffect(() => {
    setMembers([]);
  }, [name]);
  const adopt = (frame: { channel: string; members?: unknown }) => {
    if (Array.isArray(frame.members)) setMembers(frame.members as PresenceMember[]);
  };
  // The connection hook reads handlers through a ref, so this inline
  // object never churns the socket.
  useBroadcastConnection([`presence.${name}`], { onSubscribed: adopt, onPresence: adopt }, options);
  return members;
}
