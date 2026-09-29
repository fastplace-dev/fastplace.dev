# @fastplace/react

The React side of the Fastplace bridge — a server-driven SPA over the
`X-Fastplace-Request` protocol (the Inertia pattern). Your controllers
`render(...)` Python-side; this package mounts the right page component
with the server-supplied props on the client.

- **ESM-only**: the package ships ECMAScript modules (no CommonJS build);
  use a bundler (Vite, webpack, esbuild) to consume it.
- **Install**: the Fastplace app template wires this in for you
  (`npm install @fastplace/react` in a manual setup)
- **Docs**: <https://fastplace.dev>
- **Source**: <https://github.com/fastplace-dev/fastplace.dev/tree/master/packages/react>

## What it does

- **Initial load** — the backend returns an HTML document with a container
  element plus the initial JSON payload (view target name + props). The
  router mounts that component immediately: no client-side data fetching
  for the first paint.
- **Subsequent navigation** — link clicks are intercepted; the request goes
  out with `X-Fastplace-Request: true`; the backend answers with a JSON
  payload of updated props + component names only. No REST/GraphQL
  boilerplate, no duplicated routing.
- **Layouts** — pages declare a layout statically (`Page.layout = AppLayout`)
  or through the app's `applyLayouts` step; layouts persist across
  navigation instead of remounting.

## usePage

Every page component reads its server-supplied props through one hook:

```jsx
import { usePage } from "@fastplace/react";

export default function ProjectIndex() {
    const { props } = usePage();
    return <ul>{props.projects.map((p) => <li key={p.id}>{p.title}</li>)}</ul>;
}
```

`props` is exactly what the controller passed to `render(request,
component="Projects/Index", props={...})` — the wire format is JSON, so
dates arrive as strings and the framework's typed DTO serialization on the
Python side is what guarantees the shape.

## Page resolution

Pages live under `resources/js/pages/`; the view target
`"Projects/Index"` resolves to `resources/js/pages/Projects/Index.jsx`.
The resolver is injectable — the app template registers its own to support
lazy imports or custom roots.

## Navigation

```jsx
import { Link } from "@fastplace/react";

<Link href="/projects/42">Open project</Link>
```

`Link` performs the bridge visit (intercepted click → `X-Fastplace-Request`
round trip). Programmatic navigation uses the same router the `Link`
component uses; plain `<a href>` still works and simply triggers a full
document load.

## useBroadcast / usePresence

Live server push over the framework's `/ws/broadcast` WebSocket endpoint
(the backend guide lives at `https://fastplace.dev/guides/broadcasting`):

```jsx
import { useBroadcast, usePresence } from "@fastplace/react";

function OrderTracker({ orderId }) {
    const status = useBroadcast(`orders.${orderId}`, (message) => {
        // message = { channel: "orders.42", payload: {...} }
        setLatest(message.payload);
    });
    return <p data-live={status === "open"}>{statusLabel(status)}</p>;
}

function TeamBar({ roomId }) {
    const members = usePresence(`room.${roomId}`); // [{ user_id, metadata, connections }]
    return <ul>{members.map((m) => <li key={m.user_id}>{name(m.user_id)}</li>)}</ul>;
}
```

`useBroadcast` subscribes on open, delivers every `message` frame to the
callback, and survives dropped sockets: exponential-backoff reconnect
(1s doubling to a 30s ceiling), automatic resubscribe, then one
`onResubscribe(channels)` call — the hook for refetching anything missed
while disconnected. Changing the channel set closes and reopens the
socket. Unmount sends `unsubscribe` frames so presence rosters drop the
member immediately. During SSR (no `window`) both hooks stay dormant —
no socket, status `"closed"`, empty roster.

## Requirements

- React ≥ 18 (peer dependency; the monorepo develops against 19)
- A Fastplace backend serving bridge responses — `render()` from
  `fastplace.http` is the counterpart

## Development

```bash
npm install            # from the monorepo root
npm run build          # vite library build → dist/
npm run test           # vitest
```
