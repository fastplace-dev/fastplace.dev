"""Web routes — bridge pages (controllers return render(...))."""

from __future__ import annotations

from app.http.controllers.about_controller import AboutController
from app.http.controllers.dashboard_controller import DashboardController
from app.http.controllers.projects_controller import ProjectsController
from fastplace.http import Router

router = Router()

router.get("/", DashboardController, "index", name="dashboard")
router.get("/about", AboutController, "index", name="about")

router.get("/projects", ProjectsController, "index", name="projects.index")
router.post("/projects", ProjectsController, "store", name="projects.store")
router.get("/projects/{id}", ProjectsController, "show", name="projects.show")
router.post("/projects/{id}/tasks", ProjectsController, "store_task", name="projects.tasks.store")
router.post("/tasks/{id}/toggle", ProjectsController, "toggle_task", name="projects.tasks.toggle")
