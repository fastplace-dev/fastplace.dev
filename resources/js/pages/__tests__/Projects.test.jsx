import "@testing-library/jest-dom/vitest";
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";
import React from "react";

import { FastplaceProvider, router } from "@fastplace/react";

import ProjectsIndex from "../Projects/Index";
import ProjectsShow from "../Projects/Show";

const testUser = {
  id: 1,
  name: "Jane Doe",
  email: "jane@example.com",
  email_verified_at: null,
  created_at: "2026-01-01T00:00:00Z",
  updated_at: "2026-01-01T00:00:00Z",
};

function renderPage(component, url, props) {
  return render(
    <FastplaceProvider initialPage={{ component, url, props }}>
      {component === "Projects/Index" ? <ProjectsIndex /> : <ProjectsShow />}
    </FastplaceProvider>,
  );
}

afterEach(() => {
  cleanup();
  router.reset();
});

describe("Projects index", () => {
  it("shows the add-project form to an authenticated visitor", () => {
    renderPage("Projects/Index", "/projects", { projects: [], auth: { user: testUser } });

    expect(screen.getByRole("button", { name: "Add project" })).toBeInTheDocument();
  });

  it("hides write controls from guests — reads stay public", () => {
    renderPage("Projects/Index", "/projects", { projects: [] });

    expect(screen.queryByRole("button", { name: "Add project" })).not.toBeInTheDocument();
    expect(screen.getByRole("link", { name: /log in to add/i })).toHaveAttribute("href", "/login");
  });
});

describe("Projects show", () => {
  const openTask = { id: 7, title: "a task", completed: false };

  it("shows the add-task form and toggle links to an authenticated visitor", () => {
    renderPage("Projects/Show", "/projects/1", {
      project: { id: 1, name: "Demo", tasks: [openTask] },
      auth: { user: testUser },
    });

    expect(screen.getByRole("button", { name: "Add task" })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Mark done" })).toBeInTheDocument();
  });

  it("hides every write control from guests", () => {
    renderPage("Projects/Show", "/projects/1", {
      project: { id: 1, name: "Demo", tasks: [openTask] },
    });

    expect(screen.queryByRole("button", { name: "Add task" })).not.toBeInTheDocument();
    expect(screen.queryByRole("link", { name: "Mark done" })).not.toBeInTheDocument();
    expect(screen.getByRole("link", { name: /log in to add/i })).toHaveAttribute("href", "/login");
    // The read surface stays: the task itself is still listed.
    expect(screen.getByText("a task")).toBeInTheDocument();
  });
});
