/**
 * @fastplace/ai-react — streaming AI hooks for the Fastplace agent engine.
 *
 * `useAIStream({ endpoint })` posts a message to an `/ai/*` SSE endpoint and
 * folds the streamed events into chat state (messages / input / isStreaming)
 * — the same shape the Vercel AI SDK popularized, but speaking Fastplace's
 * SSE protocol: `delta` token chunks, `tool` executions, one terminal
 * `done` (or `error`) event.
 */

import React, { useCallback, useEffect, useRef, useState } from "react";

/* ------------------------------------------------------------------ *
 * Types
 * ------------------------------------------------------------------ */

export type ChatRole = "user" | "assistant" | "tool" | "system";

export interface ChatMessage {
  id: string;
  role: ChatRole;
  content: string;
}

/** One parsed SSE frame off the wire. */
export interface SSEEvent {
  event: string;
  data: Record<string, unknown>;
}

export interface UseAIStreamOptions {
  /** The `/ai/*` endpoint that returns the SSE stream. */
  endpoint: string;
  /** Extra headers merged into the POST (auth, etc.). */
  headers?: Record<string, string>;
  /** Called when the request or stream fails. */
  onError?: (error: unknown) => void;
}

export interface UseAIStreamResult {
  messages: ChatMessage[];
  input: string;
  setInput: (value: string) => void;
  handleInputChange: (event: React.ChangeEvent<HTMLInputElement | HTMLTextAreaElement>) => void;
  handleSubmit: (event?: React.FormEvent) => void;
  isStreaming: boolean;
}

export interface UseAgentResult {
  messages: ChatMessage[];
  isStreaming: boolean;
  /** Send a message programmatically (no form involved). */
  send: (message: string) => void;
  /** Drop the whole conversation. */
  reset: () => void;
}

/* ------------------------------------------------------------------ *
 * SSE parsing
 * ------------------------------------------------------------------ */

let _messageSeq = 0;
const nextId = () => `m${++_messageSeq}`;

/**
 * Stateful SSE frame parser — network chunks do not respect frame
 * boundaries, so partial input is buffered between `feed()` calls.
 *
 * ```ts
 * const parser = createSSEParser();
 * parser.feed('event: delta\ndata: {"to');  // → []
 * parser.feed('ken":"hi"}\n\n');            // → [{event: "delta", …}]
 * ```
 */
export function createSSEParser() {
  let buffer = "";

  function feed(chunk: string): SSEEvent[] {
    buffer += chunk;
    const events: SSEEvent[] = [];
    // Frames are separated by a blank line; \r\n is tolerated for proxies
    // that rewrite line endings.
    const frames = buffer.split(/\r?\n\r?\n/);
    buffer = frames.pop() ?? "";
    for (const frame of frames) {
      const parsed = parseFrame(frame);
      if (parsed) events.push(parsed);
    }
    return events;
  }

  return { feed };
}

function parseFrame(frame: string): SSEEvent | null {
  let event = "message";
  let dataRaw: string | null = null;
  for (const line of frame.split(/\r?\n/)) {
    if (line.startsWith("event:")) {
      event = line.slice("event:".length).trim();
    } else if (line.startsWith("data:")) {
      const piece = line.slice("data:".length).trimStart();
      dataRaw = dataRaw === null ? piece : `${dataRaw}\n${piece}`;
    }
  }
  if (dataRaw === null) return null;
  let data: Record<string, unknown>;
  try {
    data = JSON.parse(dataRaw) as Record<string, unknown>;
  } catch {
    data = { raw: dataRaw };
  }
  return { event, data };
}

/** Read the CSRF token the bridge backend embeds on first render. */
function csrfToken(): string | null {
  if (typeof document === "undefined") return null;
  const meta = document.querySelector<HTMLMetaElement>('meta[name="csrf-token"]');
  return meta?.content ?? null;
}

/* ------------------------------------------------------------------ *
 * The stream engine — one fetch, folded into chat state
 * ------------------------------------------------------------------ */

