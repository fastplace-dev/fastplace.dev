import React, { useEffect, useState } from "react";
import { usePage, router } from "@fastplace/react";
import AppLayout from "../../layouts/AppLayout";

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
    <main className="mx-auto max-w-4xl px-6 py-10">
      <header className="mb-6">
        <h1 className="text-2xl font-semibold">Knowledge base</h1>
        <p className="text-ink-muted text-sm">
          Ingested documents, searched in the database — vector similarity on PostgreSQL, substring
          fallback elsewhere.
        </p>
      </header>

      <form onSubmit={handleSubmit} className="mb-6 flex gap-2" role="search">
        <input
          type="search"
          name="q"
          value={query}
          onChange={(event) => setQuery(event.target.value)}
          placeholder="Search the knowledge base…"
          aria-label="Search the knowledge base"
          className="bg-surface-raised border border-line flex-1 rounded-lg px-3 py-2 focus:outline-none focus:ring-2 focus:ring-accent"
        />
        <button
          type="submit"
          className="bg-brand-600 hover:bg-brand-700 rounded-lg px-4 py-2 font-medium text-white"
        >
          Search
        </button>
      </form>

      {items.length === 0 ? (
        <p className="text-ink-muted">
          {q
            ? `No matches for “${q}”.`
            : "No knowledge items yet — ingest some via /api/v1/knowledge."}
        </p>
      ) : (
        <ul
          className="divide-y divide-line bg-surface-raised border border-line rounded-xl"
          aria-label="Knowledge items"
        >
          {items.map((item) => (
            <li key={item.id} className="px-4 py-3">
              {item.title}
            </li>
          ))}
        </ul>
      )}
    </main>
  );
}

// Persistent-layout opt-in — the bridge reads this static on the component.
KnowledgeIndex.layout = AppLayout;
