import "@testing-library/jest-dom/vitest";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi, beforeEach, afterEach } from "vitest";
import React from "react";
import {
  FastplaceProvider,
  Link,
  createFastplaceApp,
  createPageResolver,
  router,
  usePage,
} from "../index";

/* ------------------------------------------------------------------ *
 * Helpers
 * ------------------------------------------------------------------ */

const DashboardPage = () => {
  const { props } = usePage();
  return <h1>Dashboard: {(props as any).user}</h1>;
};

const ProjectsPage = () => {
  const { props, url } = usePage();
  return (
    <div>
      <h2 data-testid="projects-url">{url}</h2>
      <ul>
        {(props as any).projects.map((p: string) => (
          <li key={p}>{p}</li>
        ))}
      </ul>
    </div>
  );
};

const pages = {
  "Dashboard/Index": DashboardPage,
  "Projects/Index": ProjectsPage,
};

function renderApp(initialPage: any) {
  const App = () => {
    const { component } = usePage();
    const Page = pages[component as keyof typeof pages];
    return (
      <div>
        <Link href="/projects">Projects</Link>
        {Page ? <Page /> : null}
      </div>
    );
  };
  return render(
    <FastplaceProvider initialPage={initialPage}>
      <App />
    </FastplaceProvider>,
  );
}

function mockBridgeResponse(payload: any) {
  return {
    ok: true,
    headers: new Headers({ "content-type": "application/json" }),
    redirected: false,
    json: () => Promise.resolve(payload),
  } as unknown as Response;
}

/* ------------------------------------------------------------------ *
 * Tests
 * ------------------------------------------------------------------ */

beforeEach(() => {
  cleanup();
});

afterEach(() => {
  cleanup();
});

describe("FastplaceProvider + usePage", () => {
  it("exposes the server payload (component, props, url, version)", () => {
    renderApp({
      component: "Dashboard/Index",
      props: { user: "Firoz" },
      url: "/dashboard",
      version: "v1",
    });
    expect(screen.getByText("Dashboard: Firoz")).toBeInTheDocument();
  });
});

describe("Link", () => {
  beforeEach(() => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        mockBridgeResponse({
          component: "Projects/Index",
          props: { projects: ["Alpha", "Beta"] },
          url: "/projects",
          version: "v1",
        }),
      ),
    );
  });

  afterEach(() => {
    vi.unstubAllGlobals();
    window.history.replaceState(null, "", "/");
    router.reset();
  });

  it("sends a bridge request and swaps the page without reload", async () => {
    renderApp({
      component: "Dashboard/Index",
      props: { user: "Firoz" },
      url: "/dashboard",
      version: "v1",
    });

    await userEvent.click(screen.getByText("Projects"));

    await waitFor(() =>
      expect(screen.queryByRole("heading", { name: "Dashboard: Firoz" })).not.toBeInTheDocument(),
    );
    expect(await screen.findByText("Alpha")).toBeInTheDocument();
    expect(screen.getByTestId("projects-url")).toHaveTextContent("/projects");

    const call = (fetch as ReturnType<typeof vi.fn>).mock.calls[0];
    expect(call[0]).toBe("/projects");
    expect(call[1].headers["X-Fastplace-Request"]).toBe("true");
    expect(call[1].headers["Accept"]).toBe("application/json");
  });

  it("does not bridge-navigate external URLs (browser handles them)", async () => {
    render(
      <FastplaceProvider
        initialPage={{ component: "Dashboard/Index", props: {}, url: "/", version: "v1" }}
      >
        <Link href="https://example.com/x">External</Link>
      </FastplaceProvider>,
    );

    await userEvent.click(screen.getByText("External"));
    // External href → no bridge request issued.
    expect(fetch).not.toHaveBeenCalled();
  });
});

