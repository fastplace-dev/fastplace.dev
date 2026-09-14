import React from "react";
import { usePage } from "@fastplace/react";
import { useAIStream } from "@fastplace/ai-react";
import AppLayout from "../../layouts/AppLayout";

export default function AssistantChat() {
  const { props } = usePage();
  const endpoint = props.endpoint ?? "/ai/assistant";
  const { messages, input, handleInputChange, handleSubmit, isStreaming } = useAIStream({
    endpoint,
  });

  return (
    <main className="mx-auto flex min-h-[calc(100dvh-3.5rem)] max-w-2xl flex-col px-6 py-8">
      <header className="mb-4">
        <h1 className="text-2xl font-semibold">Assistant</h1>
        <p className="text-ink-muted text-sm">
          Streaming from <code className="text-accent">{endpoint}</code> — tool calls hit the
          knowledge base through search_docs.
        </p>
      </header>

      <div
        aria-live="polite"
        className="bg-surface-raised border border-line flex-1 space-y-3 overflow-y-auto rounded-xl p-4"
      >
        {messages.length === 0 ? (
          <p className="text-ink-muted">Ask the Fastplace AI agent…</p>
        ) : (
          messages.map((msg, idx) => (
            <div
              key={idx}
              className={
                msg.role === "user"
                  ? "bg-brand-600 ml-auto max-w-xs rounded-lg p-3 text-white"
                  : "bg-surface border border-line max-w-md rounded-lg p-3"
              }
            >
              {msg.content}
            </div>
          ))
        )}
        {isStreaming && (
          <p className="text-ink-muted animate-pulse text-sm" data-testid="streaming">
            thinking…
          </p>
        )}
      </div>

      <form onSubmit={handleSubmit} className="mt-4 flex gap-2">
        <input
          value={input}
          onChange={handleInputChange}
          placeholder="Ask the Fastplace AI agent..."
          aria-label="Message"
          className="bg-surface-raised border border-line flex-1 rounded-lg px-3 py-2 focus:outline-none focus:ring-2 focus:ring-accent"
        />
        <button
          type="submit"
          disabled={isStreaming}
          className="bg-brand-600 hover:bg-brand-700 rounded-lg px-4 py-2 font-medium text-white disabled:opacity-50"
        >
          Send
        </button>
      </form>
    </main>
  );
}

// Persistent-layout opt-in — the bridge reads this static on the component.
AssistantChat.layout = AppLayout;
