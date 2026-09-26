import "@testing-library/jest-dom/vitest";
import { cleanup, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import React from "react";

import TwoFactorSetupModal from "../two-factor-setup-modal";

// jsdom ships no document.elementFromPoint — input-otp's password-manager
// badge probe calls it from a timer while the input is focused.
document.elementFromPoint = document.elementFromPoint ?? (() => null);

type ModalProps = React.ComponentProps<typeof TwoFactorSetupModal>;

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
// stand in a minimal, spec-shaped one for the appearance store.
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

/** Render the modal with sane defaults; the harness owns `open` state. */
function renderModal(overrides: Partial<ModalProps> = {}) {
  const onClose = vi.fn();
  const clearSetupData = vi.fn();
  const fetchSetupData = vi.fn(() => Promise.resolve());
  const props: ModalProps = {
    isOpen: true,
    onClose,
    requiresConfirmation: false,
    twoFactorEnabled: false,
    qrCodeSvg: null,
    manualSetupKey: null,
    clearSetupData,
    fetchSetupData,
    errors: [],
    ...overrides,
  };

  function Harness() {
    const [open, setOpen] = React.useState(props.isOpen);
    return (
      <TwoFactorSetupModal
        {...props}
        isOpen={open}
        onClose={() => {
          onClose();
          setOpen(false);
        }}
      />
    );
  }

  render(<Harness />);
  return { onClose, clearSetupData, fetchSetupData };
}

/** Advance from the setup step to the OTP verification step. */
async function advanceToVerification(user: ReturnType<typeof userEvent.setup>) {
  await user.click(await screen.findByRole("button", { name: "Continue" }));
  expect(
    await screen.findByRole("heading", { name: "Verify authentication code" }),
  ).toBeInTheDocument();
  return screen.getByRole("dialog");
}

describe("TwoFactorSetupModal", () => {
  it("stays hidden while closed and never fetches setup data", () => {
    const { fetchSetupData } = renderModal({ isOpen: false });

    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    expect(fetchSetupData).not.toHaveBeenCalled();
  });

  it("opens on the setup step, fetches setup data once, and shows loading spinners", async () => {
    const { fetchSetupData } = renderModal();

    expect(await screen.findByRole("dialog")).toBeInTheDocument();
    expect(
      screen.getByRole("heading", { name: "Enable two-factor authentication" }),
    ).toBeInTheDocument();
    expect(screen.getByText(/scan the QR code/i)).toBeInTheDocument();
    // QR pane and manual-key pane both wait on their fetches.
    expect(screen.getAllByRole("status", { name: "Loading" })).toHaveLength(2);
    await waitFor(() => expect(fetchSetupData).toHaveBeenCalledTimes(1));
  });

  it("renders the fetched QR code svg and the manual setup key with a copy control", () => {
    renderModal({
      qrCodeSvg: "<svg viewBox='0 0 1 1'><path/></svg>",
      manualSetupKey: "ABC-123-KEY",
    });

    // The fetched SVG markup is injected verbatim into the QR pane.
    expect(document.querySelector("svg[viewBox='0 0 1 1']")).not.toBeNull();

    const keyInput = screen.getByRole("textbox") as HTMLInputElement;
    expect(keyInput).toHaveValue("ABC-123-KEY");
    expect(keyInput).toHaveAttribute("readonly");

    // The icon-only copy button sits beside the key input.
    const copyButton = keyInput.parentElement?.querySelector("button");
    expect(copyButton).not.toBeNull();
  });

  it("copies the manual setup key through the clipboard", async () => {
    const user = userEvent.setup();
    // userEvent installs the Clipboard API onto navigator during setup —
    // spy on that writeText once it exists.
    const writeText = vi.spyOn(navigator.clipboard, "writeText").mockResolvedValue(undefined);

    renderModal({ qrCodeSvg: "<svg/>", manualSetupKey: "ABC-123-KEY" });

    const keyInput = screen.getByRole("textbox") as HTMLInputElement;
    const copyButton = keyInput.parentElement!.querySelector("button")!;
    await user.click(copyButton);

    await waitFor(() => expect(writeText).toHaveBeenCalledWith("ABC-123-KEY"));
    writeText.mockRestore();
  });

  it("closes and clears setup data when no confirmation is required", async () => {
    const user = userEvent.setup();
    const { onClose, clearSetupData } = renderModal({ qrCodeSvg: "<svg/>", manualSetupKey: "K" });

    await user.click(await screen.findByRole("button", { name: "Continue" }));

    expect(clearSetupData).toHaveBeenCalledTimes(1);
    expect(onClose).toHaveBeenCalledTimes(1);
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
  });

  it("moves to the verification step with six OTP slots when confirmation is required", async () => {
    const user = userEvent.setup();
    renderModal({ qrCodeSvg: "<svg/>", manualSetupKey: "K", requiresConfirmation: true });

    const dialog = await advanceToVerification(user);

    expect(within(dialog).getByText(/enter the 6-digit code/i)).toBeInTheDocument();
    expect(dialog.querySelectorAll("[data-slot='input-otp-slot']")).toHaveLength(6);
    expect(within(dialog).getByRole("button", { name: "Back" })).toBeInTheDocument();
    const confirm = within(dialog).getByRole("button", { name: "Confirm" });
    expect(confirm).toBeDisabled();
  });

  it("returns to the setup step from Back", async () => {
    const user = userEvent.setup();
    renderModal({ qrCodeSvg: "<svg/>", manualSetupKey: "K", requiresConfirmation: true });

    await advanceToVerification(user);
    await user.click(screen.getByRole("button", { name: "Back" }));

    expect(
      await screen.findByRole("heading", { name: "Enable two-factor authentication" }),
    ).toBeInTheDocument();
  });

  it("confirms through the bridge: posts the code and closes on success", async () => {
    const user = userEvent.setup();
    const { onClose } = renderModal({
      qrCodeSvg: "<svg/>",
      manualSetupKey: "K",
      requiresConfirmation: true,
    });
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(mockJsonResponse({ ok: true })));

    await advanceToVerification(user);
    await user.type(screen.getByRole("textbox"), "123456");
    await user.click(screen.getByRole("button", { name: "Confirm" }));

    await waitFor(() => expect(onClose).toHaveBeenCalledTimes(1));
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());

    const fetchMock = fetch as unknown as ReturnType<typeof vi.fn>;
    expect(fetchMock).toHaveBeenCalledTimes(1);
    const [url, init] = fetchMock.mock.calls[0];
    expect(url).toBe("/user/confirmed-two-factor-authentication");
    expect(init.method).toBe("POST");
    expect(init.headers["X-Fastplace-Request"]).toBe("true");
    expect(JSON.parse(init.body)).toEqual({ code: "123456" });
  });

  it("surfaces the field error and stays open when the code is rejected", async () => {
    const user = userEvent.setup();
    const { onClose } = renderModal({
      qrCodeSvg: "<svg/>",
      manualSetupKey: "K",
      requiresConfirmation: true,
    });
    vi.stubGlobal(
      "fetch",
      vi
        .fn()
        .mockResolvedValue(
          mockJsonResponse(
            { message: "Invalid.", errors: { code: ["The provided code is invalid."] } },
            false,
            422,
          ),
        ),
    );

    await advanceToVerification(user);
    await user.type(screen.getByRole("textbox"), "000000");
    await user.click(screen.getByRole("button", { name: "Confirm" }));

    expect(await screen.findByText("The provided code is invalid.")).toBeInTheDocument();
    expect(screen.getByRole("dialog")).toBeInTheDocument();
    expect(onClose).not.toHaveBeenCalled();
  });

  it("offers a plain Close button once two-factor is already enabled", async () => {
    const user = userEvent.setup();
    const { onClose, clearSetupData } = renderModal({
      twoFactorEnabled: true,
      qrCodeSvg: "<svg/>",
      manualSetupKey: "K",
    });

    expect(
      screen.getByRole("heading", { name: "Two-factor authentication enabled" }),
    ).toBeInTheDocument();

    // Two "Close" buttons exist (the icon-only dialog dismiss and the primary
    // one) — act on the primary Button, identified by its data-slot.
    const closers = await screen.findAllByRole("button", { name: "Close" });
    const primary = closers.find((button) => button.hasAttribute("data-slot"));
    expect(primary).toBeDefined();
    await user.click(primary!);

    // Cleared twice, mirroring the reference flow: once by the button's next
    // step handler, once by resetModalState() while closing in the enabled
    // state.
    expect(clearSetupData).toHaveBeenCalledTimes(2);
    expect(onClose).toHaveBeenCalledTimes(1);
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
  });

  it("replaces the setup pane with the error alert when fetching failed", () => {
    renderModal({ errors: ["Failed to fetch QR code"] });

    const alert = screen.getByRole("alert");
    expect(alert).toHaveTextContent("Something went wrong.");
    expect(alert).toHaveTextContent("Failed to fetch QR code");
    expect(screen.queryByRole("button", { name: "Continue" })).not.toBeInTheDocument();
  });
});
