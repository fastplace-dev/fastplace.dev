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
  /** Called with the parsed error envelope on a JSON error answer; suppresses the full-page fallback. */
  onError?: (failure: { message?: string; status: number }) => void;
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

function setPage(page: Page, opts: { history?: HistoryMode } = {}): void {
  // Swapping the page component resets the title to the browser default
  // before the next render, so a page without <Head> never inherits the
  // previous page's title. (Full fix: carry the title in the bridge payload.)
  const componentSwapped = currentPage?.component !== page.component;
  currentPage = page;
  // Every bridge payload carries the session CSRF token (render() embeds
  // it for the no-JS hidden inputs) — keep the meta tag in step with it.
  const token = (page.props as Record<string, unknown> | undefined)?.csrf_token;
  if (typeof token === "string") writeCsrfToken(token);
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
  if (componentSwapped && typeof document !== "undefined") document.title = "";
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

/** The CSRF token render() embeds as <meta name="csrf-token">, if any. */
function csrfToken(): string | null {
  if (typeof document === "undefined") return null;
  const meta = document.querySelector<HTMLMetaElement>("meta[name='csrf-token']");
  return meta?.content || null;
}

/** Write a fresh token into the meta tag (created when the shell had none). */
function writeCsrfToken(value: string): void {
  if (typeof document === "undefined") return;
  let meta = document.querySelector<HTMLMetaElement>("meta[name='csrf-token']");
  if (!meta) {
    meta = document.createElement("meta");
    meta.name = "csrf-token";
    document.head.appendChild(meta);
  }
  meta.content = value;
}

/**
 * Adopt the token the server advertises after rotating it across an auth
 * privilege boundary (login/logout/2FA) — otherwise every later unsafe
 * bridge request would echo the stale token and die with a 419.
 */
function adoptCsrfToken(response: Response): void {
  const fresh = response.headers.get("X-Fastplace-CSRF-Token");
  if (fresh) writeCsrfToken(fresh);
}

async function visit(url: string, options: VisitOptions = {}): Promise<void> {
  const method = options.method ?? "GET";
  const hasBody = options.data != null && method !== "GET";
  const requestUrl = method === "GET" ? withQueryString(url, options.data) : url;
  const token = method === "GET" ? null : csrfToken();

  startProgress();

  let response: Response;
  try {
    response = await fetch(requestUrl, {
      method,
      headers: {
        [BRIDGE_HEADER]: "true",
        Accept: "application/json",
        ...(token ? { "X-Fastplace-CSRF-Token": token } : {}),
        ...(hasBody ? { "Content-Type": "application/json" } : {}),
        ...(options.headers ?? {}),
      },
      body: hasBody ? JSON.stringify(options.data) : undefined,
    });
  } catch (error) {
    hideProgress();
    throw error;
  }

  adoptCsrfToken(response);

  const contentType = response.headers.get("content-type") ?? "";
  const fallback = () => window.location.assign(response.redirected ? response.url : requestUrl);

  if (!contentType.includes("application/json")) {
    // Non-bridge response (redirect after login, direct hit, error page) —
    // fall back to a full browser navigation.
    hideProgress();
    fallback();
    return;
  }

  const payload = await response.json().catch(() => null);
  // Error JSON (404/422/500) and plain API payloads are not pages — never
  // swap them into the store, or the resolver would throw mid-render and
  // unmount the whole app.
  if (!response.ok || !isBridgePage(payload)) {
    hideProgress();
    if (options.onError) {
      // The caller owns the failure — surface the parsed envelope instead
      // of taking the full-page fallback.
      const body = payload as { message?: unknown } | null;
      options.onError({
        status: response.status,
        ...(typeof body?.message === "string" ? { message: body.message } : {}),
      });
      return;
    }
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
  finishProgress();
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
    resetProgress();
  },
};

/* ------------------------------------------------------------------ *
 * Navigation progress — a tiny store over the visit lifecycle.
 *
 * The bar shows while a bridge visit is in flight, completes when the
 * payload swaps in, and hides on any failure (network error, error
 * answer, full-page fallback). Concurrent visits are counted so a
 * late-finishing earlier visit cannot hide the bar early.
 * ------------------------------------------------------------------ */

export type NavigationProgressState = "idle" | "started" | "completed";

let progressState: NavigationProgressState = "idle";
const progressListeners = new Set<Listener>();
let activeVisits = 0;
let progressResetTimer: ReturnType<typeof setTimeout> | null = null;

/** How long a finished bar stays visible before hiding. */
const PROGRESS_RESET_MS = 200;

function emitProgress(): void {
  progressListeners.forEach((listener) => listener());
}

function setProgressState(state: NavigationProgressState): void {
  if (progressState === state) return;
  progressState = state;
  emitProgress();
}

function clearProgressResetTimer(): void {
  if (progressResetTimer === null) return;
  clearTimeout(progressResetTimer);
  progressResetTimer = null;
}

function startProgress(): void {
  activeVisits += 1;
  clearProgressResetTimer();
  setProgressState("started");
}

function finishProgress(): void {
  activeVisits = Math.max(0, activeVisits - 1);
  if (activeVisits > 0) return;
  setProgressState("completed");
  // Let the finished bar paint briefly, then hide it.
  progressResetTimer = setTimeout(() => {
    progressResetTimer = null;
    setProgressState("idle");
  }, PROGRESS_RESET_MS);
}

function hideProgress(): void {
  activeVisits = Math.max(0, activeVisits - 1);
  if (activeVisits > 0) return;
  clearProgressResetTimer();
  setProgressState("idle");
}

function resetProgress(): void {
  activeVisits = 0;
  clearProgressResetTimer();
  setProgressState("idle");
}

function subscribeProgress(listener: Listener): () => void {
  progressListeners.add(listener);
  return () => progressListeners.delete(listener);
}

function getProgressSnapshot(): NavigationProgressState {
  return progressState;
}

/**
 * Subscribe to the bridge's navigation progress: "idle" (hidden),
 * "started" (a visit is in flight) or "completed" (the payload swapped).
 */
export function useNavigationProgress(): NavigationProgressState {
  return useSyncExternalStore(subscribeProgress, getProgressSnapshot, () => "idle");
}

const PROGRESS_STYLE_ID = "fastplace-progress-styles";

/**
 * Neutral theming: the bar inherits the app's text color (readable in both
 * light and dark themes); override with --fastplace-progress-color.
 * prefers-reduced-motion strips the animation — the bar shows and hides
 * instantly instead.
 */
const PROGRESS_STYLES = `
[data-fastplace-progress] {
  position: fixed;
  top: 0;
  left: 0;
  right: 0;
  height: 3px;
  z-index: 2147483000;
  pointer-events: none;
}
[data-fastplace-progress] > span {
  display: block;
  height: 100%;
  background: var(--fastplace-progress-color, currentColor);
  transform-origin: 0 50%;
}
[data-fastplace-progress='started'] > span {
  animation: fastplace-progress-indeterminate 6s ease-out forwards;
}
[data-fastplace-progress='completed'] > span {
  transform: scaleX(1);
  transition: transform 150ms ease-out;
}
@keyframes fastplace-progress-indeterminate {
  0% { transform: scaleX(0); }
  30% { transform: scaleX(0.5); }
  60% { transform: scaleX(0.8); }
  100% { transform: scaleX(0.95); }
}
@media (prefers-reduced-motion: reduce) {
  [data-fastplace-progress] > span {
    animation: none !important;
    transition: none !important;
  }
}
`;

function injectProgressStyles(): void {
  if (typeof document === "undefined") return;
  if (document.getElementById(PROGRESS_STYLE_ID)) return;
  const style = document.createElement("style");
  style.id = PROGRESS_STYLE_ID;
  style.textContent = PROGRESS_STYLES;
  document.head.appendChild(style);
}

/**
 * Optional top-of-page progress bar driven by the bridge's visit lifecycle.
 * Render it once anywhere inside <FastplaceProvider>; it mounts nothing
 * while no visit is in flight.
 */
export function NavigationProgressBar(): React.ReactNode {
  const state = useNavigationProgress();
  React.useEffect(injectProgressStyles, []);
  if (state === "idle") return null;
  return (
    <div data-fastplace-progress={state} aria-hidden="true">
      <span />
    </div>
  );
}

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

  const page = useSyncExternalStore(
    subscribe,
    // reset() (the test seam) empties the store; a pending update scheduled
    // by an earlier emit() can then flush and re-render this provider
    // against the emptied store. Fall back to the page it booted with —
    // a mid-render throw here surfaced as an unhandled crash in test runs.
    () => currentPage ?? initialPage,
    () => initialPage,
  );
  return <PageContext.Provider value={page}>{children}</PageContext.Provider>;
}