describe("router.visit", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
    window.history.replaceState(null, "", "/");
    router.reset();
  });

  it("pushes history state for GET visits", async () => {
    let VisitUrl = "";
    const Probe = () => {
      const { url } = usePage();
      VisitUrl = url;
      return null;
    };
    render(
      <FastplaceProvider
        initialPage={{ component: "Dashboard/Index", props: {}, url: "/", version: "v1" }}
      >
        <Probe />
      </FastplaceProvider>,
    );

    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        mockBridgeResponse({
          component: "Projects/Index",
          props: { projects: [] },
          url: "/projects",
          version: "v1",
        }),
      ),
    );

    await router.visit("/projects");
    await waitFor(() => expect(VisitUrl).toBe("/projects"));
    expect(window.location.pathname).toBe("/projects");
  });
});

describe("createPageResolver", () => {
  it("resolves declared page components by name", () => {
    const fakeModules: Record<string, any> = {
      "/abs/path/resources/js/pages/Dashboard/Index.jsx": { default: DashboardPage },
      "/abs/path/resources/js/pages/Projects/Index.jsx": { default: ProjectsPage },
    };
    const resolver = createPageResolver(fakeModules);
    expect(resolver("Dashboard/Index")).toBe(DashboardPage);
    expect(resolver("Projects/Index")).toBe(ProjectsPage);
    expect(() => resolver("Missing/Page")).toThrow(/Missing\/Page/);
  });

  it("never registers colocated test files as pages", () => {
    // An eager glob over pages/** sweeps __tests__ neighbors in unless the
    // resolver defends itself — a registered test module would execute its
    // vitest imports in the browser.
    const TestPage = () => <div>test</div>;
    const fakeModules: Record<string, any> = {
      "/abs/path/resources/js/pages/Auth/Login.tsx": { default: DashboardPage },
      "/abs/path/resources/js/pages/__tests__/Login.test.tsx": { default: TestPage },
      "/abs/path/resources/js/pages/__tests__/helper.spec.js": { default: TestPage },
    };
    const resolver = createPageResolver(fakeModules);
    expect(resolver("Auth/Login")).toBe(DashboardPage);
    expect(() => resolver("__tests__/Login.test")).toThrow(/__tests__\/Login\.test/);
    expect(() => resolver("__tests__/helper.spec")).toThrow(/__tests__\/helper\.spec/);
  });
});

/* ------------------------------------------------------------------ *
 * Phase 3 review fixes — payload validation, replace/preserveState,
 * query serialization, hash links, lazy pages, error boundary.
 * ------------------------------------------------------------------ */

/** Replace window.location with a controllable stub; returns a restore fn. */
function stubLocation() {
  const assign = vi.fn();
  const original = window.location;
  Object.defineProperty(window, "location", {
    configurable: true,
    value: {
      href: "http://localhost/dashboard",
      origin: "http://localhost",
      host: "localhost",
      protocol: "http:",
      pathname: "/dashboard",
      search: "",
      hash: "",
      assign,
      replace: vi.fn(),
    },
  });
  return {
    assign,
    restore: () =>
      Object.defineProperty(window, "location", { configurable: true, value: original }),
  };
}

function jsonResponse(payload: any, ok = true, status = 200) {
  return {
    ok,
    status,
    headers: new Headers({ "content-type": "application/json" }),
    redirected: false,
    json: () => Promise.resolve(payload),
  } as unknown as Response;
}

