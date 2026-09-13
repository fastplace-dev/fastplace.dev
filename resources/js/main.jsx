import { createFastplaceApp, createPageResolver } from "@fastplace/react";
import "../css/app.css";

// Glob-declared pages: every resources/js/pages/**/*.{jsx,tsx} file is a
// routable component, resolved by the payload's `component` name. The glob
// is lazy — each page loads on first render inside the bridge Suspense
// boundary, keeping the initial bundle small.
const resolvePage = createPageResolver(import.meta.glob("./pages/**/*.{jsx,tsx,js,ts}"));

createFastplaceApp({ resolve: resolvePage }).catch((err) => {
  console.error("[fastplace] bootstrap failed:", err);
  const el = document.getElementById("fastplace");
  if (el) {
    el.textContent = "Failed to boot the Fastplace app — check the browser console.";
  }
});
