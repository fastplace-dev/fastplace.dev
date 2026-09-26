import "@testing-library/jest-dom/vitest";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import React from "react";
import {
  FastplaceProvider,
  Link,
  NavigationProgressBar,
  router,
  useNavigationProgress,
} from "../index";

/* ------------------------------------------------------------------ *
 * Helpers
 * ------------------------------------------------------------------ */

const initialPage = {
  component: "Dashboard/Index",
  props: { user: "Firoz" },
  url: "/dashboard",
  version: "v1",
};

const bridgePage = (url: string) => ({
  component: "Projects/Index",
  props: { projects: ["Alpha"] },
  url,
  version: "v1",
});

function jsonResponse(payload: any, ok = true, status = 200) {
  return {
    ok,
    status,
    headers: new Headers({ "content-type": "application/json" }),
    redirected: false,
    json: () => Promise.resolve(payload),
  } as unknown as Response;
}

/** A fetch answer the test holds back until the bar state is asserted. */
function deferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (reason?: unknown) => void;
  const promise = new Promise<T>((res, rej) => {
    resolve = res;
    reject = rej;
  });
  return { promise, resolve, reject };
}

function ProgressProbe() {
  const state = useNavigationProgress();
  return <span data-testid="progress-state">{state}</span>;
}

/** The bridge needs ~200ms before hiding a finished bar; sleep past it. */
const settle = () => new Promise((resolve) => setTimeout(resolve, 300));

beforeEach(() => {
  cleanup();
});

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  window.history.replaceState(null, "", "/");
  router.reset();
  document.getElementById("fastplace-progress-styles")?.remove();
});

/* ------------------------------------------------------------------ *
 * Progress lifecycle over the visit flow
 * ------------------------------------------------------------------ */

describe("navigation progress lifecycle", () => {
  it("starts on a router.visit, completes on the payload swap, then hides", async () => {
    const pending = deferred<Response>();
    vi.stubGlobal("fetch", vi.fn().mockReturnValue(pending.promise));

    render(
      <FastplaceProvider initialPage={initialPage}>
        <ProgressProbe />
      </FastplaceProvider>,
    );

    const visitPromise = router.visit("/projects");
    await waitFor(() =>
      expect(screen.getByTestId("progress-state")).toHaveTextContent("started"),
    );

    pending.resolve(jsonResponse(bridgePage("/projects")));
    await visitPromise;
    expect(screen.getByTestId("progress-state")).toHaveTextContent("completed");

    await settle();
    expect(screen.getByTestId("progress-state")).toHaveTextContent("idle");
  });

  it("starts on a Link click navigation", async () => {
    const pending = deferred<Response>();
    vi.stubGlobal("fetch", vi.fn().mockReturnValue(pending.promise));

    render(
      <FastplaceProvider initialPage={initialPage}>
        <Link href="/projects">Projects</Link>
        <ProgressProbe />
      </FastplaceProvider>,
    );

    await userEvent.click(screen.getByText("Projects"));
    expect(screen.getByTestId("progress-state")).toHaveTextContent("started");

    pending.resolve(jsonResponse(bridgePage("/projects")));
    await waitFor(() =>
      expect(screen.getByTestId("progress-state")).toHaveTextContent("completed"),
    );
  });

  it("hides when the visit fails at the network level", async () => {
    const pending = deferred<Response>();
    vi.stubGlobal("fetch", vi.fn().mockReturnValue(pending.promise));

    render(
      <FastplaceProvider initialPage={initialPage}>
        <ProgressProbe />
      </FastplaceProvider>,
    );

    const visitPromise = router.visit("/projects").catch(() => undefined);
    await waitFor(() =>
      expect(screen.getByTestId("progress-state")).toHaveTextContent("started"),
    );

    pending.reject(new TypeError("Failed to fetch"));
    await visitPromise;
    expect(screen.getByTestId("progress-state")).toHaveTextContent("idle");
  });

  it("hides on an error answer handled by onError instead of completing", async () => {
    const pending = deferred<Response>();
    vi.stubGlobal("fetch", vi.fn().mockReturnValue(pending.promise));

    render(
      <FastplaceProvider initialPage={initialPage}>
        <ProgressProbe />
      </FastplaceProvider>,
    );

    const visitPromise = router.visit("/missing", { onError: () => undefined });
    await waitFor(() =>
      expect(screen.getByTestId("progress-state")).toHaveTextContent("started"),
    );

    pending.resolve(jsonResponse({ message: "Not Found" }, false, 404));
    await visitPromise;
    expect(screen.getByTestId("progress-state")).toHaveTextContent("idle");
  });

  it("hides on the full-page fallback (non-bridge answer)", async () => {
    const pending = deferred<Response>();
    vi.stubGlobal("fetch", vi.fn().mockReturnValue(pending.promise));

    render(
      <FastplaceProvider initialPage={initialPage}>
        <ProgressProbe />
      </FastplaceProvider>,
    );

    const visitPromise = router.visit("/download");
    await waitFor(() =>
      expect(screen.getByTestId("progress-state")).toHaveTextContent("started"),
    );

    // HTML answer — the bridge hands navigation to the browser.
    pending.resolve({
      ok: true,
      status: 200,
      headers: new Headers({ "content-type": "text/html" }),
      redirected: false,
    } as unknown as Response);
    await visitPromise;
    expect(screen.getByTestId("progress-state")).toHaveTextContent("idle");
  });
});

/* ------------------------------------------------------------------ *
 * <NavigationProgressBar /> — the optional indicator component
 * ------------------------------------------------------------------ */

describe("NavigationProgressBar", () => {
  it("renders nothing while idle and shows the bar during a visit", async () => {
    const pending = deferred<Response>();
    vi.stubGlobal("fetch", vi.fn().mockReturnValue(pending.promise));

    render(
      <FastplaceProvider initialPage={initialPage}>
        <NavigationProgressBar />
      </FastplaceProvider>,
    );
    expect(document.querySelector("[data-fastplace-progress]")).toBeNull();

    const visitPromise = router.visit("/projects");
    await waitFor(() =>
      expect(document.querySelector("[data-fastplace-progress]")).toHaveAttribute(
        "data-fastplace-progress",
        "started",
      ),
    );

    pending.resolve(jsonResponse(bridgePage("/projects")));
    await visitPromise;
    expect(document.querySelector("[data-fastplace-progress]")).toHaveAttribute(
      "data-fastplace-progress",
      "completed",
    );

    await settle();
    expect(document.querySelector("[data-fastplace-progress]")).toBeNull();
  });

  it("injects its stylesheet once, with reduced-motion and theme-token support", () => {
    render(
      <FastplaceProvider initialPage={initialPage}>
        <NavigationProgressBar />
        <NavigationProgressBar />
      </FastplaceProvider>,
    );

    const styles = document.querySelectorAll("#fastplace-progress-styles");
    expect(styles).toHaveLength(1);
    const text = styles[0].textContent ?? "";
    // Instant show/hide — no animation — under prefers-reduced-motion.
    expect(text).toContain("prefers-reduced-motion");
    // Neutral theming: inherits text color, overridable via the custom property.
    expect(text).toContain("--fastplace-progress-color");
  });
});
