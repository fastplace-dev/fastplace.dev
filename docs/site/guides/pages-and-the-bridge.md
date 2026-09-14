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
