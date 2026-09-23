"""Web routes — bridge pages (controllers return render(...))."""

from __future__ import annotations

from app.http.controllers.about_controller import AboutController
from app.http.controllers.assistant_page_controller import AssistantPageController
from app.http.controllers.auth_page_controller import AuthPageController
from app.http.controllers.dashboard_controller import DashboardController
from app.http.controllers.home_controller import HomeController
from app.http.controllers.knowledge_controller import KnowledgeController
from app.http.controllers.projects_controller import ProjectsController
from app.http.controllers.settings_appearance_controller import SettingsAppearanceController
from app.http.controllers.settings_pages_controller import SettingsPagesController
from fastplace.http import Router

router = Router()

router.get("/", HomeController, "index", name="home")
router.get(
    "/dashboard",
    DashboardController,
    "index",
    name="dashboard",
    middleware=["auth", "verified"],
)
router.get("/about", AboutController, "index", name="about")
router.get("/assistant", AssistantPageController, "index", name="assistant")
router.get("/knowledge", KnowledgeController, "index", name="knowledge.index")

# Account settings pages — authenticated GETs (the settings section renders
# the app's authenticated shell); their form targets ship in later phases.
router.get(
    "/settings/appearance",
    SettingsAppearanceController,
    "index",
    name="settings.appearance",
    middleware=["auth"],
)
router.get(
    "/settings/profile",
    SettingsPagesController,
    "profile",
    name="settings.profile",
    middleware=["auth"],
)
router.get(
    "/settings/security",
    SettingsPagesController,
    "security",
    name="settings.security",
    middleware=["auth"],
)

# Guest auth pages — anonymous GET renders behind `guest`; the credential
# POSTs live in routes/auth.py.
router.get("/login", AuthPageController, "login", name="auth.login", middleware=["guest"])
router.get(
    "/register",
    AuthPageController,
    "register",
    name="auth.register",
    middleware=["guest"],
)
router.get("/forgot-password", AuthPageController, "forgot_password", name="auth.forgot_password")
router.get(
    "/reset-password/{token}",
    AuthPageController,
    "reset_password",
    name="auth.reset_password",
)
router.get(
    "/email/verify",
    AuthPageController,
    "verify_email",
    name="auth.verify_email",
    middleware=["auth"],
)
router.get(
    "/user/confirm-password",
    AuthPageController,
    "confirm_password",
    name="auth.confirm_password",
    middleware=["auth"],
)
router.get(
    "/two-factor-challenge",
    AuthPageController,
    "two_factor_challenge",
    name="auth.two_factor_challenge",
    middleware=["guest"],
)

router.get("/projects", ProjectsController, "index", name="projects.index")
router.post("/projects", ProjectsController, "store", name="projects.store")
router.get("/projects/{id}", ProjectsController, "show", name="projects.show")
router.post("/projects/{id}/tasks", ProjectsController, "store_task", name="projects.tasks.store")
router.post("/tasks/{id}/toggle", ProjectsController, "toggle_task", name="projects.tasks.toggle")
