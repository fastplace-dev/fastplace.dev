import React from "react";
import { usePage, Link } from "@fastplace/react";

export default function ProjectsShow() {
  const { props } = usePage();
  const project = props.project ?? { tasks: [] };

  // Native form posts cannot read the <meta> tag — the page props carry the
  // session CSRF token for the hidden _token field.
  const csrfToken = props.csrf_token ?? "";

  return (
    <main className="min-h-dvh bg-surface text-ink">
      <div className="mx-auto max-w-4xl px-6 py-10">
        <header className="mb-8">
          <Link href="/projects" className="text-accent text-sm">
            ← Projects
          </Link>
          <h1 className="text-2xl font-semibold mt-2">{project.name}</h1>
          {project.description ? (
            <p className="text-ink-muted mt-1">{project.description}</p>
          ) : null}
        </header>

        <form
          method="post"
          action={`/projects/${project.id}/tasks`}
          className="bg-surface-raised border border-line rounded-xl p-4 mb-6 flex gap-3 items-end"
        >
          <input type="hidden" name="_token" value={csrfToken} />
          <label className="flex-1">
            <span className="block text-ink-muted text-sm mb-1">Task title</span>
            <input
              name="title"
              required
              maxLength={255}
              className="w-full rounded-lg border border-line bg-surface px-3 py-2"
            />
          </label>
          <button
            type="submit"
            className="rounded-lg bg-accent px-4 py-2 text-sm font-medium text-surface"
          >
            Add task
          </button>
        </form>

        <section className="bg-surface-raised border border-line rounded-xl p-6">
          {(project.tasks ?? []).length === 0 ? (
            <p className="text-ink-muted">No tasks yet.</p>
          ) : (
            <ul className="divide-y divide-line">
              {project.tasks.map((task) => (
                <li key={task.id} className="py-3 flex items-center justify-between gap-4">
                  <span className={task.completed ? "line-through text-ink-muted" : ""}>
                    {task.title}
                  </span>
                  <Link
                    href={`/tasks/${task.id}/toggle`}
                    method="post"
                    className="text-accent text-sm"
                  >
                    {task.completed ? "Reopen" : "Mark done"}
                  </Link>
                </li>
              ))}
            </ul>
          )}
        </section>
      </div>
    </main>
  );
}
