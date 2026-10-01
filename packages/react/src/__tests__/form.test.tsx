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

  it("restores a <select> to its initial option after success, not option 0", async () => {
    const user = userEvent.setup();
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(mockJsonResponse({ ok: true })));

    renderWithProvider(
      <Form action="/filters" resetOnSuccess={["status", "plain"]}>
        <select name="status" aria-label="Status" defaultValue="archived">
          <option value="active">Active</option>
          <option value="paused">Paused</option>
          <option value="archived">Archived</option>
        </select>
        <select name="plain" aria-label="Plain">
          <option value="a">A</option>
          <option value="b">B</option>
        </select>
        <button type="submit">Apply</button>
      </Form>,
    );
    const status = screen.getByLabelText("Status");
    const plain = screen.getByLabelText("Plain");
    expect(status).toHaveValue("archived"); // preselected away from option 0
    await user.selectOptions(status, "active");
    await user.selectOptions(plain, "b");
    await user.click(screen.getByRole("button", { name: "Apply" }));

    await waitFor(() => {
      expect(status).toHaveValue("archived");
      // No initial selection of its own → back to the first option.
      expect(plain).toHaveValue("a");
    });
  });

  it("includes the clicked submit button's name/value in the bridge body", async () => {
    const user = userEvent.setup();
    const fetchMock = vi.fn().mockResolvedValue(mockJsonResponse({ ok: true }));
    vi.stubGlobal("fetch", fetchMock);

    renderWithProvider(
      <Form action="/posts">
        <input type="text" name="title" defaultValue="Hello" />
        <button type="submit" name="action" value="draft">
          Save Draft
        </button>
        <button type="submit" name="action" value="publish">
          Publish
        </button>
      </Form>,
    );
    await user.click(screen.getByRole("button", { name: "Publish" }));
    let [, init] = fetchMock.mock.calls[0];
    // Native no-JS posts carry the submitter's entry — the bridge must match.
    expect(JSON.parse(init.body)).toEqual({ title: "Hello", action: "publish" });

    await user.click(screen.getByRole("button", { name: "Save Draft" }));
    [, init] = fetchMock.mock.calls[1];
    expect(JSON.parse(init.body)).toEqual({ title: "Hello", action: "draft" });
  });
});

/* ------------------------------------------------------------------ *
 * <Form> file inputs — multipart submissions preserve uploads
 * ------------------------------------------------------------------ */