export function usePage<TProps extends PageProps = PageProps>(): Page<TProps> {
  const page = useContext(PageContext);
  if (page === null) {
    throw new Error("@fastplace/react: usePage() must be used inside <FastplaceProvider>.");
  }
  return page as Page<TProps>;
}

export { useCan, type CanMap } from "./useCan";
export {
  useBroadcast,
  usePresence,
  type BroadcastMessage,
  type BroadcastStatus,
  type PresenceMember,
  type UseBroadcastOptions,
} from "./useBroadcast";
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
    // Colocated tests are not routable pages — a glob over pages/** sweeps
    // __tests__ neighbors in, and registering one would execute its vitest
    // imports in the browser.
    if (/(?:^|\/)__tests__\/|\.(?:test|spec)\.[^.]+$/.test(path.slice(marker))) continue;
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
 * Layouts — pages opt in via a `layout` static
 * ------------------------------------------------------------------ */

/** A layout component renders its page content through `children`. */
export type PageLayoutComponent = React.ComponentType<{ children?: React.ReactNode }>;

/** A page's declared layout: a component, or an array (first = outermost). */
export type PageLayout = PageLayoutComponent | PageLayoutComponent[];

/**
 * Wrap a rendered page in its declared layout(s).
 *
 * A page component opts in with a static `layout` — a component rendering
 * `children`, or an array of them with the first entry outermost. Because
 * the layout sits *above* the page component in the tree with a stable
 * identity, it stays mounted across bridge navigations: its local state
 * survives while the page content swaps (the Inertia persistent-layout
 * behavior — navigation no longer resets scroll positions, audio players,
 * or open menus living in the chrome).
 */
