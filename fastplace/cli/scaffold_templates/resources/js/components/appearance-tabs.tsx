import type { LucideIcon } from "lucide-react";
import { Monitor, Moon, Sun } from "lucide-react";
import type * as React from "react";

import type { Appearance } from "@/hooks/use-appearance";
import { useAppearance } from "@/hooks/use-appearance";
import { cn } from "@/lib/utils";

export default function AppearanceTabs({ className, ...props }: React.ComponentProps<"div">) {
  const { appearance, updateAppearance } = useAppearance();

  const tabs: { value: Appearance; icon: LucideIcon; label: string }[] = [
    { value: "light", icon: Sun, label: "Light" },
    { value: "dark", icon: Moon, label: "Dark" },
    { value: "system", icon: Monitor, label: "System" },
  ];

  return (
    <div
      role="radiogroup"
      aria-label="Appearance"
      data-slot="appearance-tabs"
      className={cn(
        "inline-flex gap-1 rounded-lg border border-line bg-surface-raised p-1",
        className,
      )}
      {...props}
    >
      {tabs.map(({ value, icon: Icon, label }) => (
        <button
          key={value}
          type="button"
          role="radio"
          aria-checked={appearance === value}
          onClick={() => updateAppearance(value)}
          className={cn(
            "flex items-center rounded-md px-3.5 py-1.5 transition-colors",
            appearance === value
              ? "bg-surface text-ink shadow-xs"
              : "text-ink-muted hover:bg-surface hover:text-ink",
          )}
        >
          <Icon className="-ml-1 h-4 w-4" />
          <span className="ml-1.5 text-sm">{label}</span>
        </button>
      ))}
    </div>
  );
}
