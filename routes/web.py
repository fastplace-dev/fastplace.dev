"""Web routes — bridge pages (controllers return render(...))."""

from __future__ import annotations

from app.http.controllers.about_controller import AboutController
from app.http.controllers.dashboard_controller import DashboardController
from fastplace.http import Router

router = Router()

router.get("/", DashboardController, "index", name="dashboard")
router.get("/about", AboutController, "index", name="about")