export function applyLayouts(page: React.ReactNode, layout: unknown): React.ReactNode {
  if (layout == null) return page;
  const declared = Array.isArray(layout) ? layout : [layout];
  const components = declared.filter(
    (entry): entry is PageLayoutComponent => typeof entry === "function",
  );
  return components.reduceRight(
    (children, Component) => <Component>{children}</Component>,
    page as React.ReactNode,
  );
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
  /**
   * Wrap the page tree in app-level chrome (providers, toasts). Called inside
   * FastplaceProvider, so the chrome may read the page context via usePage().
   */
  withApp?: (app: React.ReactNode) => React.ReactNode;
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
  // A page's `layout` static wraps it from above — the layout identity is
  // stable, so it stays mounted (state intact) across page swaps.
  const BridgeApp = () => {
    const current = usePage();
    const PageComponent = options.resolve(current.component);
    return (
      <React.Suspense fallback={null}>
        {applyLayouts(<PageComponent />, (PageComponent as { layout?: PageLayout }).layout)}
      </React.Suspense>
    );
  };

  const render = async () => {
    const { createRoot } = await import("react-dom/client");
    const app = (
      <BridgeErrorBoundary>
        <BridgeApp />
      </BridgeErrorBoundary>
    );
    createRoot(el).render(
      <FastplaceProvider initialPage={page}>
        {options.withApp ? options.withApp(app) : app}
      </FastplaceProvider>,
    );
  };
  await (options.setup ?? (async ({ render: r }) => r()))({ el, render });
  return { page, el };
}

/* ------------------------------------------------------------------ *
 * Forms — declarative bridge submissions with 422 error mapping
 *
 * The backend's validation contract answers 422 with
 * `{message, errors: {field: [messages]}}`. Form submissions map that onto
 * component state instead of the full-page fallback visit() would take, so
 * the user's input survives a failed save.
 * ------------------------------------------------------------------ */

/** Per-field validation messages from the backend's 422 contract. */
export type FormErrors = Record<string, string[]>;