describe("visit payload validation", () => {
  let restore: () => void;
  let assign: ReturnType<typeof vi.fn>;

  beforeEach(() => {
    ({ assign, restore } = stubLocation());
  });

  afterEach(() => {
    restore();
    vi.unstubAllGlobals();
    window.history.replaceState(null, "", "/");
    router.reset();
  });

  it("falls back to a full page load for non-bridge JSON payloads", async () => {
    render(
      <FastplaceProvider
        initialPage={{
          component: "Dashboard/Index",
          props: { user: "Firoz" },
          url: "/dashboard",
          version: "v1",
        }}
      >
        <div />,
      </FastplaceProvider>,
    );
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(jsonResponse({ status: "ok" })));

    await router.visit("/api/v1/health");

    // A plain API JSON body is not a bridge page — never swap it in.
    expect(router.page?.component).toBe("Dashboard/Index");
    expect(assign).toHaveBeenCalledWith("/api/v1/health");
  });

  it("falls back to a full page load for error JSON responses (404)", async () => {
    render(
      <FastplaceProvider
        initialPage={{ component: "Dashboard/Index", props: {}, url: "/dashboard", version: "v1" }}
      >
        <div />,
      </FastplaceProvider>,
    );
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(jsonResponse({ message: "Not Found" }, false, 404)),
    );

    await router.visit("/definitely-not-here");

    expect(router.page?.component).toBe("Dashboard/Index");
    expect(assign).toHaveBeenCalledWith("/definitely-not-here");
  });

  it("still swaps valid bridge payloads on ok responses", async () => {
    render(
      <FastplaceProvider
        initialPage={{ component: "Dashboard/Index", props: {}, url: "/dashboard", version: "v1" }}
      >
        <div />,
      </FastplaceProvider>,
    );
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        jsonResponse({
          component: "Projects/Index",
          props: { projects: ["Alpha"] },
          url: "/projects",
          version: "v1",
        }),
      ),
    );

    await router.visit("/projects");
    expect(router.page?.component).toBe("Projects/Index");
    expect(assign).not.toHaveBeenCalled();
  });
});

