/**
 * @fastplace/react — the server-driven SPA bridge (Inertia pattern).
 *
 * Initial load: the Python backend ships an HTML document containing
 * `#fastplace[data-page='{...}']`. Subsequent navigation sends
 * `X-Fastplace-Request: true` and receives only the page payload
 * (component name + props), which this package swaps in without a reload.
 */

import React, {
  createContext,
  useCallback,
  useContext,
  useMemo,
  useSyncExternalStore,
} from "react";

/* ------------------------------------------------------------------ *
 * Types
 * ------------------------------------------------------------------ */

export type PageProps = Record<string, unknown>;

/** Inertia-compatible page payload. */
export interface Page<TProps extends PageProps = PageProps> {
  component: string;
  props: TProps;
  url: string;
  version?: string;
}

export interface VisitOptions {
  method?: "GET" | "POST" | "PUT" | "PATCH" | "DELETE";
  data?: unknown;
  headers?: Record<string, string>;
  /** Replace the current history entry instead of pushing. */
  replace?: boolean;
  /** Keep the current page state (component/props) — only update the URL. */
  preserveState?: boolean;
}

type Listener = () => void;

/** How setPage() touches browser history for this page transition. */
type HistoryMode = "push" | "replace" | "none";

/* ------------------------------------------------------------------ *
 * Page store — a tiny external store so router.visit (outside React)
 * and popstate both re-render every subscribed component.
 * ------------------------------------------------------------------ */

let currentPage: Page | null = null;
const listeners = new Set<Listener>();

function emit(): void {
  listeners.forEach((listener) => listener());
}

function subscribe(listener: Listener): () => void {
  listeners.add(listener);
  return () => listeners.delete(listener);
}

function getSnapshot(): Page {
  if (currentPage === null) {
    throw new Error(
      "@fastplace/react: no page initialized — render <FastplaceProvider initialPage={...}> first.",
    );
  }
  return currentPage;
}

function setPage(page: Page, opts: { history?: HistoryMode } = {}): void {
  currentPage = page;
  const mode = opts.history ?? "push";
  if (mode !== "none" && typeof window !== "undefined") {
    if (page.url && window.location.pathname + window.location.search !== page.url) {
      const state = { __fastplacePage: page };
      if (mode === "replace") {
        window.history.replaceState(state, "", page.url);
      } else {
        window.history.pushState(state, "", page.url);
      }
    }
  }
  emit();
}

/* ------------------------------------------------------------------ *
 * Bridge protocol
 * ------------------------------------------------------------------ */

export const BRIDGE_HEADER = "X-Fastplace-Request";

function isExternal(href: string): boolean {
  if (typeof window === "undefined") return false;
  try {
    const url = new URL(href, window.location.href);
    return url.origin !== window.location.origin;
  } catch {
    return false;
  }
}

/** A JSON body is a bridge page payload only when it has the page shape. */
function isBridgePage(page: unknown): page is Page {
  return (
    page != null &&
    typeof (page as Page).component === "string" &&
    (page as Page).props != null &&
    typeof (page as Page).url === "string"
  );
}

/** Serialize plain-object GET data into the request query string. */
function withQueryString(url: string, data: unknown): string {
  if (data == null || typeof data !== "object" || Array.isArray(data)) return url;
  const params = new URLSearchParams();
  for (const [key, value] of Object.entries(data as Record<string, unknown>)) {
    if (value != null) params.append(key, String(value));
  }
  const query = params.toString();
  if (!query) return url;
  return url + (url.includes("?") ? "&" : "?") + query;
}

