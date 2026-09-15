import React from "react";
import { usePage } from "@fastplace/react";
import { Card, CardContent } from "../../components/ui/card";
import AppLayout from "../../layouts/AppLayout";

// Scaffolded by `fastplace make:page About/Index` — props arrive from the
// controller that renders this component via `render(request, component="About/Index", props=...)`.
export default function AboutIndex() {
  const { props, url } = usePage();

  return (
    <main>
      <div className="mx-auto max-w-4xl px-6 py-10">
        <h1 className="text-2xl font-semibold">About/Index</h1>
        <p className="text-muted-foreground mt-2 text-sm">Served by {url} with bridge props:</p>
        <Card className="mt-6">
          <CardContent>
            <pre className="text-muted-foreground font-mono text-xs break-all whitespace-pre-wrap">
              {JSON.stringify(props, null, 2)}
            </pre>
          </CardContent>
        </Card>
      </div>
    </main>
  );
}

// Persistent-layout opt-in — the bridge reads this static on the component.
AboutIndex.layout = AppLayout;