describe("Form file uploads", () => {
  it("submits native multipart FormData when the form carries a file input", async () => {
    const user = userEvent.setup();
    const meta = document.createElement("meta");
    meta.name = "csrf-token";
    meta.content = "tok-123";
    document.head.appendChild(meta);
    const fetchMock = vi.fn().mockResolvedValue(mockJsonResponse({ ok: true }));
    vi.stubGlobal("fetch", fetchMock);

    renderWithProvider(
      <Form action="/uploads">
        <input type="text" name="title" defaultValue="Doc" />
        <input type="file" name="avatar" aria-label="Avatar" />
        <button type="submit">Upload</button>
      </Form>,
    );
    const file = new File(["file-bytes"], "avatar.png", { type: "image/png" });
    await user.upload(screen.getByLabelText("Avatar"), file);
    await user.click(screen.getByRole("button", { name: "Upload" }));

    const [url, init] = fetchMock.mock.calls[0];
    expect(url).toBe("/uploads");
    expect(init.method).toBe("POST");
    expect(init.headers["X-Fastplace-Request"]).toBe("true");
    expect(init.headers["X-Fastplace-CSRF-Token"]).toBe("tok-123");
    // No JSON content type — the browser sets the multipart boundary.
    expect(init.headers["Content-Type"]).toBeUndefined();
    expect(init.body).toBeInstanceOf(FormData);
    const body = init.body as FormData;
    expect(body.get("title")).toBe("Doc");
    expect(body.get("avatar")).toBeInstanceOf(File);
    // The auto CSRF input stays out — the header carries the token.
    expect(body.get("_token")).toBeNull();
    meta.remove();
  });

  it("keeps JSON bodies for forms without file inputs", async () => {
    const user = userEvent.setup();
    const fetchMock = vi.fn().mockResolvedValue(mockJsonResponse({ ok: true }));
    vi.stubGlobal("fetch", fetchMock);

    renderWithProvider(
      <Form action="/projects">
        <input type="text" name="name" defaultValue="Apollo" />
        <button type="submit">Save</button>
      </Form>,
    );
    await user.click(screen.getByRole("button", { name: "Save" }));

    const [, init] = fetchMock.mock.calls[0];
    expect(init.headers["Content-Type"]).toBe("application/json");
    expect(JSON.parse(init.body)).toEqual({ name: "Apollo" });
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
 * <Form> no-JS method override
 * ------------------------------------------------------------------ */

describe("Form no-JS method override", () => {
  it("injects a hidden _method field for non-post methods", () => {
    renderWithProvider(
      <Form action="/posts/3" method="delete">
        <button type="submit">Delete</button>
      </Form>,
    );
    const form = document.querySelector("form")!;
    // HTML only knows post/get — the intended verb rides as _method.
    expect(form).toHaveAttribute("method", "post");
    const override = form.querySelector('input[type="hidden"][name="_method"]');
    expect(override).not.toBeNull();
    expect(override).toHaveValue("delete");
  });

  it("omits _method for plain post forms", () => {
    renderWithProvider(
      <Form action="/posts">
        <button type="submit">Save</button>
      </Form>,
    );
    expect(document.querySelector('input[name="_method"]')).toBeNull();
  });

  it("keeps the auto _method out of the bridge JSON body — the real verb carries it", async () => {
    const user = userEvent.setup();
    const fetchMock = vi.fn().mockResolvedValue(mockJsonResponse({ ok: true }));
    vi.stubGlobal("fetch", fetchMock);

    renderWithProvider(
      <Form action="/posts/3" method="patch">
        <input type="text" name="title" defaultValue="Hello" />
        <button type="submit">Update</button>
      </Form>,
    );
    await user.click(screen.getByRole("button", { name: "Update" }));

    const [url, init] = fetchMock.mock.calls[0];
    expect(url).toBe("/posts/3");
    expect(init.method).toBe("PATCH"); // the bridge request uses the real verb
    expect(JSON.parse(init.body)).toEqual({ title: "Hello" });
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

  it("sends multipart instead of JSON when the data carries a File", async () => {
    const user = userEvent.setup();
    const file = new File(["file-bytes"], "avatar.png", { type: "image/png" });
    const fetchMock = vi.fn().mockResolvedValue(mockJsonResponse({ ok: true }));
    vi.stubGlobal("fetch", fetchMock);

    // JSON.stringify would corrupt the File to {} — the submission must
    // travel as native multipart instead, upload intact.
    const UploadForm = () => {
      const form = useForm({ title: "Doc", avatar: file });
      return (
        <button type="button" onClick={() => void form.post("/uploads")}>
          Upload
        </button>
      );
    };
    renderWithProvider(<UploadForm />);
    await user.click(screen.getByRole("button", { name: "Upload" }));

    const [, init] = fetchMock.mock.calls[0];
    expect(init.headers["Content-Type"]).toBeUndefined();
    expect(init.body).toBeInstanceOf(FormData);
    const body = init.body as FormData;
    expect(body.get("title")).toBe("Doc");
    const sent = body.get("avatar");
    expect(sent).toBeInstanceOf(File);
    expect((sent as File).name).toBe("avatar.png");
    expect((sent as File).size).toBe(file.size);
  });

  it("sends multipart when a File sits inside an array value (multi-upload)", async () => {
    const user = userEvent.setup();
    const files = [
      new File(["one"], "a.png", { type: "image/png" }),
      new File(["two"], "b.png", { type: "image/png" }),
    ];
    const fetchMock = vi.fn().mockResolvedValue(mockJsonResponse({ ok: true }));
    vi.stubGlobal("fetch", fetchMock);

    // A File nested in an array must not slip through to JSON.stringify,
    // which corrupts it to [{}].
    const MultiUploadForm = () => {
      const form = useForm({ attachments: files });
      return (
        <button type="button" onClick={() => void form.post("/uploads")}>
          Upload
        </button>
      );
    };
    renderWithProvider(<MultiUploadForm />);
    await user.click(screen.getByRole("button", { name: "Upload" }));

    const [, init] = fetchMock.mock.calls[0];
    expect(init.headers["Content-Type"]).toBeUndefined();
    expect(init.body).toBeInstanceOf(FormData);
    const sent = (init.body as FormData).getAll("attachments");
    expect(sent).toHaveLength(2);
    expect(sent.every((entry) => entry instanceof File)).toBe(true);
    expect((sent[0] as File).name).toBe("a.png");
    expect((sent[1] as File).name).toBe("b.png");
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
