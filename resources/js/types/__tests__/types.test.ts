import "@testing-library/jest-dom/vitest";
import { cleanup } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";
import type { Auth, BreadcrumbItem, NavItem, SharedData, User } from "@/types";

afterEach(cleanup);

// The barrel is types-only: the static imports below are erased at runtime,
// so a dynamic import is what actually proves the "@/types" alias resolves
// every underlying module — a broken path or re-export fails right here.
describe("@/types barrel", () => {
  it("resolves through the alias and stays in the module graph", async () => {
    const barrel = await import("@/types");
    expect(barrel).toBeTruthy();
  });

  it("accepts the shared page-props and navigation prop shapes", () => {
    const user: User = {
      id: 1,
      name: "Ada Lovelace",
      email: "ada@fastplace.dev",
      email_verified_at: null,
      created_at: "2026-09-15T00:00:00Z",
      updated_at: "2026-09-15T00:00:00Z",
    };
    const auth: Auth = { user };
    const shared: SharedData = {
      name: "Fastplace",
      auth,
      sidebarOpen: false,
    };
    const nav: NavItem = { title: "Dashboard", href: "/dashboard", icon: null, isActive: true };
    const crumb: BreadcrumbItem = { title: "Profile", href: "/settings/profile" };

    expect(shared.auth?.user.name).toBe("Ada Lovelace");
    expect(nav.href).toBe("/dashboard");
    expect(crumb.title).toBe("Profile");
  });
});
