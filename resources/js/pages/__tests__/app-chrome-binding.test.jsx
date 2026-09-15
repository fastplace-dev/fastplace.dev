import { describe, expect, it } from "vitest";

import AppLayout from "@/layouts/app-layout";

import AssistantChat from "../Assistant/Chat";
import DashboardIndex from "../Dashboard/Index";
import KnowledgeIndex from "../Knowledge/Index";
import ProjectsIndex from "../Projects/Index";
import ProjectsShow from "../Projects/Show";

// The sample-app data pages render under the ported sidebar chrome — the
// same shell /settings/appearance uses — not the legacy top-header layout.
describe("app page chrome binding", () => {
  const pages = [
    ["Dashboard/Index", DashboardIndex],
    ["Projects/Index", ProjectsIndex],
    ["Projects/Show", ProjectsShow],
    ["Assistant/Chat", AssistantChat],
    ["Knowledge/Index", KnowledgeIndex],
  ];

  it.each(pages)("binds %s to the ported app sidebar layout", (_name, Page) => {
    expect(Page.layout).toBe(AppLayout);
  });
});
