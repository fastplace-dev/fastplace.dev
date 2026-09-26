import { usePage } from "@fastplace/react";

import AppLogoIcon from "@/components/app-logo-icon";

export default function AppLogo() {
  const { name, appName } = usePage<{ name?: string; appName?: string }>().props;
  // No shared props are injected yet — fall back to the dashboard's appName,
  // then the brand default, so the sidebar label is never blank.
  const label = name ?? appName ?? "Fastplace";

  return (
    <>
      <div className="bg-sidebar-primary text-sidebar-primary-foreground flex aspect-square size-8 items-center justify-center rounded-md">
        <AppLogoIcon className="size-6 fill-current text-sidebar-primary-foreground" />
      </div>
      <div className="ml-1 grid flex-1 text-left text-sm">
        <span className="mb-0.5 truncate leading-tight font-semibold">{label}</span>
      </div>
    </>
  );
}
