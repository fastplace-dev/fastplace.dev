import React from "react";

// Scaffolded by `fastplace make:component Card` — compose into pages or
// other components. Theme tokens (bg-surface, text-ink, border-line, …) are
// Tailwind utilities defined in resources/css/app.css.
export function Card({ children }) {
  return (
    <div className="border-line bg-surface-raised text-ink rounded-lg border p-4">
      {children}
    </div>
  );
}
