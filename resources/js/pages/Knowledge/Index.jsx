import React, { useEffect, useState } from "react";
import { usePage, router } from "@fastplace/react";
import { Button } from "../../components/ui/button";
import { Card, CardContent } from "../../components/ui/card";
import { Input } from "../../components/ui/input";
import AppLayout from "@/layouts/app-layout";

export default function KnowledgeIndex() {
  const { props } = usePage();
  const q = props.q ?? "";
  const items = props.items ?? [];
  const [query, setQuery] = useState(q);

  // A bridge navigation can re-render this same component with a new q
  // (search → nav link to /knowledge) — follow the server payload instead
  // of keeping the stale local text.
  useEffect(() => {
    setQuery(q);
  }, [q]);

  // GET search — progressive-enhancement friendly (works without JS).
  const handleSubmit = (event) => {
    event.preventDefault();
    router.visit("/knowledge", { data: query ? { q: query } : {}, replace: true });
  };

  return (
    <div>
      <div className="mx-auto max-w-4xl px-6 py-10">
        <header className="mb-6">
          <h1 className="text-2xl font-semibold">Knowledge base</h1>
          <p className="text-muted-foreground mt-2 text-sm">
            Ingested documents, searched in the database — vector similarity on PostgreSQL,
            substring fallback elsewhere.
          </p>
        </header>

        <form onSubmit={handleSubmit} className="mb-6 flex gap-2" role="search">
          <Input
            type="search"
            name="q"
            value={query}
            onChange={(event) => setQuery(event.target.value)}
            placeholder="Search the knowledge base…"
            aria-label="Search the knowledge base"
            className="flex-1"
          />
          <Button type="submit">Search</Button>
        </form>

        {items.length === 0 ? (
          <p className="text-muted-foreground">
            {q
              ? `No matches for “${q}”.`
              : "No knowledge items yet — ingest some via /api/v1/knowledge."}
          </p>
        ) : (
          <Card>
            <CardContent>
              <ul className="divide-y divide-line" aria-label="Knowledge items">
                {items.map((item) => (
                  <li key={item.id} className="py-3">
                    {item.title}
                  </li>
                ))}
              </ul>
            </CardContent>
          </Card>
        )}
      </div>
    </div>
  );
}

// Persistent-layout opt-in — the bridge reads this static on the component.
KnowledgeIndex.layout = AppLayout;
