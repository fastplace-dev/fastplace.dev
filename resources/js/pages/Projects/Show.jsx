import React from "react";
import { usePage, Link } from "@fastplace/react";
import { Card, CardContent } from "../../components/ui/card";
import { Button } from "../../components/ui/button";
import { Input } from "../../components/ui/input";
import { Label } from "../../components/ui/label";
import AppLayout from "@/layouts/app-layout";

export default function ProjectsShow() {
  const { props } = usePage();
  const project = props.project ?? { tasks: [] };

  // Reads are a public demo surface, writes are authenticated (the route
  // middleware enforces it) — a guest never sees controls that would bounce.
  const user = props.auth?.user;

  // Native form posts cannot read the <meta> tag — the page props carry the
  // session CSRF token for the hidden _token field.
  const csrfToken = props.csrf_token ?? "";

  return (
    <div>
      <div className="mx-auto max-w-4xl px-6 py-10">
        <header className="mb-8">
          <Link href="/projects" className="text-accent text-sm">
            ← Projects
          </Link>
          <h1 className="text-2xl font-semibold mt-2">{project.name}</h1>
          {project.description ? (
            <p className="text-muted-foreground mt-1">{project.description}</p>
          ) : null}
        </header>

        <Card className="mb-6 gap-3 py-4">
          <CardContent className="px-4">
            {user ? (
              <form
                method="post"
                action={`/projects/${project.id}/tasks`}
                className="flex flex-wrap items-end gap-3"
              >
                <input type="hidden" name="_token" value={csrfToken} />
                <div className="min-w-48 flex-1">
                  <Label htmlFor="task-title" className="mb-1">
                    Task title
                  </Label>
                  <Input id="task-title" name="title" required maxLength={255} />
                </div>
                <Button type="submit">Add task</Button>
              </form>
            ) : (
              <p className="text-muted-foreground text-sm">
                Browsing the demo as a guest.{" "}
                <Link href="/login" className="text-accent">
                  Log in to add
                </Link>{" "}
                a task.
              </p>
            )}
          </CardContent>
        </Card>

        <Card className="py-6">
          <CardContent>
            {(project.tasks ?? []).length === 0 ? (
              <p className="text-muted-foreground">No tasks yet.</p>
            ) : (
              <ul className="divide-y divide-line">
                {project.tasks.map((task) => (
                  <li key={task.id} className="flex items-center justify-between gap-4 py-3">
                    <span className={task.completed ? "line-through text-muted-foreground" : ""}>
                      {task.title}
                    </span>
                    {user ? (
                      <Link
                        href={`/tasks/${task.id}/toggle`}
                        method="post"
                        className="text-accent text-sm"
                      >
                        {task.completed ? "Reopen" : "Mark done"}
                      </Link>
                    ) : null}
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
ProjectsShow.layout = AppLayout;