export interface FormSubmitOptions {
  headers?: Record<string, string>;
  /** Called for a 2xx response (payload is null when the page swapped). */
  onSuccess?: (payload: unknown) => void;
  /** Called with the mapped field errors on a 422 (or `{}` on other failures); the second argument carries the non-422 envelope when there was one. */
  onError?: (errors: FormErrors, failure?: { message?: string; status?: number }) => void;
  /** Always called once the submission settles. */
  onFinish?: () => void;
}

type SubmitOutcome =
  | { kind: "swapped" }
  | { kind: "succeeded"; payload: unknown }
  | { kind: "errored"; errors: FormErrors; message?: string; status?: number }
  | { kind: "navigated" };

function isFieldErrorMap(value: unknown): value is FormErrors {
  if (value == null || typeof value !== "object") return false;
  return Object.values(value).every(
    (messages) => Array.isArray(messages) && messages.every((m) => typeof m === "string"),
  );
}

/** True when the value is a File or an array carrying one (repeated key). */
function carriesFile(value: unknown): boolean {
  if (value instanceof File) return true;
  return Array.isArray(value) && value.some((item) => item instanceof File);
}

/**
 * JSON.stringify corrupts a File to {} — any body carrying one must travel
 * as native multipart instead. Returns the FormData to send, or null when
 * the data is file-free (JSON is fine then).
 */
function toMultipartBody(data: unknown): FormData | null {
  if (data instanceof FormData) return data;
  if (data == null || typeof data !== "object") return null;
  const hasFile = Object.values(data).some(carriesFile);
  if (!hasFile) return null;
  const formData = new FormData();
  for (const [key, value] of Object.entries(data)) {
    if (value == null) continue; // JSON.stringify drops these too
    if (Array.isArray(value)) {
      for (const item of value) formData.append(key, item as string | File);
    } else if (value instanceof Blob) {
      formData.append(key, value);
    } else {
      formData.append(key, typeof value === "object" ? JSON.stringify(value) : String(value));
    }
  }
  return formData;
}

async function submitBridge(
  url: string,
  method: "POST" | "PUT" | "PATCH" | "DELETE",
  data: unknown,
  headers?: Record<string, string>,
): Promise<SubmitOutcome> {
  // A FormData body travels as native multipart (uploads intact); anything
  // else goes as JSON. The multipart Content-Type is left to the browser —
  // it carries the boundary only it knows.
  const multipart = toMultipartBody(data);
  const token = csrfToken();
  let response: Response;
  try {
    response = await fetch(url, {
      method,
      headers: {
        [BRIDGE_HEADER]: "true",
        Accept: "application/json",
        ...(token ? { "X-Fastplace-CSRF-Token": token } : {}),
        ...(multipart ? {} : { "Content-Type": "application/json" }),
        ...(headers ?? {}),
      },
      body: multipart ?? JSON.stringify(data),
    });
  } catch {
    // Network-level failure — nothing to map onto fields.
    return { kind: "errored", errors: {} };
  }

  adoptCsrfToken(response);

  const contentType = response.headers.get("content-type") ?? "";
  if (!contentType.includes("application/json")) {
    // Non-bridge answer (a redirect that landed on HTML, a downloaded file) —
    // hand the navigation to the browser, which follows it natively.
    window.location.assign(response.redirected ? response.url : url);
    return { kind: "navigated" };
  }

  const payload = await response.json().catch(() => null);

  if (response.status === 422 && isFieldErrorMap((payload as { errors?: unknown })?.errors)) {
    return { kind: "errored", errors: (payload as { errors: FormErrors }).errors };
  }
  if (!response.ok) {
    // Non-422 failure — carry the envelope's message and the status up, so
    // pages can show WHY it failed, not just that it failed.
    const body = payload as { message?: unknown } | null;
    return {
      kind: "errored",
      errors: {},
      status: response.status,
      ...(typeof body?.message === "string" ? { message: body.message } : {}),
    };
  }
  if (isBridgePage(payload)) {
    setPage(payload);
    return { kind: "swapped" };
  }
  return { kind: "succeeded", payload };
}

/** FormData entries to a JSON body: repeated keys collect into arrays. */
function serializeForm(formData: FormData): Record<string, unknown> {
  const data: Record<string, unknown> = {};
  for (const [key, value] of formData.entries()) {
    const existing = data[key];
    if (existing === undefined) data[key] = value;
    else if (Array.isArray(existing)) existing.push(value);
    else data[key] = [existing, value];
  }
  return data;
}

