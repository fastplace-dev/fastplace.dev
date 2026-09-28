import { cn } from "@/lib/utils";

/**
 * Keyboard-first bypass around the app chrome (WCAG 2.4.1 / a11y1-G2).
 *
 * Visually hidden until focused, then a solid brand chip pinned top-left.
 * The target landmark should carry `tabIndex={-1}` so focus actually moves
 * in every browser.
 */
export function SkipToContent({
  targetId = "main-content",
  className,
}: {
  targetId?: string;
  className?: string;
}) {
  return (
    <a
      href={`#${targetId}`}
      className={cn(
        "sr-only focus:not-sr-only focus:absolute focus:left-4 focus:top-4 focus:z-50 focus:bg-primary focus:px-4 focus:py-2.5 focus:text-primary-foreground focus:text-sm focus:font-medium focus:shadow-lg focus:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2",
        className,
      )}
    >
      Skip to content
    </a>
  );
}
