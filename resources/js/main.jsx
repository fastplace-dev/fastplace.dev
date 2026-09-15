import { createFastplaceApp, createPageResolver } from "@fastplace/react";
import { Toaster } from "@/components/ui/sonner";
import { TooltipProvider } from "@/components/ui/tooltip";
import { initializeTheme } from "@/hooks/use-appearance";
import "../css/app.css";

// Seed light/dark mode from the persisted preference before the React root
// paints, so the first frame already carries the right token palette.
initializeTheme();

// Glob-declared pages: every resources/js/pages/**/*.{jsx,tsx} file is a
// routable component, resolved by the payload's `component` name. Colocated
// __tests__ modules are excluded — they import vitest and must never reach
// the browser graph. The glob is eager on purpose: pages declare their
// persistent layout through a `layout` component static, and a lazy wrapper
// could not expose it before first render. At sample-app scale the up-front
// cost is negligible; an app that outgrows this can go lazy and give layout
// pages a eager glob of their own.
const resolvePage = createPageResolver(
  import.meta.glob(["./pages/**/*.{jsx,tsx,js,ts}", "!./pages/**/__tests__/**"], {
    eager: true,
  }),
);

createFastplaceApp({
  resolve: resolvePage,
  // App-level chrome, mounted inside the provider so the toaster can read
  // flash messages from the page payload.
  withApp: (app) => (
    <TooltipProvider delayDuration={0}>
      {app}
      <Toaster />
    </TooltipProvider>
  ),
}).catch((err) => {
  console.error("[fastplace] bootstrap failed:", err);
  const el = document.getElementById("fastplace");
  if (el) {
    el.textContent = "Failed to boot the Fastplace app — check the browser console.";
  }
});
