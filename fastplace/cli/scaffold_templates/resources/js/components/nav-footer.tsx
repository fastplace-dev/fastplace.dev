import * as React from "react";

import {
  SidebarGroup,
  SidebarGroupContent,
  SidebarMenu,
  SidebarMenuButton,
  SidebarMenuItem,
} from "@/components/ui/sidebar";
import { toUrl } from "@/lib/utils";
import type { NavItem } from "@/types";

export function NavFooter({
  items,
  className,
  ...props
}: React.ComponentProps<typeof SidebarGroup> & {
  items: NavItem[];
}) {
  return (
    <SidebarGroup {...props} className={`group-data-[collapsible=icon]:p-0 ${className || ""}`}>
      <SidebarGroupContent>
        <SidebarMenu>
          {items.map((item) => (
            <SidebarMenuItem key={item.title}>
              <SidebarMenuButton asChild className="text-ink-muted hover:text-ink">
                <a href={toUrl(item.href)} target="_blank" rel="noopener noreferrer">
                  {item.icon && <item.icon className="h-5 w-5" />}
                  <span>{item.title}</span>
                </a>
              </SidebarMenuButton>
            </SidebarMenuItem>
          ))}
        </SidebarMenu>
      </SidebarGroupContent>
    </SidebarGroup>
  );
}
