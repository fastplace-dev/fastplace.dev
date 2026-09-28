import { useId, type HTMLAttributes } from "react";

import { cn } from "@/lib/utils";

/**
 * Field-level validation message.
 *
 * Renders as an announced `role="alert"` (a11y1-G1) with a stable id so the
 * owning input can reference it through `aria-describedby`; pass an explicit
 * `id` when the pairing is written by hand (`<field>-error` convention).
 */
export default function InputError({
  message,
  className = "",
  id,
  ...props
}: HTMLAttributes<HTMLParagraphElement> & { message?: string }) {
  const generatedId = useId();

  return message ? (
    <p
      id={id ?? generatedId}
      role="alert"
      {...props}
      className={cn("text-sm text-danger", className)}
    >
      {message}
    </p>
  ) : null;
}
