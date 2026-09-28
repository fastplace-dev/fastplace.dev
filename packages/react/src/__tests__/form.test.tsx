import "@testing-library/jest-dom/vitest";
import { act, cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import React from "react";
import { FastplaceProvider, Form, Head, router, useForm, usePage } from "../index";

/* ------------------------------------------------------------------ *
 * Helpers
 * ------------------------------------------------------------------ */

function mockJsonResponse(payload: unknown, ok = true, status = 200) {
  return {
    ok,
    status,
    headers: new Headers({ "content-type": "application/json" }),
    redirected: false,
    json: () => Promise.resolve(payload),
  } as unknown as Response;
}

function mockHtmlResponse(url = "/destination") {
  return {
    ok: true,
    status: 200,
    headers: new Headers({ "content-type": "text/html" }),
    redirected: true,
    url,
    json: () => Promise.reject(new Error("not json")),
  } as unknown as Response;
}

/** Replace window.location with a controllable stub; returns a restore fn. */
function stubLocation() {
  const assign = vi.fn();
  const original = window.location;
  Object.defineProperty(window, "location", {
    configurable: true,
    value: { ...original, assign },
  });
  return {
    assign,
    restore() {
      Object.defineProperty(window, "location", { configurable: true, value: original });
    },
  };
}

/** A page that exposes the live page payload so tests can assert swaps. */
const PayloadProbe = () => {
  const { component, props } = usePage();
  return (
    <p data-testid="probe">
      {component}:{JSON.stringify(props)}
    </p>
  );
};

function renderWithProvider(node: React.ReactNode) {
  return render(
    <FastplaceProvider initialPage={{ component: "Dashboard/Index", props: {}, url: "/" }}>
      {node}
      <PayloadProbe />
    </FastplaceProvider>,
  );
}

beforeEach(() => {
  cleanup();
  document.title = "";
});

afterEach(() => {
  cleanup();
  router.reset();
  vi.unstubAllGlobals();
  document.head.querySelectorAll("meta[name='csrf-token']").forEach((m) => m.remove());
});

/* ------------------------------------------------------------------ *
 * <Form>
 * ------------------------------------------------------------------ */

describe("Form", () => {
  it("renders its children inside a form", () => {
    renderWithProvider(
      <Form action="/projects">
        <input type="text" name="name" />
        <button type="submit">Save</button>
      </Form>,
    );
    expect(screen.getByRole("button", { name: "Save" })).toHaveAttribute("type", "submit");
  });

  it("adopts the rotated CSRF token advertised on the submit response", async () => {
    const user = userEvent.setup();
    const meta = document.createElement("meta");
    meta.name = "csrf-token";
    meta.content = "stale-token";
    document.head.appendChild(meta);
    const page = { component: "Projects/Index", props: {}, url: "/projects" };
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce({
        ok: true,
        headers: new Headers({
          "content-type": "application/json",
          "X-Fastplace-CSRF-Token": "rotated-token",
        }),
        redirected: false,
        json: () => Promise.resolve(page),
      } as unknown as Response)
      .mockResolvedValueOnce(mockJsonResponse({ ok: true }));
    vi.stubGlobal("fetch", fetchMock);

    renderWithProvider(
      <Form action="/projects">
        <input type="text" name="name" defaultValue="Apollo" />
        <button type="submit">Save</button>
      </Form>,
    );
    await user.click(screen.getByRole("button", { name: "Save" }));

    expect(fetchMock.mock.calls[0][1].headers["X-Fastplace-CSRF-Token"]).toBe("stale-token");
    expect(meta.content).toBe("rotated-token");

    // A second unsafe action on the swapped page must send the fresh token.
    await router.visit("/logout", { method: "POST" });
    expect(fetchMock.mock.calls[1][1].headers["X-Fastplace-CSRF-Token"]).toBe("rotated-token");
  });

  it("submits the form fields as JSON to the action with bridge headers", async () => {
    const user = userEvent.setup();
    const meta = document.createElement("meta");
    meta.name = "csrf-token";
    meta.content = "tok-123";
    document.head.appendChild(meta);
    vi.stubGlobal(
      "fetch",
      vi
        .fn()
        .mockResolvedValue(
          mockJsonResponse({ component: "Projects/Index", props: {}, url: "/projects" }),
        ),
    );

    renderWithProvider(
      <Form action="/projects">
        {() => (
          <>
            <input type="text" name="name" defaultValue="Apollo" />
            <input type="hidden" name="tags" defaultValue="a" />
            <input type="hidden" name="tags" defaultValue="b" />
            <button type="submit">Save</button>
          </>
        )}
      </Form>,
    );
    await user.click(screen.getByRole("button", { name: "Save" }));

    const [url, init] = (fetch as ReturnType<typeof vi.fn>).mock.calls[0];
    expect(url).toBe("/projects");
    expect(init.method).toBe("POST");
    expect(init.headers["X-Fastplace-Request"]).toBe("true");
    expect(init.headers["X-Fastplace-CSRF-Token"]).toBe("tok-123");
    expect(init.headers["Content-Type"]).toBe("application/json");
    expect(JSON.parse(init.body)).toEqual({ name: "Apollo", tags: ["a", "b"] });
    meta.remove();
  });

  it("feeds 422 field errors to the render prop without navigating", async () => {
    const user = userEvent.setup();
    const { assign, restore } = stubLocation();
    vi.stubGlobal(
      "fetch",
      vi
        .fn()
        .mockResolvedValue(
          mockJsonResponse(
            { message: "The given data was invalid.", errors: { name: ["Required."] } },
            false,
            422,
          ),
        ),
    );

    renderWithProvider(
      <Form action="/projects">
        {({ errors, processing }) => (
          <>
            {errors.name ? <p role="alert">{errors.name[0]}</p> : null}
            <span data-testid="flag">{processing ? "busy" : "idle"}</span>
            <input type="text" name="name" />
            <button type="submit">Save</button>
          </>
        )}
      </Form>,
    );
    await user.click(screen.getByRole("button", { name: "Save" }));

    expect(await screen.findByRole("alert")).toHaveTextContent("Required.");
    await waitFor(() => expect(screen.getByTestId("flag")).toHaveTextContent("idle"));
    expect(assign).not.toHaveBeenCalled();
    restore();
  });

  it("swaps the page when the server answers with a bridge payload", async () => {
    const user = userEvent.setup();
    const onSuccess = vi.fn();
    vi.stubGlobal(
      "fetch",
      vi
        .fn()
        .mockResolvedValue(
          mockJsonResponse({ component: "Projects/Index", props: { saved: 1 }, url: "/projects" }),
        ),
    );

    renderWithProvider(
      <Form action="/projects" onSuccess={onSuccess}>
        {() => (
          <>
            <input type="text" name="name" />
            <button type="submit">Save</button>
          </>
        )}
      </Form>,
    );
    await user.click(screen.getByRole("button", { name: "Save" }));

    await waitFor(() =>
      expect(screen.getByTestId("probe")).toHaveTextContent('Projects/Index:{"saved":1}'),
    );
    expect(onSuccess).toHaveBeenCalled();
  });

  it("falls back to a full navigation for non-bridge responses", async () => {
    const user = userEvent.setup();
    const { assign, restore } = stubLocation();
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(mockHtmlResponse("/after-login")));

    renderWithProvider(
      <Form action="/login">
        {() => (
          <>
            <input type="text" name="email" />
            <button type="submit">Log in</button>
          </>
        )}
      </Form>,
    );
    await user.click(screen.getByRole("button", { name: "Log in" }));

    await waitFor(() => expect(assign).toHaveBeenCalledWith("/after-login"));
    restore();
  });

  it("keeps the form usable without JS: renders a real form element", () => {
    renderWithProvider(
      <Form action="/projects">
        <input type="text" name="name" />
      </Form>,
    );
    const form = document.querySelector("form");
    expect(form).not.toBeNull();
    expect(form).toHaveAttribute("action", "/projects");
  });

  it("resets only the named fields after a successful submit (resetOnSuccess)", async () => {
    const user = userEvent.setup();
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(mockJsonResponse({ ok: true })));

    renderWithProvider(
      <Form action="/login" resetOnSuccess={["password"]}>
        {() => (
          <>
            <input type="text" name="email" defaultValue="a@b.c" />
            <input type="password" name="password" aria-label="Password" />
            <button type="submit">Log in</button>
          </>
        )}
      </Form>,
    );
    await user.type(screen.getByLabelText("Password"), "secret");
    expect(screen.getByLabelText("Password")).toHaveValue("secret");
    await user.click(screen.getByRole("button", { name: "Log in" }));

    const form = document.querySelector("form")!;
    await waitFor(() => {
      expect((form.querySelector("[name=password]") as HTMLInputElement).value).toBe("");
      expect((form.querySelector("[name=email]") as HTMLInputElement).value).toBe("a@b.c");
    });
  });
});

