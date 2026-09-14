import { createFastplaceApp, createPageResolver } from "@fastplace/react";
import "../css/app.css";

// Glob-declared pages: every resources/js/pages/**/*.{jsx,tsx} file is a
// routable component, resolved by the payload's `component` name. The glob
// is eager on purpose: pages declare their persistent layout through a
// `layout` component static, and a lazy wrapper could not expose it before
// first render. At sample-app scale the up-front cost is negligible; an app
// that outgrows this can go lazy and give layout pages a eager glob of
// their own.
const resolvePage = createPageResolver(
  import.meta.glob("./pages/**/*.{jsx,tsx,js,ts}", { eager: true }),
);

createFastplaceApp({ resolve: resolvePage }).catch((err) => {
  console.error("[fastplace] bootstrap failed:", err);
  const el = document.getElementById("fastplace");
  if (el) {
    el.textContent = "Failed to boot the Fastplace app — check the browser console.";
  }
});