describe("CSRF protection", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
    window.history.replaceState(null, "", "/");
    router.reset();
    document.head.querySelectorAll("meta[name='csrf-token']").forEach((m) => m.remove());
  });

  it("sends the CSRF token header on unsafe-method visits", async () => {
    render(
      <FastplaceProvider
        initialPage={{ component: "Dashboard/Index", props: {}, url: "/dashboard", version: "v1" }}
      >
        <div />,
      </FastplaceProvider>,
    );
    const meta = document.createElement("meta");
    meta.name = "csrf-token";
    meta.content = "session-csrf-token-value";
    document.head.appendChild(meta);

    const fetchMock = vi.fn().mockResolvedValue(
      mockBridgeResponse({
        component: "Dashboard/Index",
        props: {},
        url: "/dashboard",
        version: "v1",
      }),
    );
    vi.stubGlobal("fetch", fetchMock);

    await router.visit("/logout", { method: "POST" });

    expect(fetchMock.mock.calls[0][1].headers["X-Fastplace-CSRF-Token"]).toBe(
      "session-csrf-token-value",
    );
  });

  it("omits the CSRF header on GET visits and without a meta tag", async () => {
    render(
      <FastplaceProvider
        initialPage={{ component: "Dashboard/Index", props: {}, url: "/dashboard", version: "v1" }}
      >
        <div />,
      </FastplaceProvider>,
    );
    const fetchMock = vi.fn().mockResolvedValue(
      mockBridgeResponse({
        component: "Dashboard/Index",
        props: {},
        url: "/search",
        version: "v1",
      }),
    );
    vi.stubGlobal("fetch", fetchMock);

    await router.visit("/search"); // GET — no CSRF header
    expect(fetchMock.mock.calls[0][1].headers["X-Fastplace-CSRF-Token"]).toBeUndefined();

    await router.visit("/logout", { method: "POST" }); // no meta tag present
    expect(fetchMock.mock.calls[1][1].headers["X-Fastplace-CSRF-Token"]).toBeUndefined();
  });

  it("adopts the rotated token from the X-Fastplace-CSRF-Token response header", async () => {
    render(
      <FastplaceProvider
        initialPage={{ component: "Dashboard/Index", props: {}, url: "/dashboard", version: "v1" }}
      >
        <div />,
      </FastplaceProvider>,
    );
    const meta = document.createElement("meta");
    meta.name = "csrf-token";
    meta.content = "stale-token";
    document.head.appendChild(meta);

    const page = (url: string) => ({
      component: "Dashboard/Index",
      props: {},
      url,
      version: "v1",
    });
    const respondWith = (token: string) =>
      ({
        ok: true,
        headers: new Headers({
          "content-type": "application/json",
          "X-Fastplace-CSRF-Token": token,
        }),
        redirected: false,
        json: () => Promise.resolve(page("/dashboard")),
      }) as unknown as Response;

    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(respondWith("rotated-by-login")) // login rotates the token
      .mockResolvedValueOnce(respondWith("rotated-again"));
    vi.stubGlobal("fetch", fetchMock);

    await router.visit("/login", { method: "POST" });
    expect(meta.content).toBe("rotated-by-login");
    await router.visit("/logout", { method: "POST" });

    expect(fetchMock.mock.calls[1][1].headers["X-Fastplace-CSRF-Token"]).toBe("rotated-by-login");
    expect(meta.content).toBe("rotated-again");
  });

  it("writes props.csrf_token into the meta tag on a swapped page", async () => {
    render(
      <FastplaceProvider
        initialPage={{ component: "Dashboard/Index", props: {}, url: "/dashboard", version: "v1" }}
      >
        <div />,
      </FastplaceProvider>,
    );
    const meta = document.createElement("meta");
    meta.name = "csrf-token";
    meta.content = "stale-token";
    document.head.appendChild(meta);

    const fetchMock = vi.fn().mockResolvedValue(
      mockBridgeResponse({
        component: "Dashboard/Index",
        props: { csrf_token: "fresh-from-props" },
        url: "/dashboard",
        version: "v1",
      }),
    );
    vi.stubGlobal("fetch", fetchMock);

    await router.visit("/dashboard");

    expect(meta.content).toBe("fresh-from-props");
    await router.visit("/logout", { method: "POST" });
    expect(fetchMock.mock.calls[1][1].headers["X-Fastplace-CSRF-Token"]).toBe("fresh-from-props");
  });

  it("adopts the advertised token even from an error response, and creates a missing meta tag", async () => {
    render(
      <FastplaceProvider
        initialPage={{ component: "Dashboard/Index", props: {}, url: "/dashboard", version: "v1" }}
      >
        <div />,
      </FastplaceProvider>,
    );
    // No meta tag at all — the shell rendered before any session existed.
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce({
        ok: false,
        status: 419,
        headers: new Headers({
          "content-type": "application/json",
          "X-Fastplace-CSRF-Token": "recovered-token",
        }),
        redirected: false,
        json: () => Promise.resolve({ message: "CSRF token mismatch." }),
      } as unknown as Response)
      .mockResolvedValueOnce(
        mockBridgeResponse({
          component: "Dashboard/Index",
          props: {},
          url: "/dashboard",
          version: "v1",
        }),
      );
    vi.stubGlobal("fetch", fetchMock);

    await router.visit("/logout", { method: "POST" });

    const meta = document.querySelector<HTMLMetaElement>("meta[name='csrf-token']");
    expect(meta?.content).toBe("recovered-token");
    await router.visit("/logout", { method: "POST" });
    expect(fetchMock.mock.calls[1][1].headers["X-Fastplace-CSRF-Token"]).toBe("recovered-token");
  });
});

