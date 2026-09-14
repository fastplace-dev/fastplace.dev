import React from "react";
import { usePage } from "@fastplace/react";
import AppLayout from "../../layouts/AppLayout";

// Scaffolded by `fastplace make:page About/Index` — props arrive from the
// controller that renders this component via `render(request, component="About/Index", props=...)`.
export default function AboutIndex() {
  const { props, url } = usePage();

  return (
    <div className="p-8">
      <h1 className="text-2xl font-semibold">About/Index</h1>
      <p className="mt-2 text-sm opacity-70">Served by {url} with bridge props:</p>
      <pre className="mt-4 text-xs">{JSON.stringify(props, null, 2)}</pre>
    </div>
  );
}

// Persistent-layout opt-in — the bridge reads this static on the component.
AboutIndex.layout = AppLayout;
