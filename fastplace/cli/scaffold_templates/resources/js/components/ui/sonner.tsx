import * as React from "react";
import { Toaster as Sonner } from "sonner";

import { useAppearance } from "@/hooks/use-appearance";
import { useFlashToast } from "@/hooks/use-flash-toast";

function Toaster({ ...props }: React.ComponentProps<typeof Sonner>) {
  // Sonner themes itself from the resolved value — "system" is decided above,
  // in the appearance store, so the toaster never guesses on its own.
  const { resolvedAppearance } = useAppearance();

  useFlashToast();

  return (
    <Sonner
      data-slot="toaster"
      theme={resolvedAppearance}
      className="toaster group"
      position="bottom-right"
      style={
        {
          "--normal-bg": "var(--popover)",
          "--normal-text": "var(--popover-foreground)",
          "--normal-border": "var(--border)",
        } as React.CSSProperties
      }
      {...props}
    />
  );
}

export { Toaster };
