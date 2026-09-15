import React from "react";
import { usePage } from "@fastplace/react";
import { useAIStream } from "@fastplace/ai-react";
import { Card } from "../../components/ui/card";
import { Button } from "../../components/ui/button";
import { Input } from "../../components/ui/input";
import AppLayout from "@/layouts/app-layout";

export default function AssistantChat() {
  const { props } = usePage();
  const endpoint = props.endpoint ?? "/ai/assistant";
  const { messages, input, handleInputChange, handleSubmit, isStreaming } = useAIStream({
    endpoint,
  });

  return (
    <div className="mx-auto flex min-h-[calc(100dvh-5rem)] max-w-2xl flex-col px-6 py-8">
      <header className="mb-4">
        <h1 className="text-2xl font-semibold">Assistant</h1>
        <p className="text-muted-foreground text-sm">
          Streaming from <code className="text-accent">{endpoint}</code> — tool calls hit the
          knowledge base through search_docs.
        </p>
      </header>

      <Card aria-live="polite" className="flex-1 gap-3 overflow-y-auto p-4">
        {messages.length === 0 ? (
          <p className="text-muted-foreground">Ask the Fastplace AI agent…</p>
        ) : (
          messages.map((msg, idx) => (
            <div
              key={idx}
              className={
                msg.role === "user"
                  ? "bg-primary text-primary-foreground ml-auto max-w-xs rounded-lg p-3"
                  : "bg-surface border border-line max-w-md rounded-lg p-3"
              }
            >
              {msg.content}
            </div>
          ))
        )}
        {isStreaming && (
          <p className="text-muted-foreground animate-pulse text-sm" data-testid="streaming">
            thinking…
          </p>
        )}
      </Card>

      <form onSubmit={handleSubmit} className="mt-4 flex gap-2">
        <Input
          value={input}
          onChange={handleInputChange}
          placeholder="Ask the Fastplace AI agent..."
          aria-label="Message"
          className="flex-1"
        />
        <Button type="submit" disabled={isStreaming}>
          Send
        </Button>
      </form>
    </div>
  );
}

// Persistent-layout opt-in — the bridge reads this static on the component.
AssistantChat.layout = AppLayout;