async function visit(url: string, options: VisitOptions = {}): Promise<void> {
  const method = options.method ?? "GET";
  const hasBody = options.data != null && method !== "GET";
  const requestUrl = method === "GET" ? withQueryString(url, options.data) : url;

  const response = await fetch(requestUrl, {
    method,
    headers: {
      [BRIDGE_HEADER]: "true",
      Accept: "application/json",
      ...(hasBody ? { "Content-Type": "application/json" } : {}),
      ...(options.headers ?? {}),
    },
    body: hasBody ? JSON.stringify(options.data) : undefined,
  });

  const contentType = response.headers.get("content-type") ?? "";
  const fallback = () => window.location.assign(response.redirected ? response.url : requestUrl);

  if (!contentType.includes("application/json")) {
    // Non-bridge response (redirect after login, direct hit, error page) —
    // fall back to a full browser navigation.
    fallback();
    return;
  }

  const payload = await response.json().catch(() => null);
  // Error JSON (404/422/500) and plain API payloads are not pages — never
  // swap them into the store, or the resolver would throw mid-render and
  // unmount the whole app.
  if (!response.ok || !isBridgePage(payload)) {
    fallback();
    return;
  }

  const history: HistoryMode = options.replace ? "replace" : "push";
  if (options.preserveState && currentPage) {
    // Keep the current component and props; adopt the new URL/version only.
    setPage({ ...currentPage, url: payload.url, version: payload.version }, { history });
  } else {
    setPage(payload, { history });
  }
}

function handlePopState(event: PopStateEvent): void {
  const state = event.state as { __fastplacePage?: Page } | null;
  if (state?.__fastplacePage) {
    // The browser already moved the URL — do not touch history again.
    setPage(state.__fastplacePage, { history: "none" });
  } else if (currentPage) {
    const target = window.location.pathname + window.location.search;
    // Hash-only movement (fragment anchors) never changes the page URL —
    // nothing to fetch.
    if (target !== currentPage.url) {
      void visit(target, { replace: true });
    }
  }
}

if (typeof window !== "undefined") {
  window.addEventListener("popstate", handlePopState);
}

export const router = {
  /** Navigate through the bridge: `router.visit('/projects')`. */
  visit,
  /** Current page (null before the first initialization). */
  get page(): Page | null {
    return currentPage;
  },
  /** Reset store state — useful in tests. */
  reset(): void {
    currentPage = null;
    listeners.clear();
  },
};

/* ------------------------------------------------------------------ *
 * React bindings
 * ------------------------------------------------------------------ */

const PageContext = createContext<Page | null>(null);

export interface FastplaceProviderProps {
  initialPage: Page;
  children: React.ReactNode;
}

export function FastplaceProvider({ initialPage, children }: FastplaceProviderProps) {
  const init = useMemo(() => {
    if (currentPage === null) {
      currentPage = initialPage; // no history push — the browser owns this entry
      emit();
    }
    return currentPage;
  }, [initialPage]);
  void init;

  const page = useSyncExternalStore(subscribe, getSnapshot, () => initialPage);
  return <PageContext.Provider value={page}>{children}</PageContext.Provider>;
}

export function usePage<TProps extends PageProps = PageProps>(): Page<TProps> {
  const page = useContext(PageContext);
  if (page === null) {
    throw new Error("@fastplace/react: usePage() must be used inside <FastplaceProvider>.");
  }
  return page as Page<TProps>;
}

/* ------------------------------------------------------------------ *
 * Link
 * ------------------------------------------------------------------ */

export interface LinkProps extends React.AnchorHTMLAttributes<HTMLAnchorElement> {
  /** Destination path; bridge-navigated when internal. */
  href: string;
  /** HTTP method for the bridge request (form links). */
  method?: VisitOptions["method"];
  data?: unknown;
  replace?: boolean;
  preserveState?: boolean;
  as?: "a";
}

