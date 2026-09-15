import { clsx, type ClassValue } from "clsx";
import { twMerge } from "tailwind-merge";

/**
 * Merge conditional class names and dedupe conflicting Tailwind utilities.
 * Caller-provided classes always win over earlier (variant) classes.
 */
export function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs));
}

/**
 * Normalize a link target that arrives either as a plain href string or
 * wrapped in a `{ url }` object, so callers always hand a string to Link.
 */
export function toUrl(url: string | { url: string }): string {
  return typeof url === "string" ? url : url.url;
}
