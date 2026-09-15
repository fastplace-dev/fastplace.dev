import React from "react";
import { usePage, Link } from "@fastplace/react";
import { Card, CardContent } from "../../components/ui/card";
import { Button } from "../../components/ui/button";
import { Input } from "../../components/ui/input";
import { Label } from "../../components/ui/label";
import AppLayout from "@/layouts/app-layout";

export default function ProjectsIndex() {
  const { props } = usePage();
  const projects = props.projects ?? [];

  // Native form posts cannot read the <meta> tag — the page props carry the
  // session CSRF token for the hidden _token field.
  const csrfToken = props.csrf_token ?? "";

  return (
    <div>
      <div className="mx-auto max-w-4xl px-6 py-10">
        <header className="mb-8 flex items-baseline justify-between">
          <h1 className="text-2xl font-semibold">Projects</h1>
          <Link href="/dashboard" className="text-accent text-sm">
            ← Dashboard
          </Link>
        </header>

        <Card className="mb-6 gap-3 py-4">
          <CardContent className="px-4">
            <form method="post" action="/projects" className="flex flex-wrap items-end gap-3">
              <input type="hidden" name="_token" value={csrfToken} />
              <div className="min-w-48 flex-1">
                <Label htmlFor="project-name" className="mb-1">
                  Project name
                </Label>
                <Input id="project-name" name="name" required maxLength={255} />
              </div>
              <div className="min-w-48 flex-1">
                <Label htmlFor="project-description" className="mb-1">
                  Description
                </Label>
                <Input id="project-description" name="description" maxLength={2000} />
              </div>
              <Button type="submit">Add project</Button>
            </form>
          </CardContent>
        </Card>

        <Card className="py-6">
          <CardContent>
            {projects.length === 0 ? (
              <p className="text-muted-foreground">No projects yet.</p>
            ) : (
              <ul className="divide-y divide-line">
                {projects.map((project) => (
                  <li key={project.id} className="flex items-baseline justify-between gap-4 py-3">
                    <div>
                      <Link href={`/projects/${project.id}`} className="text-accent font-medium">
                        {project.name}
                      </Link>
                      {project.description ? (
                        <p className="text-muted-foreground text-sm">{project.description}</p>
                      ) : null}
                    </div>
                    <span className="text-muted-foreground text-sm tabular-nums whitespace-nowrap">
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
ProjectsIndex.layout = AppLayout;
