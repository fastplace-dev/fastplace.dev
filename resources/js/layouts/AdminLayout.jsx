import React from "react";

// Scaffolded by `fastplace make:layout AdminLayout` — pages opt in via a
// `layout = AdminLayout` static on the page component; the bridge keeps this
// chrome mounted across navigation so its state survives page swaps.
export default function AdminLayout({ children }) {
  return (
    <div className="bg-surface text-ink min-h-dvh">
      <main className="mx-auto max-w-4xl px-6 py-10">{children}</main>
    </div>
  );
}