/** State handed to a <Form> render-prop child. */
export interface FormRenderState {
  processing: boolean;
  errors: FormErrors;
  /** The server's message from a non-422 failure (401/403/429), when sent. */
  message?: string;
  /** The HTTP status behind the current errors/message. */
  status?: number;
  clearErrors: () => void;
  reset: () => void;
}

export interface FormProps extends Omit<
  React.FormHTMLAttributes<HTMLFormElement>,
  "action" | "method" | "onSubmit" | "onError" | "children"
> {
  /** Where the bridge submission is sent; also the no-JS form action. */
  action: string;
  method?: "post" | "put" | "patch" | "delete";
  /** Field names restored to their initial DOM values after a success. */
  resetOnSuccess?: string[];
  /** Static children, or a render prop receiving the live form state. */
  children: React.ReactNode | ((state: FormRenderState) => React.ReactNode);
  headers?: Record<string, string>;
  onSuccess?: (payload: unknown) => void;
  onError?: (errors: FormErrors, failure?: { message?: string; status?: number }) => void;
  onFinish?: () => void;
}

/**
 * A native `<form>` whose submit is intercepted for the bridge. Without JS
 * it still posts to `action` — with the intended verb carried as a hidden
 * `_method` field for put/patch/delete (the backend's method-override layer
 * translates it before routing). The DOM owns the field values
 * (uncontrolled); on submit the entries are collected from the live form
 * element and sent as JSON — or as native multipart when the form carries
 * a file input, so uploads survive the bridge too.
 */
