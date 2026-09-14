# @fastplace/ai-react

Streaming AI hooks for Fastplace — React bindings for the framework's
`/ai/*` SSE endpoints. Agents stream tokens as they are produced; these
hooks consume the stream and hand you reactive state for it.

- **Install**: wired into the Fastplace app template
  (`npm install @fastplace/ai-react` in a manual setup)
- **Docs**: <https://fastplace.dev>
- **Source**: <https://github.com/firozanam/fastplace.dev/tree/master/packages/ai-react>

## useAIStream

The main hook — point it at an agent endpoint and render the stream:

```jsx
import { useAIStream } from "@fastplace/ai-react";

export default function Assistant() {
    const { messages, input, setInput, handleSubmit, isStreaming } = useAIStream({
        endpoint: "/ai/assistant",
    });

    return (
        <>
            <ul>
                {messages.map((m, i) => (
                    <li key={i}><strong>{m.role}:</strong> {m.content}</li>
                ))}
            </ul>
            <form onSubmit={handleSubmit}>
                <input value={input} onChange={(e) => setInput(e.target.value)} />
                <button disabled={isStreaming}>Send</button>
            </form>
        </>
    );
}
```

The backend is an `Agent` registered through `routes/ai.py`; its
`stream_response(...)` returns the SSE response this hook parses. Tool
calls made by the agent surface in the stream payload as they resolve —
the hook does not hide them, it exposes them on the message objects
(`role: "tool"`).

## useAgent

The programmatic variant — same streaming contract, no form wiring:

```jsx
const { messages, isStreaming, send, reset } = useAgent({ endpoint: "/ai/researcher" });
```

`send(message)` starts a run (a send while streaming is ignored); `delta`
chunks and `tool` executions fold into `messages`, and `reset()` aborts
any in-flight run and clears the conversation (unmount aborts too). Raw
SSE frames are not exposed here — import `createSSEParser()` if you need
frame-level access.

## SSE details the hooks handle for you

- **Reconnection-safe parsing** — partial frames never corrupt state; the
  parser accumulates buffers per event boundary.
- **Abort** — canceling a run closes the underlying `fetch` body instead of
  letting the stream dangle.
- **Backpressure-free rendering** — deltas are batched into state updates,
  not one `setState` per token.

## Requirements

- React ≥ 18 (peer dependency)
- A Fastplace backend exposing `routes/ai.py` agent endpoints

## Development

```bash
npm install            # from the monorepo root
npm run build          # vite library build → dist/
npm run test           # vitest
```