describe("history semantics", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
    window.history.replaceState(null, "", "/");
    router.reset();
  });

  it("replace:true replaces the history entry and updates the URL", async () => {
    const replaceSpy = vi.spyOn(window.history, "replaceState");
    const pushSpy = vi.spyOn(window.history, "pushState");
    render(
      <FastplaceProvider
        initialPage={{ component: "Dashboard/Index", props: {}, url: "/dashboard", version: "v1" }}
      >
        <div />,
      </FastplaceProvider>,
    );
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        mockBridgeResponse({
          component: "Projects/Index",
          props: { projects: [] },
          url: "/projects",
          version: "v1",
        }),
      ),
    );

    await router.visit("/projects", { replace: true });

    expect(router.page?.url).toBe("/projects");
    expect(window.location.pathname).toBe("/projects");
    expect(replaceSpy).toHaveBeenCalled();
    expect(pushSpy).not.toHaveBeenCalled();
    replaceSpy.mockRestore();
    pushSpy.mockRestore();
  });

  it("preserveState keeps the current component and props, updating only the url", async () => {
    renderApp({
      component: "Dashboard/Index",
      props: { user: "Firoz" },
      url: "/dashboard",
      version: "v1",
    });
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        mockBridgeResponse({
          component: "Projects/Index",
          props: { projects: ["New"] },
          url: "/projects",
          version: "v1",
        }),
      ),
    );

    await router.visit("/projects", { preserveState: true });

    await waitFor(() => expect(router.page?.url).toBe("/projects"));
    // Same component + props as before the visit — only the URL moved.
    expect(router.page?.component).toBe("Dashboard/Index");
    expect(router.page?.props).toEqual({ user: "Firoz" });
    expect(screen.getByText("Dashboard: Firoz")).toBeInTheDocument();
  });

  it("serializes GET visit data into the query string", async () => {
    render(
      <FastplaceProvider
        initialPage={{ component: "Dashboard/Index", props: {}, url: "/dashboard", version: "v1" }}
      >
        <div />,
      </FastplaceProvider>,
    );
    const fetchMock = vi.fn().mockResolvedValue(
      mockBridgeResponse({
        component: "Dashboard/Index",
        props: {},
        url: "/search?q=fastplace",
        version: "v1",
      }),
    );
    vi.stubGlobal("fetch", fetchMock);

    await router.visit("/search", { data: { q: "fastplace", page: 2 } });

    expect(fetchMock.mock.calls[0][0]).toBe("/search?q=fastplace&page=2");
  });
});

describe("hash links", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
    window.history.replaceState(null, "", "/");
    router.reset();
  });

  it("lets the browser handle fragment-only anchors natively", async () => {
    // Sync location with the initial payload — as after a real initial load.
    window.history.replaceState(null, "", "/dashboard");
    render(
      <FastplaceProvider
        initialPage={{ component: "Dashboard/Index", props: {}, url: "/dashboard", version: "v1" }}
      >
        <Link href="#notes">Notes</Link>
      </FastplaceProvider>,
    );
    const fetchMock = vi.fn();
    vi.stubGlobal("fetch", fetchMock);

    await userEvent.click(screen.getByText("Notes"));

    // Native anchor jump — no bridge request, page state untouched.
    expect(fetchMock).not.toHaveBeenCalled();
    expect(window.location.hash).toBe("#notes");
  });
});

describe("lazy page resolution", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
    window.history.replaceState(null, "", "/");
    router.reset();
    document.body.innerHTML = "";
  });

  it("resolves lazy import() glob entries through React.lazy", async () => {
    const LazyPage = () => {
      const { props } = usePage();
      return <h1>Lazy: {(props as any).tag}</h1>;
    };
    const lazyModules = {
      "/x/resources/js/pages/Lazy/Index.jsx": () => Promise.resolve({ default: LazyPage }),
    };
    const resolver = createPageResolver(lazyModules as unknown as Record<string, any>);

    const el = document.createElement("div");
    el.id = "fastplace";
    el.dataset.page = JSON.stringify({
      component: "Lazy/Index",
      props: { tag: "works" },
      url: "/lazy",
      version: "v1",
    });
    document.body.appendChild(el);

    await createFastplaceApp({ resolve: resolver });
    await waitFor(() => expect(screen.getByText("Lazy: works")).toBeInTheDocument());
  });
});

describe("bootstrap error boundary", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
    window.history.replaceState(null, "", "/");
    router.reset();
    document.body.innerHTML = "";
  });

  it("renders a fallback instead of unmounting when resolution fails", async () => {
    const boom = () => {
      throw new Error("page component missing");
    };
    const el = document.createElement("div");
    el.id = "fastplace";
    el.dataset.page = JSON.stringify({
      component: "Missing/Index",
      props: {},
      url: "/missing",
      version: "v1",
    });
    document.body.appendChild(el);

    await createFastplaceApp({ resolve: boom as any });
    await waitFor(() =>
      expect(screen.getByText(/failed to render this page/i)).toBeInTheDocument(),
    );
  });
});

