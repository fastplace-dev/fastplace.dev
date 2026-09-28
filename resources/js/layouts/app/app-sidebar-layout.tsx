import { AppContent } from "@/components/app-content";
import { AppShell } from "@/components/app-shell";
import { AppSidebar } from "@/components/app-sidebar";
import { AppSidebarHeader } from "@/components/app-sidebar-header";
import { SkipToContent } from "@/components/skip-to-content";
import type { AppLayoutProps } from "@/types";

export default function AppSidebarLayout({ children, breadcrumbs = [] }: AppLayoutProps) {
  return (
    <AppShell variant="sidebar">
      <SkipToContent />
      <AppSidebar />
      <AppContent
        variant="sidebar"
        id="main-content"
        tabIndex={-1}
        className="min-w-0 overflow-x-clip focus:outline-none"
      >
        <AppSidebarHeader breadcrumbs={breadcrumbs} />
        {children}
      </AppContent>
    </AppShell>
  );
}
