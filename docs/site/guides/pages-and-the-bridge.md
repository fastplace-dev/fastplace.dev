# Pages & the bridge

Fastplace serves the web through a **server-driven SPA** (the Inertia
pattern): the backend owns routing, data, and validation; React owns
rendering and interaction. There is no client-side router to keep in sync
and no REST layer to design for your own UI.

## Rendering a page

Controllers return `render()` from `fastplace.http`:

```python
from fastplace.http import Controller, render


class ProjectController(Controller):
    async def show(self, request):
        project = await GetProject(request.path_params["id"]).handle()
        return render(
            request,
            component="Projects/Show",
            props={"project": project},
        )
```

- **Initial load** — the response is an HTML document containing a
  container element and the initial JSON payload (the view target name +
  props). The client mounts immediately; no data fetching for first paint.
- **Subsequent navigation** — link clicks are intercepted and sent with
  `X-Fastplace-Request: true`. The backend answers with a JSON payload of
  the new component name + props only. Both modes set `Vary` correctly.

## The React side

Pages live under `resources/js/pages/`, resolved by the view target:
`"Projects/Show"` → `resources/js/pages/Projects/Show.jsx`.

```jsx
import { usePage, Link } from "@fastplace/react";

export default function Show() {
    const { props } = usePage();
    return (
        <article>
            <h1>{props.project.title}</h1>
            <Link href="/projects">Back</Link>
        </article>
    );
}
```

Props cross the wire as JSON — dates arrive as strings, models as plain
objects. Type the contract in Python (below), not in JavaScript.

## Layouts

A page declares its layout statically; layouts persist across navigation
instead of remounting:

```jsx
import AppLayout from "@/layouts/AppLayout";

export default function Show() { /* ... */ }
Show.layout = AppLayout;
```

Apps that prefer convention can run an `applyLayouts` step over the page
map instead of touching each file.

## The unified API

The same modules also serve native clients at `/api/v1`. Annotate a
Pydantic model return type and the framework serializes **and validates**
it — wrap collections in a response model (a bare `list[...]` annotation
is serialized but not validated):

```python
from app.modules.projects.services.results import ProjectsPage


class ApiProjectController(Controller):
    async def index(self, request) -> ProjectsPage:
        return ProjectsPage(data=await ListProjects().handle(), total=...)
```

Outbound Pydantic models are the wire contract; `model_dump(mode="json")`
for bridge props keeps the two surfaces byte-identical.

## Forms and validation

Requests are Pydantic models in `app/http/requests/`:

```python
from pydantic import BaseModel, Field


class StoreProjectRequest(BaseModel):
    title: str = Field(min_length=1, max_length=120)


# in the controller
payload = await request.validate(StoreProjectRequest)
```

Invalid input maps to the standard 422 payload (field → messages) on both
the API and the bridge — the frontend can render errors from one shape.

## Navigation details

- `Link` performs the bridge visit; plain `<a href>` still works (full
  document load).
- Redirects (`Redirect` responses) are followed transparently by the
  bridge client.
- The bridge never re-runs page JavaScript on navigation — state in
  layouts survives; state in pages resets, by design.

## Page titles & SEO

Fastplace renders a **server-driven SPA**: the initial HTML document is
produced by the backend, and crawlers that do not execute JavaScript
(Facebook, Slack, iMessage, WhatsApp unfurlers, search bots) read its
`<head>`. Pass head metadata as keyword arguments to `render()` so every
tag is already present in the initial document:

```python
return render(
    request,
    component="Projects/Show",
    props={"project": project},
    title=f"{project.name} — Projects",
    description=project.summary,
    canonical=f"/projects/{project.id}",
    image="/covers/launch.png",
    og={"type": "article"},
)
```

- **`title`** — the `<title>` tag. Without it the title is derived from
  the component name (`Auth/ForgotPassword` → "Forgot Password").
- **`description`** — `<meta name="description">` and `og:description`.
- **`canonical`** — `<link rel="canonical">` and `og:url` (both carry
  the same absolute URL). Relative paths are resolved against the
  `APP_URL` config value — set it to your public origin in production.
  It is deliberately **never** derived from the request's `Host`
  header, which an attacker can poison.
- **`image`** — `og:image`, absolutized the same way.
- **`robots`** — `<meta name="robots">` (e.g. `noindex` on auth pages).
- **`og`** — Open Graph overrides and extras: `title`, `description`,
  `image`, `url`, `type`, `site_name`, plus any additional
  `og:*` key (`locale`, `video`, ...). `og:title`, `og:type` (default
  `website`) and `og:site_name` (default `APP_NAME`) always render;
  `twitter:card` defaults to `summary`, or `summary_large_image` when
  an image resolves.
- **`head_tags`** — a list of raw trusted tags appended verbatim
  (hreflang alternates, JSON-LD, ...).

On SPA navigation the `<Head>` component updates `document.title`
client-side; the head arguments only shape the initial document, which
is where crawlers look.

## Without JavaScript

Fastplace keeps the web's escape hatches open when JavaScript never
loads:

- **Reading** — the initial HTML document carries the page title, a
  description/OG block, and a `<noscript>` notice explaining the state.
  The SPA itself (the hydrated interface) requires JavaScript.
- **Forms** — `<Form>` renders a real `<form method="post">` and
  injects a hidden `_token` field carrying the session CSRF token, so a
  native form post works with JS disabled. The server answers with a
  303 redirect back to the form, flashing validation errors (or the
  CSRF-expiry message) onto the session; the reloaded page surfaces
  them through the standard `errors` prop. The same contract covers
  session-expired CSRF failures for JavaScript clients too.

This is the documented rendering contract: **SPA-first, server-owned
head, no-JS form posts**. Opt-in server-side rendering of the full
React tree (a Node render sidecar with graceful fallback) is on the
roadmap — see the blueprint's rendering-contract section.
