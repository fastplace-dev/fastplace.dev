import "@testing-library/jest-dom/vitest";
import { cleanup, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import React from "react";

import ManageTwoFactor from "../manage-two-factor";

// jsdom ships no document.elementFromPoint — input-otp's password-manager
// badge probe calls it from a timer while an input inside the modal is focused.
document.elementFromPoint = document.elementFromPoint ?? (() => null);

// jsdom ships no scrollIntoView — revealing the recovery codes schedules a
// smooth scroll onto the list container.
window.HTMLElement.prototype.scrollIntoView =
  window.HTMLElement.prototype.scrollIntoView ?? vi.fn();

/** Bridge-shaped JSON answer, mirroring the form.test.tsx helper. */
function mockJsonResponse(payload: unknown, ok = true, status = 200) {
  return {
    ok,
    status,
    headers: new Headers({ "content-type": "application/json" }),
    redirected: false,
    json: () => Promise.resolve(payload),
  } as unknown as Response;
}

// The vitest jsdom window exposes no localStorage (browsers always do) —
// stand in a minimal, spec-shaped one for the appearance store used by the
// setup modal.
beforeEach(() => {
  const store = new Map<string, string>();
  vi.stubGlobal("localStorage", {
    getItem: (key: string) => store.get(key) ?? null,
    setItem: (key: string, value: string) => void store.set(key, value),
    removeItem: (key: string) => void store.delete(key),
    clear: () => store.clear(),
    key: (index: number) => [...store.keys()][index] ?? null,
    get length() {
      return store.size;
    },
  });
  // jsdom has no matchMedia either — a light stand-in resolves "system" to
  // light mode for the QR invert filter branch.
  vi.stubGlobal(
    "matchMedia",
    vi.fn(
      () =>
        ({
          matches: false,
          media: "",
          onchange: null,
          addEventListener: () => {},
          removeEventListener: () => {},
          addListener: () => {},
          removeListener: () => {},
          dispatchEvent: () => true,
        }) as MediaQueryList,
    ),
  );
});

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

type FetchMock = ReturnType<typeof vi.fn>;

/**
 * Bridge fetch stand-in routing by URL: every two-factor endpoint answers
 * with a realistic payload unless a test overrides one via `responses`.
 */
function stubBridgeFetch(responses: Record<string, Response> = {}): FetchMock {
  const defaults: Record<string, Response> = {
    "/user/two-factor-qr-code": mockJsonResponse({ svg: "<svg/>", url: "otpauth://test" }),
    "/user/two-factor-secret-key": mockJsonResponse({ secretKey: "TEST-SECRET-KEY" }),
    "/user/two-factor-recovery-codes": mockJsonResponse(["AAAA-1", "BBBB-2"]),
    "/user/two-factor-authentication": mockJsonResponse({ ok: true }),
  };

  const fetchMock = vi.fn((url: string) =>
    Promise.resolve(responses[url] ?? defaults[url] ?? mockJsonResponse({ ok: true })),
  );
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

function callsTo(fetchMock: FetchMock, url: string) {
  return fetchMock.mock.calls.filter(([calledUrl]) => calledUrl === url);
}

describe("ManageTwoFactor", () => {
  it("renders nothing when the user cannot manage two-factor authentication", () => {
    stubBridgeFetch();
    const { container } = render(<ManageTwoFactor canManageTwoFactor={false} />);

    expect(container).toBeEmptyDOMElement();
  });

  it("renders nothing by default without the management permission", () => {
    stubBridgeFetch();
    const { container } = render(<ManageTwoFactor twoFactorEnabled />);

    expect(container).toBeEmptyDOMElement();
  });

  it("shows the enable branch with heading and copy while 2FA is off", () => {
    stubBridgeFetch();
    render(<ManageTwoFactor canManageTwoFactor twoFactorEnabled={false} />);

    expect(screen.getByRole("heading", { name: "Two-factor authentication" })).toBeInTheDocument();
    expect(screen.getByText("Manage your two-factor authentication settings")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Enable 2FA" })).toBeInTheDocument();

    // The disabled state offers no disable control and no recovery codes card.
    expect(screen.queryByRole("button", { name: "Disable 2FA" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /recovery codes/i })).not.toBeInTheDocument();
  });

  it("enables 2FA through the bridge and opens the setup modal on success", async () => {
    const user = userEvent.setup();
    const fetchMock = stubBridgeFetch();

    render(<ManageTwoFactor canManageTwoFactor twoFactorEnabled={false} />);

    await user.click(screen.getByRole("button", { name: "Enable 2FA" }));

    // The enable toggle posts to the two-factor endpoint over the bridge.
    await waitFor(() =>
      expect(callsTo(fetchMock, "/user/two-factor-authentication")).toHaveLength(1),
    );
    const [url, init] = callsTo(fetchMock, "/user/two-factor-authentication")[0];
    expect(url).toBe("/user/two-factor-authentication");
    expect(init.method).toBe("POST");
    expect(init.headers["X-Fastplace-Request"]).toBe("true");

    // Success hands over to the setup modal.
    const dialog = await screen.findByRole("dialog");
    expect(
      within(dialog).getByRole("heading", { name: "Enable two-factor authentication" }),
    ).toBeInTheDocument();

    // The open modal fetches the QR code and the manual setup key.
    await waitFor(() => expect(callsTo(fetchMock, "/user/two-factor-qr-code")).toHaveLength(1));
    await waitFor(() => expect(callsTo(fetchMock, "/user/two-factor-secret-key")).toHaveLength(1));
    expect(await within(dialog).findByDisplayValue("TEST-SECRET-KEY")).toBeInTheDocument();
  });

  it("keeps the setup modal closed when enabling fails", async () => {
    const user = userEvent.setup();
    stubBridgeFetch({
      "/user/two-factor-authentication": mockJsonResponse(
        { message: "Failed.", errors: { code: ["Could not enable two-factor authentication."] } },
        false,
        422,
      ),
    });

    render(<ManageTwoFactor canManageTwoFactor twoFactorEnabled={false} />);

    const enableButton = screen.getByRole("button", { name: "Enable 2FA" });
    await user.click(enableButton);

    // The button recovers from the rejected submission...
    await waitFor(() => expect(enableButton).toBeEnabled());
    // ...and the setup modal never opened.
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  });

  it("offers Continue setup once cached setup data exists", async () => {
    const user = userEvent.setup();
    stubBridgeFetch();

    render(<ManageTwoFactor canManageTwoFactor twoFactorEnabled={false} />);

    await user.click(screen.getByRole("button", { name: "Enable 2FA" }));
    const dialog = await screen.findByRole("dialog");
    await within(dialog).findByDisplayValue("TEST-SECRET-KEY");

    // Dismissing via the dialog's icon close keeps the cached setup data
    // (2FA is not enabled yet, so nothing is cleared on close).
    await user.click(within(dialog).getByRole("button", { name: "Close" }));
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());

    // With setup data cached, the section pivots to Continue setup.
    expect(screen.getByRole("button", { name: /continue setup/i })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Enable 2FA" })).not.toBeInTheDocument();

    // Continue setup reopens the modal with the cached data still on show.
    await user.click(screen.getByRole("button", { name: /continue setup/i }));
    const reopened = await screen.findByRole("dialog");
    expect(await within(reopened).findByDisplayValue("TEST-SECRET-KEY")).toBeInTheDocument();
  });

  it("shows the disable branch when 2FA is on and disables over the bridge", async () => {
    const user = userEvent.setup();
    const fetchMock = stubBridgeFetch();

    render(<ManageTwoFactor canManageTwoFactor twoFactorEnabled />);

    expect(screen.getByRole("button", { name: "Disable 2FA" })).toBeInTheDocument();
    expect(screen.getByText(/prompted for a secure, random pin/i)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Enable 2FA" })).not.toBeInTheDocument();

    // The recovery codes card auto-loads its codes in the background.
    await waitFor(() =>
      expect(callsTo(fetchMock, "/user/two-factor-recovery-codes")).toHaveLength(1),
    );

    // Revealing them shows the fetched codes.
    await user.click(screen.getByRole("button", { name: /view recovery codes/i }));
    const list = await screen.findByRole("list", { name: "Recovery codes" });
    expect(within(list).getByText("AAAA-1")).toBeInTheDocument();
    expect(within(list).getByText("BBBB-2")).toBeInTheDocument();

    // Disabling sends a bridge DELETE to the two-factor endpoint.
    await user.click(screen.getByRole("button", { name: "Disable 2FA" }));
    await waitFor(() =>
      expect(callsTo(fetchMock, "/user/two-factor-authentication")).toHaveLength(1),
    );
    const [, init] = callsTo(fetchMock, "/user/two-factor-authentication")[0];
    expect(init.method).toBe("DELETE");
    expect(init.headers["X-Fastplace-Request"]).toBe("true");
  });

  it("clears cached two-factor data when 2FA switches off and on again", async () => {
    const user = userEvent.setup();
    const fetchMock = stubBridgeFetch();

    function Harness() {
      const [enabled, setEnabled] = React.useState(true);
      return (
        <>
          <ManageTwoFactor canManageTwoFactor twoFactorEnabled={enabled} />
          <button onClick={() => setEnabled((value) => !value)}>toggle</button>
        </>
      );
    }

    render(<Harness />);

    // Enabled: the recovery codes card mounts and fetches once.
    await waitFor(() =>
      expect(callsTo(fetchMock, "/user/two-factor-recovery-codes")).toHaveLength(1),
    );

    // Disable, then re-enable: the cached codes were cleared on the
    // enabled -> disabled transition, so the remounted card refetches.
    await user.click(screen.getByRole("button", { name: "toggle" }));
    expect(screen.getByRole("button", { name: "Enable 2FA" })).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "toggle" }));
    expect(screen.getByRole("button", { name: "Disable 2FA" })).toBeInTheDocument();
    await waitFor(() =>
      expect(callsTo(fetchMock, "/user/two-factor-recovery-codes")).toHaveLength(2),
    );
  });
});
