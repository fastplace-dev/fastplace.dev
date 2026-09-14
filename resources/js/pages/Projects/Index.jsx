import React from "react";
import { usePage, Link } from "@fastplace/react";

export default function ProjectsIndex() {
  const { props } = usePage();
  const projects = props.projects ?? [];

  // Native form posts cannot read the <meta> tag — the page props carry the
  // session CSRF token for the hidden _token field.
  const csrfToken = props.csrf_token ?? "";

  return (
    <main className="min-h-dvh bg-surface text-ink">
      <div className="mx-auto max-w-4xl px-6 py-10">
        <header className="mb-8 flex items-baseline justify-between">
          <h1 className="text-2xl font-semibold">Projects</h1>
          <Link href="/" className="text-accent text-sm">
            ← Dashboard
          </Link>
        </header>

        <form
          method="post"
          action="/projects"
          className="bg-surface-raised border border-line rounded-xl p-4 mb-6 flex flex-wrap gap-3 items-end"
        >
          <input type="hidden" name="_token" value={csrfToken} />
          <label className="flex-1 min-w-48">
            <span className="block text-ink-muted text-sm mb-1">Project name</span>
            <input
              name="name"
              required
              maxLength={255}
              className="w-full rounded-lg border border-line bg-surface px-3 py-2"
            />
          </label>
          <label className="flex-1 min-w-48">
            <span className="block text-ink-muted text-sm mb-1">Description</span>
            <input
              name="description"
              maxLength={2000}
              className="w-full rounded-lg border border-line bg-surface px-3 py-2"
            />
          </label>
          <button
            type="submit"
            className="rounded-lg bg-accent px-4 py-2 text-sm font-medium text-surface"
          >
            Add project
          </button>
        </form>

        <section className="bg-surface-raised border border-line rounded-xl p-6">
          {projects.length === 0 ? (
            <p className="text-ink-muted">No projects yet.</p>
          ) : (
            <ul className="divide-y divide-line">
              {projects.map((project) => (
                <li key={project.id} className="py-3 flex justify-between gap-4">
                  <div>
                    <Link href={`/projects/${project.id}`} className="text-accent font-medium">
                      {project.name}
                    </Link>
                    {project.description ? (
                      <p className="text-ink-muted text-sm">{project.description}</p>
                    ) : null}
                  </div>
                  <span className="text-ink-muted text-sm whitespace-nowrap">
                    {project.task_count ?? 0} tasks · {project.open_task_count ?? 0} open
                  </span>
                </li>
              ))}
            </ul>
          )}
        </section>
      </div>
    </main>
  );
}
