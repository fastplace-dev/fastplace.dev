import { Link } from "@fastplace/react";
import { BookOpen, FolderGit2, LayoutGrid, Settings } from "lucide-react";

import AppLogo from "@/components/app-logo";
import { NavFooter } from "@/components/nav-footer";
import { NavMain } from "@/components/nav-main";
import { NavUser } from "@/components/nav-user";
import {
  Sidebar,
  SidebarContent,
  SidebarFooter,
  SidebarHeader,
  SidebarMenu,
  SidebarMenuButton,
  SidebarMenuItem,
} from "@/components/ui/sidebar";
import type { NavItem } from "@/types";

const mainNavItems: NavItem[] = [
  {
    title: "Dashboard",
    href: "/dashboard",
    icon: LayoutGrid,
  },
  {
    title: "Settings",
    href: "/settings/profile",
    icon: Settings,
  },
];

const footerNavItems: NavItem[] = [
  {
    title: "Repository",
    href: "https://github.com/fastplace-dev/fastplace.dev",
    icon: FolderGit2,
  },
  {
    title: "Documentation",
    href: "https://fastplace.dev/docs",
    icon: BookOpen,
  },
];

export function AppSidebar() {
  return (
    <Sidebar
      collapsible="icon"
      variant="inset"
      // The inset variant ships without an edge; draw the same hairline the
      // inset header uses underneath itself, on the content-facing side.
      className="border-r border-sidebar-border/50"
    >
      <SidebarHeader>
        <SidebarMenu>
          <SidebarMenuItem>
            <SidebarMenuButton size="lg" asChild>
              <Link href="/dashboard">
                <AppLogo />
              </Link>
            </SidebarMenuButton>
          </SidebarMenuItem>
        </SidebarMenu>
      </SidebarHeader>

      <SidebarContent>
        <NavMain items={mainNavItems} />
      </SidebarContent>

      <SidebarFooter>
        <NavFooter items={footerNavItems} className="mt-auto" />
        <NavUser />
      </SidebarFooter>
    </Sidebar>
  );
}