export function Form({
  action,
  method = "post",
  resetOnSuccess,
  children,
  headers,
  onSuccess,
  onError,
  onFinish,
  ...rest
}: FormProps) {
  const [processing, setProcessing] = React.useState(false);
  const [errors, setErrors] = React.useState<FormErrors>({});
  const [failure, setFailure] = React.useState<{ message?: string; status?: number }>({});
  const formRef = React.useRef<HTMLFormElement | null>(null);
  // Selects have no `defaultValue` IDL to restore from — the initial value
  // lives in React props (client render) or the `selected` attribute (SSR
  // markup). Capture what the DOM actually shows once, right after mount.
  const initialSelectValues = React.useRef<Map<HTMLSelectElement, string[]> | null>(null);
  React.useLayoutEffect(() => {
    if (initialSelectValues.current) return;
    const snapshot = new Map<HTMLSelectElement, string[]>();
    for (const select of formRef.current?.querySelectorAll("select") ?? []) {
      snapshot.set(
        select,
        Array.from(select.selectedOptions).map((option) => option.value),
      );
    }
    initialSelectValues.current = snapshot;
  }, []);

  const clearErrors = React.useCallback(() => {
    setErrors({});
    setFailure({});
  }, []);
  const reset = React.useCallback(() => {
    formRef.current?.reset();
    setErrors({});
    setFailure({});
  }, []);

  /** Restore only the named controls to their initial DOM values. */
  const resetFields = React.useCallback((names: string[]) => {
    const form = formRef.current;
    if (!form) return;
    for (const name of names) {
      const node = form.elements.namedItem(name);
      const controls = node instanceof RadioNodeList ? Array.from(node) : node ? [node] : [];
      for (const control of controls) {
        if (
          control instanceof HTMLInputElement &&
          (control.type === "checkbox" || control.type === "radio")
        ) {
          control.checked = control.defaultChecked;
        } else if (control instanceof HTMLInputElement || control instanceof HTMLTextAreaElement) {
          control.value = control.defaultValue;
        } else if (control instanceof HTMLSelectElement) {
          const remembered = initialSelectValues.current?.get(control);
          if (remembered) {
            // Restore the values the page shipped with — even when no
            // option carries a `selected` attribute (client-rendered).
            for (const option of Array.from(control.options)) {
              option.selected = remembered.includes(option.value);
            }
          } else {
            // Late-mounted select (missed the snapshot): fall back to the
            // default-selected option, then the spec default (option 0).
            let hasDefault = false;
            for (const option of Array.from(control.options)) {
              option.selected = option.defaultSelected;
              if (option.defaultSelected) hasDefault = true;
            }
            if (!hasDefault && !control.multiple && control.options.length > 0) {
              control.selectedIndex = 0;
            }
          }
        }
      }
    }
  }, []);

  const handleSubmit = async (event: React.FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    const form = event.currentTarget;
    // The clicked submit button contributes its name/value entry — matching
    // what a native no-JS submission sends.
    const submitter = (event.nativeEvent as SubmitEvent).submitter;
    const formData = new FormData(form, submitter instanceof HTMLElement ? submitter : undefined);
    const hasAutoToken = form.querySelector('input[data-fastplace-csrf][name="_token"]') !== null;
    const hasAutoMethod =
      form.querySelector('input[data-fastplace-method][name="_method"]') !== null;
    // A file input makes JSON.stringify corrupt the File into {} — submit
    // native multipart instead, exactly like the no-JS post would.
    const multipart = Array.from(formData.values()).some((value) => value instanceof File);
    // The auto token rides the X-Fastplace-CSRF-Token header and the real
    // verb rides the method line, so the no-JS-only fields stay out of the
    // bridge body (native posts still carry them — the inputs remain).
    let body: FormData | Record<string, unknown>;
    if (multipart) {
      if (hasAutoToken) formData.delete("_token");
      if (hasAutoMethod) formData.delete("_method");
      body = formData;
    } else {
      const collected = serializeForm(formData);
      if (hasAutoToken) delete collected._token;
      if (hasAutoMethod) delete collected._method;
      body = collected;
    }
    setProcessing(true);
    try {
      const outcome = await submitBridge(
        action,
        method.toUpperCase() as "POST" | "PUT" | "PATCH" | "DELETE",
        body,
        headers,
      );
      if (outcome.kind === "errored") {
        setErrors(outcome.errors);
        setFailure({ message: outcome.message, status: outcome.status });
        onError?.(outcome.errors, { message: outcome.message, status: outcome.status });
      } else {
        if (resetOnSuccess) resetFields(resetOnSuccess);
        if (outcome.kind === "succeeded") {
          onSuccess?.(outcome.payload);
        } else if (outcome.kind === "swapped") {
          onSuccess?.(null);
        }
      }
    } finally {
      setProcessing(false);
      onFinish?.();
    }
  };

  // The DOM element always posts natively (the only verb HTML knows);
  // the real verb rides on the bridge request.
  const noJsToken = csrfToken();
  return (
    <form
      ref={formRef}
      action={action}
      method="post"
      onSubmit={handleSubmit}
      data-slot="form"
      {...rest}
    >
      {noJsToken ? (
        // Without JavaScript the browser posts this form natively; the
        // session token must ride along as a field. First child, so a
        // developer-rendered duplicate resolves to it (first value wins
        // server-side). Excluded from the bridge body at submit time.
        <input type="hidden" name="_token" value={noJsToken} data-fastplace-csrf />
      ) : null}
      {method !== "post" ? (
        // HTML knows no put/patch/delete — the no-JS post carries the verb
        // here for the backend's method-override layer to translate. The
        // bridge request itself uses the real verb, so this stays out of
        // its body.
        <input type="hidden" name="_method" value={method} data-fastplace-method />
      ) : null}
      {typeof children === "function"
        ? (children as (state: FormRenderState) => React.ReactNode)({
            processing,
            errors,
            message: failure.message,
            status: failure.status,
            clearErrors,
            reset,
          })
        : children}
    </form>
  );
}

export interface UseFormReturn<TData extends Record<string, unknown>> {
  data: TData;
  setData: {
    (updater: (data: TData) => TData): void;
    (key: keyof TData & string, value: unknown): void;
    (patch: Partial<TData>): void;
  };
  processing: boolean;
  errors: FormErrors;
  /** The server's message from a non-422 failure (401/403/429), when sent. */
  message?: string;
  /** The HTTP status behind the current errors/message. */
  status?: number;
  setErrors: (errors: FormErrors) => void;
  clearErrors: () => void;
  /** Restore initial data — all fields, or just the ones named. */
  reset: (...fields: (keyof TData & string)[]) => void;
  /** Rewrite the payload right before it is sent. */
  transform: (transformer: (data: TData) => TData) => void;
  wasSuccessful: boolean;
  /** True for ~2s after a successful submit — for "Saved." indicators. */
  recentlySuccessful: boolean;
  submit: (
    method: "post" | "put" | "patch" | "delete",
    url: string,
    options?: FormSubmitOptions,
  ) => Promise<void>;
  post: (url: string, options?: FormSubmitOptions) => Promise<void>;
  put: (url: string, options?: FormSubmitOptions) => Promise<void>;
  patch: (url: string, options?: FormSubmitOptions) => Promise<void>;
  delete: (url: string, options?: FormSubmitOptions) => Promise<void>;
}

