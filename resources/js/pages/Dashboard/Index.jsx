import React from "react";
import { usePage, Link } from "@fastplace/react";
import { Card, CardContent, CardHeader, CardTitle } from "../../components/ui/card";
import AppLayout from "@/layouts/app-layout";

export default function DashboardIndex() {
  const { props } = usePage();
  const stats = props.stats ?? { projects: 0, open_tasks: 0, completed_tasks: 0 };
  const knowledgeItems = props.knowledge_items ?? 0;
  const recentProjects = props.recent_projects ?? [];

  const cards = [
    { value: stats.projects, label: `${stats.projects} projects` },
    { value: stats.open_tasks, label: `${stats.open_tasks} open tasks` },
    { value: stats.completed_tasks, label: `${stats.completed_tasks} completed` },
    { value: knowledgeItems, label: `${knowledgeItems} knowledge items` },
  ];

  return (
    <div>
      <div className="mx-auto max-w-4xl px-6 py-10">
        <header className="mb-8 flex items-baseline justify-between">
          <h1 className="text-2xl font-semibold">{props.appName ?? "Fastplace"}</h1>
          <span className="text-muted-foreground text-sm">{props.url}</span>
        </header>

        <section className="grid grid-cols-2 gap-4 sm:grid-cols-4" aria-label="Stats">
          {cards.map(({ value, label }) => (
            <Card key={label} className="gap-2 py-4">
              <CardContent className="px-4">
                <p className="text-2xl font-semibold tabular-nums">{value}</p>
                <p className="text-muted-foreground text-sm">{label}</p>
              </CardContent>
            </Card>
          ))}
        </section>

        <Card className="mt-6">
          <CardHeader className="flex-row items-baseline justify-between">
            <CardTitle className="text-lg font-medium">Recent projects</CardTitle>
            <Link href="/projects" className="text-accent text-sm">
              All projects →
            </Link>
          </CardHeader>
          <CardContent>
            {recentProjects.length === 0 ? (
              <p className="text-muted-foreground">No projects yet.</p>
            ) : (
              <ul className="divide-y divide-line">
                {recentProjects.map((project) => (
                  <li key={project.id} className="flex items-baseline justify-between py-3">
                    <Link href={`/projects/${project.id}`} className="text-accent font-medium">
                      {project.name}
                    </Link>
                    <span className="text-muted-foreground text-sm tabular-nums">
                      {project.task_count ?? 0} tasks · {project.open_task_count ?? 0} open
                    </span>
                  </li>
                ))}
              </ul>
            )}
          </CardContent>
        </Card>
      </div>
    </div>
  );
}

// Persistent-layout opt-in — the bridge reads this static on the component.
DashboardIndex.layout = AppLayout;
