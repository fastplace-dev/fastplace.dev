import React from "react";
import { usePage, Link } from "@fastplace/react";

export default function DashboardIndex() {
  const { props } = usePage();
  const projects = props.projects ?? [];

  return (
    <main className="min-h-dvh bg-surface text-ink">
      <div className="mx-auto max-w-4xl px-6 py-10">
        <header className="mb-8 flex items-baseline justify-between">
          <h1 className="text-2xl font-semibold">{props.appName ?? "Fastplace"}</h1>
          <span className="text-ink-muted text-sm">{props.url}</span>
        </header>

        <section className="bg-surface-raised border border-line rounded-xl p-6">
          <h2 className="text-lg font-medium mb-4">Projects</h2>
          {projects.length === 0 ? (
            <p className="text-ink-muted">No projects yet.</p>
          ) : (
            <ul className="divide-y divide-line">
              {projects.map((project) => (
                <li key={project.id} className="py-3 flex justify-between">
                  <span>{project.name}</span>
                  <span className="text-ink-muted text-sm">{project.task_count ?? 0} tasks</span>
                </li>
              ))}
            </ul>
          )}
        </section>

        <nav className="mt-6 flex gap-6 text-accent">
          <Link href="/about">About</Link>
          <a href="/api/v1/health">API health</a>
        </nav>
      </div>
    </main>
  );
}