/* ------------------------------------------------------------------ *
 * <Form> no-JS CSRF token
 * ------------------------------------------------------------------ */

describe("Form no-JS CSRF token", () => {
  function appendMeta(content: string) {
    const meta = document.createElement("meta");
    meta.name = "csrf-token";
    meta.content = content;
    document.head.appendChild(meta);
    return meta;
  }

  it("injects a hidden _token input when a csrf token is known", () => {
    appendMeta("tok-123");
    renderWithProvider(
      <Form action="/projects">
        <input type="text" name="name" />
      </Form>,
    );
    const token = document.querySelector('input[type="hidden"][name="_token"]');
    expect(token).not.toBeNull();
    expect(token).toHaveValue("tok-123");
    expect(token).toHaveAttribute("data-fastplace-csrf");
  });

  it("omits the token input when no csrf token is known", () => {
    renderWithProvider(
      <Form action="/projects">
        <input type="text" name="name" />
      </Form>,
    );
    expect(document.querySelector('input[name="_token"]')).toBeNull();
  });

  it("renders the token input as the first form control (first value wins)", () => {
    appendMeta("tok-123");
    renderWithProvider(
      <Form action="/projects">
        <input type="text" name="name" />
      </Form>,
    );
    const form = document.querySelector("form")!;
    expect(form.firstElementChild).toBe(form.querySelector('input[name="_token"]'));
  });

  it("keeps the auto token out of the bridge JSON body (the header carries it)", async () => {
    const user = userEvent.setup();
    appendMeta("tok-123");
    vi.stubGlobal(
      "fetch",
      vi
        .fn()
        .mockResolvedValue(
          mockJsonResponse({ component: "Projects/Index", props: {}, url: "/projects" }),
        ),
    );

    renderWithProvider(
      <Form action="/projects">
        <input type="text" name="name" defaultValue="Apollo" />
        <button type="submit">Save</button>
      </Form>,
    );
    await user.click(screen.getByRole("button", { name: "Save" }));

    const [, init] = (fetch as ReturnType<typeof vi.fn>).mock.calls[0];
    expect(init.headers["X-Fastplace-CSRF-Token"]).toBe("tok-123");
    expect(JSON.parse(init.body)).toEqual({ name: "Apollo" });
  });
});

