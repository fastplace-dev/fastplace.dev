import React from "react";
import { usePage, Link } from "@fastplace/react";
import AppLayout from "../../layouts/AppLayout";

export default function DashboardIndex() {
  const { props } = usePage();
  const stats = props.stats ?? { projects: 0, open_tasks: 0, completed_tasks: 0 };
  const knowledgeItems = props.knowledge_items ?? 0;
  const recentProjects = props.recent_projects ?? [];

  return (
    <main>
      <div className="mx-auto max-w-4xl px-6 py-10">
        <header className="mb-8 flex items-baseline justify-between">
          <h1 className="text-2xl font-semibold">{props.appName ?? "Fastplace"}</h1>
          <span className="text-ink-muted text-sm">{props.url}</span>
        </header>

        <section className="grid grid-cols-2 gap-4 sm:grid-cols-4" aria-label="Stats">
          <div className="bg-surface-raised border border-line rounded-xl p-4">
            <p className="text-2xl font-semibold">{stats.projects}</p>
            <p className="text-ink-muted text-sm">{stats.projects} projects</p>
          </div>
          <div className="bg-surface-raised border border-line rounded-xl p-4">
            <p className="text-2xl font-semibold">{stats.open_tasks}</p>
            <p className="text-ink-muted text-sm">{stats.open_tasks} open tasks</p>
          </div>
          <div className="bg-surface-raised border border-line rounded-xl p-4">
            <p className="text-2xl font-semibold">{stats.completed_tasks}</p>
            <p className="text-ink-muted text-sm">{stats.completed_tasks} completed</p>
          </div>
          <div className="bg-surface-raised border border-line rounded-xl p-4">
            <p className="text-2xl font-semibold">{knowledgeItems}</p>
            <p className="text-ink-muted text-sm">{knowledgeItems} knowledge items</p>
          </div>
        </section>

        <section className="mt-6 bg-surface-raised border border-line rounded-xl p-6">
          <div className="mb-4 flex items-baseline justify-between">
            <h2 className="text-lg font-medium">Recent projects</h2>
            <Link href="/projects" className="text-accent text-sm">
              All projects →
            </Link>
          </div>
          {recentProjects.length === 0 ? (
            <p className="text-ink-muted">No projects yet.</p>
          ) : (
            <ul className="divide-y divide-line">
              {recentProjects.map((project) => (
                <li key={project.id} className="py-3 flex justify-between">
                  <Link href={`/projects/${project.id}`} className="text-accent">
                    {project.name}
                  </Link>
                  <span className="text-ink-muted text-sm">
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

// Persistent-layout opt-in — the bridge reads this static on the component.
DashboardIndex.layout = AppLayout;
