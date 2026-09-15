import "@testing-library/jest-dom/vitest";
import { cleanup } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";

import { cn, toUrl } from "@/lib/utils";

afterEach(cleanup);

describe("cn", () => {
  it("joins truthy class names and skips falsy values", () => {
    expect(cn("flex", false, undefined, "items-center", null, "")).toBe("flex items-center");
  });

  it("dedupes conflicting tailwind utilities with the last one winning", () => {
    expect(cn("px-2 py-1", "px-4")).toBe("py-1 px-4");
  });

  it("lets a caller-provided className override earlier classes", () => {
    expect(cn("h-9 px-4", "h-10")).toBe("px-4 h-10");
  });
});

describe("toUrl", () => {
  it("returns a plain string url unchanged", () => {
    expect(toUrl("/dashboard")).toBe("/dashboard");
  });

  it("unwraps an object-shaped url to its url field", () => {
    expect(toUrl({ url: "/settings/profile" })).toBe("/settings/profile");
  });
});