function useAgentEngine({ endpoint, headers, onError }: UseAIStreamOptions) {
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [isStreaming, setIsStreaming] = useState(false);
  // Refs mirror the streaming state synchronously: a second send in the
  // same tick must see the guard, not a stale closure copy of the state.
  const streamingRef = useRef(false);
  const controllerRef = useRef<AbortController | null>(null);

  // Unmounting mid-stream aborts the in-flight request instead of leaving
  // it appending into a dead component.
  useEffect(() => {
    const controller = controllerRef;
    return () => controller.current?.abort();
  }, []);

  const send = useCallback(
    async (message: string) => {
      if (streamingRef.current) return;
      streamingRef.current = true;
      setIsStreaming(true);
      // The assistant's reply grows in place as deltas arrive.
      const assistantId = nextId();
      setMessages((current) => [
        ...current,
        { id: assistantId, role: "user", content: message },
        { id: assistantId + "a", role: "assistant", content: "" },
      ]);

      const controller = new AbortController();
      controllerRef.current = controller;
      let reader: ReadableStreamDefaultReader<Uint8Array> | null = null;
      try {
        const response = await fetch(endpoint, {
          method: "POST",
          headers: {
            "Content-Type": "application/json",
            Accept: "text/event-stream",
            ...(csrfToken() ? { "X-Fastplace-CSRF-Token": csrfToken()! } : {}),
            ...headers,
          },
          body: JSON.stringify({ message }),
          signal: controller.signal,
        });
        if (!response.ok || !response.body) {
          throw new Error(`agent endpoint responded ${response.status}`);
        }

        const parser = createSSEParser();
        reader = response.body.getReader();
        const decoder = new TextDecoder();
        let finished = false;

        while (!finished) {
          const { done, value } = await reader.read();
          if (done) break;
          for (const { event, data } of parser.feed(decoder.decode(value, { stream: true }))) {
            if (event === "delta") {
              const token = String(data.token ?? "");
              setMessages((current) =>
                current.map((m) =>
                  m.id === assistantId + "a" ? { ...m, content: m.content + token } : m,
                ),
              );
            } else if (event === "tool") {
              const content = JSON.stringify({
                name: data.name,
                arguments: data.arguments,
                result: data.result,
              });
              setMessages((current) => [...current, { id: nextId(), role: "tool", content }]);
            } else if (event === "done") {
              // The authoritative final text replaces the accumulated
              // deltas (server-side truth wins over reassembly).
              const content = String(data.content ?? "");
              setMessages((current) =>
                current.map((m) => (m.id === assistantId + "a" ? { ...m, content } : m)),
              );
              finished = true;
            } else if (event === "error") {
              const content = String(data.message ?? "stream error");
              setMessages((current) =>
                current.map((m) => (m.id === assistantId + "a" ? { ...m, content } : m)),
              );
              finished = true;
            }
          }
        }
      } catch (error) {
        // Abort (unmount or reset) is intentional — not an error to report.
        if ((error as Error)?.name === "AbortError") return;
        onError?.(error);
        // The placeholder must not stay an empty orphan bubble.
        const note = error instanceof Error ? error.message : "the agent stream failed";
        setMessages((current) =>
          current.map((m) => (m.id === assistantId + "a" ? { ...m, content: note } : m)),
        );
      } finally {
        // Cancel the body so the server sees the disconnect promptly.
        reader?.cancel().catch(() => {});
        if (controllerRef.current === controller) controllerRef.current = null;
        streamingRef.current = false;
        setIsStreaming(false);
      }
    },
    [endpoint, headers, onError],
  );

  const reset = useCallback(() => {
    controllerRef.current?.abort();
    controllerRef.current = null;
    streamingRef.current = false;
    setIsStreaming(false);
    setMessages([]);
  }, []);

  return { messages, isStreaming, send, reset };
}

/* ------------------------------------------------------------------ *
 * useAIStream — the form-facing hook
 * ------------------------------------------------------------------ */

export function useAIStream(options: UseAIStreamOptions): UseAIStreamResult {
  const { messages, isStreaming, send } = useAgentEngine(options);
  const [input, setInput] = useState("");

  const handleInputChange = useCallback(
    (event: React.ChangeEvent<HTMLInputElement | HTMLTextAreaElement>) => {
      setInput(event.target.value);
    },
    [],
  );

  const handleSubmit = useCallback(
    (event?: React.FormEvent) => {
      event?.preventDefault();
      const message = input.trim();
      if (!message || isStreaming) return;
      setInput("");
      void send(message);
    },
    [input, isStreaming, send],
  );

  return {
    messages,
    input,
    setInput,
    handleInputChange,
    handleSubmit,
    isStreaming,
  };
}

/* ------------------------------------------------------------------ *
 * useAgent — the programmatic facade
 * ------------------------------------------------------------------ */

/**
 * Same engine without the form coupling — for agents wired to buttons,
 * background tasks, or non-chat UIs.
 */
export function useAgent(options: UseAIStreamOptions): UseAgentResult {
  const { messages, isStreaming, send, reset } = useAgentEngine(options);

  const sendSync = useCallback(
    (message: string) => {
      const trimmed = message.trim();
      if (!trimmed || isStreaming) return;
      void send(trimmed);
    },
    [isStreaming, send],
  );

  return { messages, isStreaming, send: sendSync, reset };
}