export function Link({
  href,
  method = "GET",
  data,
  replace = false,
  preserveState = false,
  onClick,
  ...rest
}: LinkProps) {
  const handleClick = useCallback(
    (event: React.MouseEvent<HTMLAnchorElement>) => {
      onClick?.(event);
      if (event.defaultPrevented) return;
      // Fragment-only anchors (#notes) keep native jump-to-anchor behavior.
      if (href.startsWith("#")) return;
      if (event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;
      if (event.button !== 0) return;
      const target = (event.currentTarget as HTMLAnchorElement).target;
      if (target && target !== "_self") return;
      if (isExternal(href)) return; // let the browser navigate

      event.preventDefault();
      void visit(href, { method, data, replace, preserveState });
    },
    [href, method, data, replace, preserveState, onClick],
  );

  return <a href={href} onClick={handleClick} data-fastplace-link="" {...rest} />;
}

/* ------------------------------------------------------------------ *
 * Page resolution
 * ------------------------------------------------------------------ */

/**
 * Build a page resolver from an `import.meta.glob` result. Point the glob at
 * the pages directory, e.g. (note: pattern shown escaped for JSDoc) a glob
 * matching "pages" followed by any nesting and a .jsx/.tsx extension:
 *
 * ```js
 * const resolvePage = createPageResolver(import.meta.glob(pattern));
 * ```
 *
 * Both eager (`{ eager: true }`, modules with a `.default`) and lazy
 * (default export loader functions, wrapped in React.lazy) glob results are
 * supported. Lazy globs keep every page out of the initial bundle — pages
 * load on first render inside the bridge's Suspense boundary.
 */
export type PageModule = { default: React.ComponentType<any> };
export type LazyPageModule = () => Promise<PageModule>;
export type PageResolver = (name: string) => React.ComponentType<any>;

export function createPageResolver(
  modules: Record<string, PageModule | LazyPageModule>,
): PageResolver {
  const registry = new Map<string, React.ComponentType<any>>();
  for (const [path, module] of Object.entries(modules)) {
    const marker = path.lastIndexOf("/pages/");
    if (marker === -1) continue;
    const name = path.slice(marker + "/pages/".length).replace(/\.(jsx?|tsx?)$/, "");
    if (typeof module === "function") {
      // Lazy (non-eager) glob entry — load the page on first render.
      const loader = module as LazyPageModule;
      registry.set(
        name,
        React.lazy(() => loader().then((m) => ({ default: m.default }))),
      );
    } else {
      registry.set(name, (module as PageModule).default);
    }
  }
  return function resolve(name: string) {
    const component = registry.get(name);
    if (!component) {
      throw new Error(
        `@fastplace/react: page component "${name}" not found under resources/js/pages/.`,
      );
    }
    return component;
  };
}

/* ------------------------------------------------------------------ *
 * App bootstrap
 * ------------------------------------------------------------------ */

export interface FastplaceAppOptions {
  /** Resolve a page component by its payload name. */
  resolve: PageResolver;
  /** Mount hook — defaults to createRoot(...).render(...). */
  setup?: (options: { el: HTMLElement; render: () => void }) => void;
  /** Root element selector (default: #fastplace). */
  selector?: string;
}

/** Read the initial page payload the backend embedded in the HTML shell. */
export function readInitialPage(): Page | null {
  if (typeof document === "undefined") return null;
  const el = document.querySelector<HTMLElement>("#fastplace");
  const raw = el?.dataset.page;
  if (!raw) return null;
  try {
    return JSON.parse(raw) as Page;
  } catch {
    return null;
  }
}

interface BoundaryState {
  error: Error | null;
}

/** Keeps a resolver/render failure from unmounting the whole React root. */
class BridgeErrorBoundary extends React.Component<{ children: React.ReactNode }, BoundaryState> {
  state: BoundaryState = { error: null };

  static getDerivedStateFromError(error: Error): BoundaryState {
    return { error };
  }

  componentDidCatch(error: Error): void {
    // Surface the failure in the console; the boundary keeps the app alive.
    console.error("[fastplace] page render failed:", error);
  }

  render(): React.ReactNode {
    if (this.state.error) {
      return (
        <div data-fastplace-error="" style={{ padding: "2rem" }}>
          Fastplace failed to render this page — check the browser console.
        </div>
      );
    }
    return this.props.children;
  }
}

export async function createFastplaceApp(options: FastplaceAppOptions) {
  const page = readInitialPage();
  if (!page) {
    throw new Error("@fastplace/react: no #fastplace[data-page] element found in the document.");
  }
  const el = document.querySelector<HTMLElement>(options.selector ?? "#fastplace");
  if (!el) throw new Error("@fastplace/react: mount element not found.");

  // Resolves the component from the live page store on every render, so a
  // bridge navigation (router.visit / popstate) swaps the page component.
  const BridgeApp = () => {
    const current = usePage();
    const PageComponent = options.resolve(current.component);
    return (
      <React.Suspense fallback={null}>
        <PageComponent />
      </React.Suspense>
    );
  };

  const render = async () => {
    const { createRoot } = await import("react-dom/client");
    createRoot(el).render(
      <FastplaceProvider initialPage={page}>
        <BridgeErrorBoundary>
          <BridgeApp />
        </BridgeErrorBoundary>
      </FastplaceProvider>,
    );
  };
  await (options.setup ?? (async ({ render: r }) => r()))({ el, render });
  return { page, el };
}
