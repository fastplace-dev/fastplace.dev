import "@testing-library/jest-dom/vitest";
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";
import React from "react";
import { FastplaceProvider, router } from "@fastplace/react";
import DashboardIndex from "../pages/Dashboard/Index.jsx";
import ProjectsIndex from "../pages/Projects/Index.jsx";
import ProjectsShow from "../pages/Projects/Show.jsx";

afterEach(() => {
  cleanup();
  router.reset();
});

const STATS = { projects: 2, open_tasks: 3, completed_tasks: 1 };

// The write controls render for authenticated visitors only — reads stay
// public. Guest gating has its own suite (pages/__tests__/Projects).
const AUTH = { auth: { user: { id: 1, name: "Jane", email: "jane@example.com" } } };

describe("Dashboard/Index", () => {
  it("renders the module stats from the dashboard service", () => {
    render(
      <FastplaceProvider
        initialPage={{
          component: "Dashboard/Index",
          props: {
            appName: "Fastplace",
            url: "/",
            stats: STATS,
            knowledge_items: 5,
            recent_projects: [
              { id: 1, name: "Framework build", task_count: 3, open_task_count: 2 },
            ],
          },
          url: "/",
        }}
      >
        <DashboardIndex />
      </FastplaceProvider>,
    );
    expect(screen.getByRole("heading", { name: "Fastplace" })).toBeInTheDocument();
    expect(screen.getByText("2 projects")).toBeInTheDocument();
    expect(screen.getByText("3 open tasks")).toBeInTheDocument();
    expect(screen.getByText("1 completed")).toBeInTheDocument();
    expect(screen.getByText("5 knowledge items")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: /Framework build/ })).toHaveAttribute(
      "href",
      "/projects/1",
    );
  });

  it("shows the empty state before anything is seeded", () => {
    render(
      <FastplaceProvider
        initialPage={{
          component: "Dashboard/Index",
          props: {
            appName: "Fastplace",
            url: "/",
            stats: { projects: 0, open_tasks: 0, completed_tasks: 0 },
            knowledge_items: 0,
            recent_projects: [],
          },
          url: "/",
        }}
      >
        <DashboardIndex />
      </FastplaceProvider>,
    );
    expect(screen.getByText("No projects yet.")).toBeInTheDocument();
  });
});

describe("Projects/Index", () => {
  it("lists projects with their task counts and a create form", () => {
    render(
      <FastplaceProvider
        initialPage={{
          component: "Projects/Index",
          props: {
            ...AUTH,
            projects: [
              { id: 7, name: "Alpha", description: "first", task_count: 2, open_task_count: 1 },
            ],
          },
          url: "/projects",
        }}
      >
        <ProjectsIndex />
      </FastplaceProvider>,
    );
    expect(screen.getByRole("heading", { name: "Projects" })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: /Alpha/ })).toHaveAttribute("href", "/projects/7");
    expect(screen.getByText("2 tasks · 1 open")).toBeInTheDocument();
    expect(screen.getByLabelText("Project name")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Add project" })).toBeInTheDocument();
  });

  it("shows the empty state", () => {
    render(
      <FastplaceProvider
        initialPage={{
          component: "Projects/Index",
          props: { projects: [] },
          url: "/projects",
        }}
      >
        <ProjectsIndex />
      </FastplaceProvider>,
    );
    expect(screen.getByText("No projects yet.")).toBeInTheDocument();
  });
});

describe("Projects/Show", () => {
  const PAGE = {
    component: "Projects/Show",
    props: {
      ...AUTH,
      project: {
        id: 3,
        name: "Framework build",
        description: "sample",
        tasks: [
          { id: 11, title: "Write the ORM contract", completed: false },
          { id: 12, title: "Ship the React bridge", completed: true },
        ],
      },
    },
    url: "/projects/3",
  };

  it("renders the task list with completion state", () => {
    render(
      <FastplaceProvider initialPage={PAGE}>
        <ProjectsShow />
      </FastplaceProvider>,
    );
    expect(screen.getByRole("heading", { name: "Framework build" })).toBeInTheDocument();
    expect(screen.getByText("Write the ORM contract")).not.toHaveClass("line-through");
    expect(screen.getByText("Ship the React bridge")).toHaveClass("line-through");
    expect(screen.getByRole("link", { name: /Mark done/i })).toBeInTheDocument();
  });

  it("offers an add-task form posting back to the project", () => {
    render(
      <FastplaceProvider initialPage={PAGE}>
        <ProjectsShow />
      </FastplaceProvider>,
    );
    const form = screen.getByRole("button", { name: "Add task" }).closest("form");
    expect(form).not.toBeNull();
    expect(form).toHaveAttribute("method", "post");
    expect(form).toHaveAttribute("action", "/projects/3/tasks");
    expect(screen.getByLabelText("Task title")).toBeInTheDocument();
  });
});

describe("native form posts carry the CSRF token", () => {
  it("Projects/Index embeds the session token as a hidden _token field", () => {
    render(
      <FastplaceProvider
        initialPage={{
          component: "Projects/Index",
          props: { ...AUTH, projects: [], csrf_token: "tok-abc123" },
          url: "/projects",
        }}
      >
        <ProjectsIndex />
      </FastplaceProvider>,
    );
    const form = screen.getByRole("button", { name: "Add project" }).closest("form");
    const token = form.querySelector('input[name="_token"]');
    expect(token).not.toBeNull();
    expect(token).toHaveAttribute("type", "hidden");
    expect(token).toHaveAttribute("value", "tok-abc123");
  });

  it("Projects/Show embeds the session token as a hidden _token field", () => {
    render(
      <FastplaceProvider
        initialPage={{
          component: "Projects/Show",
          props: {
            ...AUTH,
            project: { id: 3, name: "P", description: "", tasks: [] },
            csrf_token: "tok-def456",
          },
          url: "/projects/3",
        }}
      >
        <ProjectsShow />
      </FastplaceProvider>,
    );
    const form = screen.getByRole("button", { name: "Add task" }).closest("form");
    expect(form.querySelector('input[name="_token"]')).toHaveAttribute("value", "tok-def456");
  });
});
