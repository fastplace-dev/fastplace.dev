import { ChevronsUpDown } from "lucide-react";
import { usePage } from "@fastplace/react";

import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import {
  SidebarMenu,
  SidebarMenuButton,
  SidebarMenuItem,
  useSidebar,
} from "@/components/ui/sidebar";
import { UserInfo } from "@/components/user-info";
import { UserMenuContent } from "@/components/user-menu-content";
import { useIsMobile } from "@/hooks/use-mobile";
import type { SharedData, User } from "@/types";

// Pre-auth backend every viewer is a guest, but the chrome keeps the profile
// control the reference app shows — a placeholder account stands in until the
// auth phase starts delivering a real user through the bridge props.
const GUEST_USER: User = {
  id: 0,
  name: "Guest",
  email: "",
  avatar: undefined,
  email_verified_at: null,
  created_at: "",
  updated_at: "",
};

export function NavUser() {
  const auth = usePage<SharedData>().props.auth;
  const { state } = useSidebar();
  const isMobile = useIsMobile();

  const user = auth?.user ?? GUEST_USER;

  return (
    <SidebarMenu>
      <SidebarMenuItem>
        <DropdownMenu>
          <DropdownMenuTrigger asChild>
            <SidebarMenuButton
              size="lg"
              className="group text-sidebar-accent-foreground data-[state=open]:bg-sidebar-accent"
              data-test="sidebar-menu-button"
            >
              <UserInfo user={user} />
              <ChevronsUpDown className="ml-auto size-4" />
            </SidebarMenuButton>
          </DropdownMenuTrigger>
          <DropdownMenuContent
            className="w-(--radix-dropdown-menu-trigger-width) min-w-56 rounded-lg"
            align="end"
            side={isMobile ? "bottom" : state === "collapsed" ? "left" : "bottom"}
          >
            <UserMenuContent user={user} />
          </DropdownMenuContent>
        </DropdownMenu>
      </SidebarMenuItem>
    </SidebarMenu>
  );
}
