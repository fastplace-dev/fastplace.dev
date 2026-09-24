import "@testing-library/jest-dom/vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import React from "react";
import { FastplaceProvider, Form, router, useForm } from "../index";

/** An error JSON answer shaped like the backend's unified envelope. */
function errorResponse(status: number, body: unknown, contentType = "application/json") {
  return {
    ok: false,
    status,
    headers: new Headers({ "content-type": contentType }),
    redirected: false,
    json: () => Promise.resolve(body),
  } as unknown as Response;
}

/** Replace window.location with a controllable stub — verbatim from bridge.test.tsx. */
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

beforeEach(() => {
  cleanup();
});

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  window.history.replaceState(null, "", "/");
  router.reset();
});

describe("useForm non-422 failures", () => {
  it("surfaces the envelope message and status from a 403", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(errorResponse(403, { message: "You do not own this project." })),
    );
    let state: ReturnType<typeof useForm<{ name: string }>> | null = null;
    const Page = () => {
      state = useForm({ name: "Firoz" });
      return <button onClick={() => state!.post("/settings/profile")}>save</button>;
    };
    render(
      <FastplaceProvider initialPage={{ component: "P", props: {}, url: "/p" } as never}>
        <Page />
      </FastplaceProvider>,
    );
    await userEvent.click(screen.getByRole("button"));
    expect(state!.message).toBe("You do not own this project.");
    expect(state!.status).toBe(403);
    expect(state!.errors).toEqual({});
  });

  it("keeps the 422 field-mapping contract untouched", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        errorResponse(422, {
          message: "The given data was invalid.",
          errors: { email: ["The email field is required."] },
        }),
      ),
    );
    let state: ReturnType<typeof useForm<{ email: string }>> | null = null;
    const Page = () => {
      state = useForm({ email: "" });
      return <button onClick={() => state!.post("/login")}>save</button>;
    };
    render(
      <FastplaceProvider initialPage={{ component: "P", props: {}, url: "/p" } as never}>
        <Page />
      </FastplaceProvider>,
    );
    await userEvent.click(screen.getByRole("button"));
    expect(state!.errors).toEqual({ email: ["The email field is required."] });
    expect(state!.message).toBeUndefined();
    expect(state!.status).toBeUndefined();
  });

  it("has no message on a network-level failure", async () => {
    vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new TypeError("network down")));
    let state: ReturnType<typeof useForm<{ name: string }>> | null = null;
    const Page = () => {
      state = useForm({ name: "" });
      return <button onClick={() => state!.post("/x")}>save</button>;
    };
    render(
      <FastplaceProvider initialPage={{ component: "P", props: {}, url: "/p" } as never}>
        <Page />
      </FastplaceProvider>,
    );
    await userEvent.click(screen.getByRole("button"));
    expect(state!.errors).toEqual({});
    expect(state!.message).toBeUndefined();
  });

  it("hands the failure object to onError as a second argument", async () => {
    const onError = vi.fn();
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(errorResponse(429, { message: "Too many attempts." })),
    );
    let state: ReturnType<typeof useForm<{ name: string }>> | null = null;
    const Page = () => {
      state = useForm({ name: "" });
      return <button onClick={() => state!.post("/x", { onError })}>save</button>;
    };
    render(
      <FastplaceProvider initialPage={{ component: "P", props: {}, url: "/p" } as never}>
        <Page />
      </FastplaceProvider>,
    );
    await userEvent.click(screen.getByRole("button"));
    expect(onError).toHaveBeenCalledWith({}, { message: "Too many attempts.", status: 429 });
  });
});

describe("Form render-prop non-422 failures", () => {
  it("exposes message and status to the render prop", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(errorResponse(403, { message: "This action is unauthorized." })),
    );
    render(
      <FastplaceProvider initialPage={{ component: "P", props: {}, url: "/p" } as never}>
        <Form action="/x">
          {({ message, status }) => (
            <output>
              {message}|{status ?? ""}
            </output>
          )}
        </Form>
      </FastplaceProvider>,
    );
    // The form has no inputs — fire the submit event directly so the bridge
    // handler runs (the render-prop assertion is the contract).
    fireEvent.submit(document.querySelector("form")!);
    await waitFor(() =>
      expect(screen.getByText("This action is unauthorized.|403")).toBeInTheDocument(),
    );
  });
});

describe("router.visit error interception", () => {
  const restores: Array<() => void> = [];
  afterEach(() => {
    restores.splice(0).forEach((restore) => restore());
  });

  it("calls onError with the parsed envelope instead of the full-page fallback", async () => {
    const { assign, restore } = stubLocation();
    restores.push(restore);
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(errorResponse(403, { message: "Forbidden area." })),
    );
    const onError = vi.fn();
    await router.visit("/admin", { onError });
    expect(onError).toHaveBeenCalledWith({ message: "Forbidden area.", status: 403 });
    expect(assign).not.toHaveBeenCalled();
  });

  it("keeps the full-page fallback when no onError is given", async () => {
    const { assign, restore } = stubLocation();
    restores.push(restore);
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(errorResponse(403, { message: "Forbidden area." })),
    );
    await router.visit("/admin");
    expect(assign).toHaveBeenCalledTimes(1);
  });

  it("still falls back on non-JSON error answers even with onError", async () => {
    const { assign, restore } = stubLocation();
    restores.push(restore);
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(errorResponse(500, "<html>boom</html>", "text/html")),
    );
    const onError = vi.fn();
    await router.visit("/admin", { onError });
    expect(onError).not.toHaveBeenCalled();
    expect(assign).toHaveBeenCalledTimes(1);
  });
});