/* ------------------------------------------------------------------ *
 * useForm
 * ------------------------------------------------------------------ */

describe("useForm", () => {
  const ProbeForm = ({
    onError,
    onSuccess,
    onFinish,
  }: {
    onError?: () => void;
    onSuccess?: () => void;
    onFinish?: () => void;
  }) => {
    const form = useForm({ name: "Initial", email: "" });
    return (
      <div>
        <output data-testid="data">{JSON.stringify(form.data)}</output>
        <output data-testid="errors">{JSON.stringify(form.errors)}</output>
        <output data-testid="state">
          {form.processing ? "busy" : form.recentlySuccessful ? "fresh" : "idle"}
        </output>
        <button
          type="button"
          onClick={() => void form.post("/projects", { onError, onSuccess, onFinish })}
        >
          Save
        </button>
        <button type="button" onClick={() => form.setData("name", "Typed")}>
          Edit
        </button>
        <button type="button" onClick={() => form.reset("name")}>
          Reset name
        </button>
        <button type="button" onClick={() => form.clearErrors()}>
          Clear errors
        </button>
      </div>
    );
  };

  it("manages data, submits, and maps 422 responses onto errors", async () => {
    const user = userEvent.setup();
    const onError = vi.fn();
    vi.stubGlobal(
      "fetch",
      vi
        .fn()
        .mockResolvedValue(
          mockJsonResponse(
            { message: "The given data was invalid.", errors: { email: ["Invalid email."] } },
            false,
            422,
          ),
        ),
    );

    renderWithProvider(<ProbeForm onError={onError} />);
    await user.click(screen.getByRole("button", { name: "Edit" }));
    expect(screen.getByTestId("data")).toHaveTextContent("Typed");

    await user.click(screen.getByRole("button", { name: "Save" }));
    expect(await screen.findByTestId("errors")).toHaveTextContent("Invalid email.");
    expect(onError).toHaveBeenCalled();
    expect(screen.getByTestId("state")).toHaveTextContent("idle");

    await user.click(screen.getByRole("button", { name: "Clear errors" }));
    expect(screen.getByTestId("errors")).toHaveTextContent("{}");
  });

  it("resets fields back to the initial data", async () => {
    const user = userEvent.setup();
    renderWithProvider(<ProbeForm />);
    await user.click(screen.getByRole("button", { name: "Edit" }));
    await user.click(screen.getByRole("button", { name: "Reset name" }));
    expect(screen.getByTestId("data")).toHaveTextContent("Initial");
  });

  it("flags success and settles recentlySuccessful", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    const onSuccess = vi.fn();
    const onFinish = vi.fn();
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(mockJsonResponse({ ok: true })));

    const user2 = userEvent.setup({ advanceTimers: vi.advanceTimersByTime });
    renderWithProvider(<ProbeForm onSuccess={onSuccess} onFinish={onFinish} />);
    await user2.click(screen.getByRole("button", { name: "Save" }));

    expect(await screen.findByTestId("state")).toHaveTextContent("fresh");
    expect(onSuccess).toHaveBeenCalled();
    expect(onFinish).toHaveBeenCalled();

    await act(async () => {
      vi.advanceTimersByTime(2100);
    });
    await waitFor(() => expect(screen.getByTestId("state")).toHaveTextContent("idle"));
    vi.useRealTimers();
  });

  it("supports transform() before submit", async () => {
    const user = userEvent.setup();
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(mockJsonResponse({ ok: true })));

    const Transformed = () => {
      const form = useForm({ name: "  spaced  " });
      return (
        <button
          type="button"
          onClick={() => {
            form.transform((data) => ({ ...data, name: (data.name as string).trim() }));
            void form.post("/x");
          }}
        >
          Send
        </button>
      );
    };
    renderWithProvider(<Transformed />);
    await user.click(screen.getByRole("button", { name: "Send" }));
    const [, init] = (fetch as ReturnType<typeof vi.fn>).mock.calls[0];
    expect(init.body).toBe(JSON.stringify({ name: "spaced" })); // trimmed by transform()
  });
});

/* ------------------------------------------------------------------ *
 * <Head>
 * ------------------------------------------------------------------ */

describe("Head", () => {
  it("sets the document title", async () => {
    render(
      <FastplaceProvider initialPage={{ component: "A", props: {}, url: "/" }}>
        <Head title="Projects — Fastplace" />
      </FastplaceProvider>,
    );
    await waitFor(() => expect(document.title).toBe("Projects — Fastplace"));
  });
});