/** Controlled bridge form state for pages that own their field values. */
export function useForm<TData extends Record<string, unknown>>(
  initialData: TData | (() => TData),
): UseFormReturn<TData> {
  const initialRef = React.useRef(initialData);
  const [data, setDataState] = React.useState<TData>(initialData);
  const [processing, setProcessing] = React.useState(false);
  const [errors, setErrorsState] = React.useState<FormErrors>({});
  const [failure, setFailure] = React.useState<{ message?: string; status?: number }>({});
  const [wasSuccessful, setWasSuccessful] = React.useState(false);
  const [recentlySuccessful, setRecentlySuccessful] = React.useState(false);
  const transformRef = React.useRef<(data: TData) => TData>((current) => current);

  const setData = (
    keyOrPatchOrUpdater: ((data: TData) => TData) | (keyof TData & string) | Partial<TData>,
    value?: unknown,
  ): void => {
    if (typeof keyOrPatchOrUpdater === "function") {
      setDataState((current) => keyOrPatchOrUpdater(current));
    } else if (typeof keyOrPatchOrUpdater === "string") {
      setDataState((current) => ({ ...current, [keyOrPatchOrUpdater]: value }));
    } else {
      setDataState((current) => ({ ...current, ...keyOrPatchOrUpdater }));
    }
  };

  const setErrors = React.useCallback((next: FormErrors) => setErrorsState(next), []);
  const clearErrors = React.useCallback(() => {
    setErrorsState({});
    setFailure({});
  }, []);
  const reset = React.useCallback((...fields: (keyof TData & string)[]) => {
    const seed = initialRef.current;
    const base = typeof seed === "function" ? (seed as () => TData)() : seed;
    setDataState((current) => {
      if (fields.length === 0) return base;
      const restored: Record<string, unknown> = {};
      for (const field of fields) restored[field] = base[field];
      return { ...current, ...restored };
    });
  }, []);
  const transform = React.useCallback((transformer: (data: TData) => TData) => {
    transformRef.current = transformer;
  }, []);

  const submit = React.useCallback(
    async (
      method: "post" | "put" | "patch" | "delete",
      url: string,
      options: FormSubmitOptions = {},
    ) => {
      setProcessing(true);
      try {
        const outcome = await submitBridge(
          url,
          method.toUpperCase() as "POST" | "PUT" | "PATCH" | "DELETE",
          transformRef.current(data),
          options.headers,
        );
        if (outcome.kind === "errored") {
          setErrorsState(outcome.errors);
          setFailure({ message: outcome.message, status: outcome.status });
          options.onError?.(outcome.errors, { message: outcome.message, status: outcome.status });
        } else {
          setWasSuccessful(true);
          setRecentlySuccessful(true);
          setTimeout(() => setRecentlySuccessful(false), 2000);
          options.onSuccess?.(outcome.kind === "succeeded" ? outcome.payload : null);
        }
      } finally {
        setProcessing(false);
        options.onFinish?.();
      }
    },
    [data],
  );

  return {
    data,
    setData,
    processing,
    errors,
    message: failure.message,
    status: failure.status,
    setErrors,
    clearErrors,
    reset,
    transform,
    wasSuccessful,
    recentlySuccessful,
    submit,
    post: (url, options) => submit("post", url, options),
    put: (url, options) => submit("put", url, options),
    patch: (url, options) => submit("patch", url, options),
    delete: (url, options) => submit("delete", url, options),
  };
}

/** Set the document title from a page component. */
export function Head({ title }: { title?: string }) {
  React.useEffect(() => {
    if (title !== undefined) document.title = title;
  }, [title]);
  return null;
}
