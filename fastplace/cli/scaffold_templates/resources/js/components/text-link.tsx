import type { ComponentProps } from "react";

import { Link } from "@fastplace/react";

import { cn } from "@/lib/utils";

type Props = ComponentProps<typeof Link>;

export default function TextLink({ className = "", children, ...props }: Props) {
  return (
    <Link
      className={cn(
        "text-ink underline decoration-line underline-offset-4 transition-colors duration-300 ease-out hover:decoration-current!",
        className,
      )}
      {...props}
    >
      {children}
    </Link>
  );
}
