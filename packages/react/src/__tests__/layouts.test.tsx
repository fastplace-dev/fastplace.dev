import "@testing-library/jest-dom/vitest";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi, beforeEach, afterEach } from "vitest";
import React, { useState } from "react";
import {
  FastplaceProvider,
  applyLayouts,
  createFastplaceApp,
  createPageResolver,
  router,
  usePage,
} from "../index";

/* ------------------------------------------------------------------ *
 * Helpers
 * ------------------------------------------------------------------ */

const Page = () => <h1>Page content</h1>;

const Outer = ({ children }: { children?: React.ReactNode }) => (
  <div data-testid="outer">
    <div data-testid="outer-inner">{children}</div>
  </div>
);

const Inner = ({ children }: { children?: React.ReactNode }) => (
  <div data-testid="inner">{children}</div>
);

function renderTree(node: React.ReactNode) {
  return render(
    <FastplaceProvider
      initialPage={{
        component: "X",
        props: {},
        url: "/",
        version: "v1",
      }}
    >
      {node}
    </FastplaceProvider>,
  );
}

// The page store is module-global — reset it and the DOM between every
// test, or a previous provider's page leaks into the next mount.
beforeEach(() => {
  cleanup();
  router.reset();
});

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  window.history.replaceState(null, "", "/");
  router.reset();
  document.body.innerHTML = "";
});

/* ------------------------------------------------------------------ *
 * applyLayouts — pure wrapper
 * ------------------------------------------------------------------ */

describe("applyLayouts", () => {
  it("returns the page unchanged when no layout is declared", () => {
    const { container } = renderTree(applyLayouts(<Page />, undefined));
    expect(container.querySelector("h1")).toBeInTheDocument();
    expect(container.querySelector('[data-testid="outer"]')).not.toBeInTheDocument();
  });

  it("wraps the page in a single layout component", () => {
    const { container } = renderTree(applyLayouts(<Page />, Outer));
    expect(
      container.querySelector('[data-testid="outer"] > [data-testid="outer-inner"] h1'),
    ).toBeInTheDocument();
  });

  it("applies a layout array outside-in (first = outermost)", () => {
    const { container } = renderTree(applyLayouts(<Page />, [Outer, Inner]));
    expect(
      container.querySelector('[data-testid="outer"] [data-testid="inner"] h1'),
    ).toBeInTheDocument();
  });

  it("renders the page bare for an unrecognized layout value", () => {
    const { container } = renderTree(applyLayouts(<Page />, "not-a-layout"));
    expect(container.querySelector("h1")).toBeInTheDocument();
    expect(container.querySelector('[data-testid="outer"]')).not.toBeInTheDocument();
  });
});

/* ------------------------------------------------------------------ *
 * createFastplaceApp — pages opt in via a `layout` static
 * ------------------------------------------------------------------ */

describe("persistent layouts in the bridge app", () => {
  // A layout with local state — persistence means this state survives a
  // page swap (the layout never unmounts during bridge navigation).
  const StatefulLayout = ({ children }: { children?: React.ReactNode }) => {
    const [count, setCount] = useState(0);
    const { component } = usePage();
    return (
      <div data-testid="app-layout">
        <header>
          <span data-testid="layout-page">{component}</span>
          <button onClick={() => setCount(count + 1)}>bump {count}</button>
        </header>
        <main>{children}</main>
      </div>
    );
  };

  const LaidOutPage = () => {
    const { props } = usePage();
    return <h1>Laid: {(props as any).tag}</h1>;
  };
  (LaidOutPage as any).layout = StatefulLayout;

  const OtherLaidOutPage = () => <h2>Other page</h2>;
  (OtherLaidOutPage as any).layout = StatefulLayout;

  function mount(initialComponent = "Laid/Index") {
    const resolve = createPageResolver({
      "/x/resources/js/pages/Laid/Index.jsx": { default: LaidOutPage },
      "/x/resources/js/pages/Other/Index.jsx": { default: OtherLaidOutPage },
      "/x/resources/js/pages/Bare/Index.jsx": { default: Page },
    });
    const el = document.createElement("div");
    el.id = "fastplace";
    el.dataset.page = JSON.stringify({
      component: initialComponent,
      props: { tag: "first" },
      url: "/laid",
      version: "v1",
    });
    document.body.appendChild(el);
    return createFastplaceApp({ resolve });
  }

  it("renders a page through its layout static", async () => {
    await mount();
    expect(await screen.findByText("Laid: first")).toBeInTheDocument();
    expect(screen.getByTestId("app-layout")).toBeInTheDocument();
  });

  it("keeps the layout mounted (state intact) across a page swap", async () => {
    await mount();
    await userEvent.click(await screen.findByText("bump 0"));
    await waitFor(() => expect(screen.getByText("bump 1")).toBeInTheDocument());

    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue({
        ok: true,
        headers: new Headers({ "content-type": "application/json" }),
        redirected: false,
        json: () =>
          Promise.resolve({
            component: "Other/Index",
            props: {},
            url: "/other",
            version: "v1",
          }),
      } as unknown as Response),
    );

    await router.visit("/other");
    await waitFor(() => expect(screen.getByText("Other page")).toBeInTheDocument());
    // The layout — and its counter — survived the navigation.
    expect(screen.getByText("bump 1")).toBeInTheDocument();
    expect(screen.getByTestId("layout-page")).toHaveTextContent("Other/Index");
  });

  it("renders pages without a layout static bare", async () => {
    await mount("Bare/Index");
    await waitFor(() => expect(screen.getByText("Page content")).toBeInTheDocument());
    expect(screen.queryByTestId("app-layout")).not.toBeInTheDocument();
  });
});
