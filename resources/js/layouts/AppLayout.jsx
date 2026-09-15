import React from "react";
import { Link, usePage } from "@fastplace/react";

const NAV = [
  { href: "/dashboard", label: "Dashboard" },
  { href: "/projects", label: "Projects" },
  { href: "/knowledge", label: "Knowledge" },
  { href: "/assistant", label: "Assistant" },
  { href: "/about", label: "About" },
];

/**
 * The persistent application chrome — nav header + content slot.
 * Pages opt in via a `layout = AppLayout` static; the bridge keeps this
 * component mounted across navigation, so its state survives page swaps.
 */
export default function AppLayout({ children }) {
  const { url } = usePage();
  const current = url.split("?")[0];
  const active = (href) => current === href || current.startsWith(`${href}/`);

  return (
    <div className="min-h-dvh bg-surface text-ink">
      <header className="bg-surface-raised/80 sticky top-0 z-40 border-b border-line backdrop-blur">
        <div className="mx-auto flex max-w-4xl items-center justify-between px-6 py-3">
          <Link href="/dashboard" className="text-lg font-semibold tracking-tight">
            Fastplace
          </Link>
          <nav aria-label="Primary" className="flex gap-1 text-sm">
            {NAV.map(({ href, label }) => (
              <Link
                key={href}
                href={href}
                aria-current={active(href) ? "page" : undefined}
                className={
                  active(href)
                    ? "bg-accent/10 text-accent rounded-lg px-3 py-1.5 font-medium"
                    : "text-ink-muted hover:bg-surface hover:text-ink rounded-lg px-3 py-1.5 transition-colors"
                }
              >
                {label}
              </Link>
            ))}
          </nav>
        </div>
      </header>
      {children}
    </div>
  );
}