/* ------------------------------------------------------------------ *
 * createFastplaceApp — the real bootstrap swaps components on visit
 * ------------------------------------------------------------------ */

describe("createFastplaceApp withApp", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
    window.history.replaceState(null, "", "/");
    router.reset();
    document.body.innerHTML = "";
  });

  it("wraps the page tree inside the provider so app chrome can use the page context", async () => {
    // Mount a shell that reads page props — only possible when withApp
    // renders above the page but below FastplaceProvider.
    const Shell = ({ children }: { children?: React.ReactNode }) => {
      const { props } = usePage();
      return (
        <div data-testid="shell">
          <span>user:{(props as any).user}</span>
          {children}
        </div>
      );
    };

    const el = document.createElement("div");
    el.id = "fastplace";
    el.dataset.page = JSON.stringify({
      component: "Dashboard/Index",
      props: { user: "Firoz" },
      url: "/dashboard",
      version: "v1",
    });
    document.body.appendChild(el);

    const resolve = createPageResolver({
      "/x/resources/js/pages/Dashboard/Index.jsx": { default: DashboardPage },
    });

    await createFastplaceApp({ resolve, withApp: (app) => <Shell>{app}</Shell> });

    await waitFor(() => expect(screen.getByTestId("shell")).toBeInTheDocument());
    expect(screen.getByText("user:Firoz")).toBeInTheDocument();
    expect(screen.getByText("Dashboard: Firoz")).toBeInTheDocument();
    // The shell wraps the router content — shell first, page inside it.
    expect(screen.getByTestId("shell")).toContainElement(screen.getByText("Dashboard: Firoz"));
  });

  it("keeps the unwrapped tree when no withApp is given", async () => {
    const el = document.createElement("div");
    el.id = "fastplace";
    el.dataset.page = JSON.stringify({
      component: "Dashboard/Index",
      props: { user: "Ana" },
      url: "/dashboard",
      version: "v1",
    });
    document.body.appendChild(el);

    const resolve = createPageResolver({
      "/x/resources/js/pages/Dashboard/Index.jsx": { default: DashboardPage },
    });

    await createFastplaceApp({ resolve });
    await waitFor(() => expect(screen.getByText("Dashboard: Ana")).toBeInTheDocument());
  });
});

describe("createFastplaceApp", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
    window.history.replaceState(null, "", "/");
    router.reset();
    document.body.innerHTML = "";
  });

  it("remounts the resolved page component after a bridge navigation", async () => {
    const AboutPage = () => {
      const { props } = usePage();
      return <h1>About: {(props as any).framework}</h1>;
    };
    const pages = { "Dashboard/Index": DashboardPage, "About/Index": AboutPage };
    const resolve = createPageResolver({
      "/x/resources/js/pages/Dashboard/Index.jsx": { default: DashboardPage },
      "/x/resources/js/pages/About/Index.jsx": { default: AboutPage },
    });
    void pages;

    // Server shell: container + embedded initial payload.
    const el = document.createElement("div");
    el.id = "fastplace";
    el.dataset.page = JSON.stringify({
      component: "Dashboard/Index",
      props: { user: "Firoz" },
      url: "/dashboard",
      version: "v1",
    });
    document.body.appendChild(el);

    await createFastplaceApp({ resolve });
    await waitFor(() => expect(screen.getByText("Dashboard: Firoz")).toBeInTheDocument());

    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        mockBridgeResponse({
          component: "About/Index",
          props: { framework: "fastplace" },
          url: "/about",
          version: "v1",
        }),
      ),
    );

    await router.visit("/about");
    await waitFor(() => expect(screen.getByText("About: fastplace")).toBeInTheDocument());
    expect(screen.queryByText("Dashboard: Firoz")).not.toBeInTheDocument();
  });
});
