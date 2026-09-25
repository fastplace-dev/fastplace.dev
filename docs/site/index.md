---
layout: home

hero:
  name: Fastplace
  text: Full-stack, AI-native, one deployable.
  tagline: An opinionated Python + React framework with a productive developer experience — server-driven SPA, unified API, and AI as infrastructure.
  image:
    src: /fastpklace-banner.png
    alt: Fastplace banner
  actions:
    - theme: brand
      text: Get started
      link: /getting-started
    - theme: alt
      text: View on GitHub
      link: https://github.com/firozanam/fastplace.dev

features:
  - icon: 🧩
    title: Modular monolith
    details: One deployable unit of strictly bounded modules. Controllers → Services → Repositories → Models, enforced by a CI boundary gate — not by discipline.
  - icon: 📡
    title: Server-driven SPA
    details: The Inertia pattern over the X-Fastplace-Request protocol. Controllers render() Python-side; React mounts the right page with the server's props. No REST boilerplate, no duplicated routing.
  - icon: 🔌
    title: Unified API
    details: The same modules serve /api/v1 for mobile and desktop. Pydantic DTO return annotations are serialized and validated by the framework — the wire shape is a contract, not a hope.
  - icon: 🤖
    title: AI as infrastructure
    details: "The @Tool decorator turns typed functions into LLM tool schemas. Agents stream over SSE straight into useAIStream. VectorField + Model.vector_search() put similarity next to your SQL."
  - icon: 🗄️
    title: Real ORM, any SQL
    details: "SQLAlchemy 2.x under a fluent API: relations, eager loading, global scopes (soft delete, tenancy), polymorphism, domain events to the queue. SQLite by default; PostgreSQL, MySQL, MongoDB supported."
  - icon: 🏢
    title: Opt-in multi-tenancy
    details: fastplace-tenancy adds company scoping at every layer — ORM scope, middleware binding, cache keys, job metadata, storage, search, vectors, RLS — fail-closed by design.
---

## The 60-second tour

```bash
fastplace new my-app && cd my-app
fastplace migrate && fastplace db:seed
fastplace run dev        # Uvicorn reload + Vite HMR, one command
```

A controller, a service, a repository, a model — the whole stack in four
small files:

```python
# routes/web.py
router.get("/projects", ProjectController, "index")


# app/http/controllers/project_controller.py
class ProjectController:
    async def index(self, request):
        projects = await ListProjects(request.user).handle()
        return render(request, component="Projects/Index", props={"projects": projects})
```

```jsx
// resources/js/pages/Projects/Index.jsx
import { usePage } from "@fastplace/react";

export default function Index() {
    const { props } = usePage();
    return <ul>{props.projects.map((p) => <li key={p.id}>{p.title}</li>)}</ul>;
}
```

## Where to go next

- [Getting started](/getting-started) — install, scaffold, first module
- [Pages & the bridge](/guides/pages-and-the-bridge) — how rendering works
- [Database & ORM](/guides/database) — models, relations, scopes, migrations
- [API overview](/api/overview) — the public surface at a glance
