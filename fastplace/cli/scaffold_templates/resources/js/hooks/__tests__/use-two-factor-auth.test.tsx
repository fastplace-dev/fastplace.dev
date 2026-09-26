import "@testing-library/jest-dom/vitest";
import { act, cleanup, renderHook } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { useTwoFactorAuth } from "../use-two-factor-auth";

const QR_CODE_URL = "/user/two-factor-qr-code";
const SECRET_KEY_URL = "/user/two-factor-secret-key";
const RECOVERY_CODES_URL = "/user/two-factor-recovery-codes";

function jsonResponse(payload: unknown, ok = true): Response {
  return {
    ok,
    headers: new Headers({ "content-type": "application/json" }),
    json: () => Promise.resolve(payload),
  } as unknown as Response;
}

// The hook fetches JSON from plain bridge endpoints — stand in global fetch
// with a tiny URL router so each test controls what the backend answers.
type RouteTable = Record<string, unknown>;
function stubFetchRoutes(routes: RouteTable, ok = true): ReturnType<typeof vi.fn> {
  const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
    const url = String(input);
    if (url in routes) return jsonResponse(routes[url], ok);
    throw new Error(`unexpected fetch: ${url}`);
  });
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  vi.clearAllMocks();
});

describe("useTwoFactorAuth", () => {
  it("starts with no setup data and no errors", () => {
    stubFetchRoutes({});

    const { result } = renderHook(() => useTwoFactorAuth());

    expect(result.current.qrCodeSvg).toBeNull();
    expect(result.current.manualSetupKey).toBeNull();
    expect(result.current.recoveryCodesList).toEqual([]);
    expect(result.current.hasSetupData).toBe(false);
    expect(result.current.errors).toEqual([]);
  });

  it("fetchQrCode stores the svg from the qr endpoint", async () => {
    const fetchMock = stubFetchRoutes({
      [QR_CODE_URL]: { svg: "<svg>qr</svg>", url: "otpauth://totp/Example" },
    });

    const { result } = renderHook(() => useTwoFactorAuth());
    await act(async () => {
      await result.current.fetchQrCode();
    });

    expect(fetchMock).toHaveBeenCalledWith(QR_CODE_URL, expect.anything());
    expect(result.current.qrCodeSvg).toBe("<svg>qr</svg>");
    expect(result.current.hasSetupData).toBe(false); // no setup key yet
    expect(result.current.errors).toEqual([]);
  });

  it("fetchSetupKey stores the manual setup key", async () => {
    stubFetchRoutes({ [SECRET_KEY_URL]: { secretKey: "AB3X-9KQ2" } });

    const { result } = renderHook(() => useTwoFactorAuth());
    await act(async () => {
      await result.current.fetchSetupKey();
    });

    expect(result.current.manualSetupKey).toBe("AB3X-9KQ2");
    expect(result.current.hasSetupData).toBe(false); // no qr svg yet
  });

  it("fetchSetupData loads both halves and flips hasSetupData", async () => {
    stubFetchRoutes({
      [QR_CODE_URL]: { svg: "<svg>qr</svg>", url: "otpauth://totp/Example" },
      [SECRET_KEY_URL]: { secretKey: "AB3X-9KQ2" },
    });

    const { result } = renderHook(() => useTwoFactorAuth());
    await act(async () => {
      await result.current.fetchSetupData();
    });

    expect(result.current.qrCodeSvg).toBe("<svg>qr</svg>");
    expect(result.current.manualSetupKey).toBe("AB3X-9KQ2");
    expect(result.current.hasSetupData).toBe(true);
    expect(result.current.errors).toEqual([]);
  });

  it("records an error and clears the svg when the qr fetch fails", async () => {
    stubFetchRoutes({}, false); // every route answers !ok

    const { result } = renderHook(() => useTwoFactorAuth());
    await act(async () => {
      await result.current.fetchQrCode();
    });

    expect(result.current.qrCodeSvg).toBeNull();
    expect(result.current.errors).toContain("Failed to fetch QR code");
  });

  it("records an error when the setup key fetch fails", async () => {
    stubFetchRoutes({}, false);

    const { result } = renderHook(() => useTwoFactorAuth());
    await act(async () => {
      await result.current.fetchSetupKey();
    });

    expect(result.current.manualSetupKey).toBeNull();
    expect(result.current.errors).toContain("Failed to fetch a setup key");
  });

  it("fetchRecoveryCodes stores the code list", async () => {
    stubFetchRoutes({ [RECOVERY_CODES_URL]: ["111-111", "222-222"] });

    const { result } = renderHook(() => useTwoFactorAuth());
    await act(async () => {
      await result.current.fetchRecoveryCodes();
    });

    expect(result.current.recoveryCodesList).toEqual(["111-111", "222-222"]);
    expect(result.current.errors).toEqual([]);
  });

  it("records an error and empties the list when recovery codes fail", async () => {
    stubFetchRoutes({ [RECOVERY_CODES_URL]: ["111-111"] });
    const { result } = renderHook(() => useTwoFactorAuth());
    await act(async () => {
      await result.current.fetchRecoveryCodes(); // seed a list first
    });

    const failing = stubFetchRoutes({}, false);
    void failing;
    await act(async () => {
      await result.current.fetchRecoveryCodes();
    });

    expect(result.current.recoveryCodesList).toEqual([]);
    expect(result.current.errors).toContain("Failed to fetch recovery codes");
  });

  it("clearErrors drops only the error list", async () => {
    stubFetchRoutes({}, false);
    const { result } = renderHook(() => useTwoFactorAuth());
    await act(async () => {
      await result.current.fetchQrCode();
    });
    expect(result.current.errors).toHaveLength(1);

    act(() => result.current.clearErrors());

    expect(result.current.errors).toEqual([]);
    expect(result.current.qrCodeSvg).toBeNull();
  });

  it("clearSetupData drops the qr svg, key, and errors together", async () => {
    stubFetchRoutes({
      [QR_CODE_URL]: { svg: "<svg>qr</svg>", url: "otpauth://totp/Example" },
      [SECRET_KEY_URL]: { secretKey: "AB3X-9KQ2" },
    });
    const { result } = renderHook(() => useTwoFactorAuth());
    await act(async () => {
      await result.current.fetchSetupData();
    });
    expect(result.current.hasSetupData).toBe(true);

    act(() => result.current.clearSetupData());

    expect(result.current.qrCodeSvg).toBeNull();
    expect(result.current.manualSetupKey).toBeNull();
    expect(result.current.hasSetupData).toBe(false);
    expect(result.current.errors).toEqual([]);
  });

  it("clearTwoFactorAuthData also wipes the recovery codes", async () => {
    stubFetchRoutes({ [RECOVERY_CODES_URL]: ["111-111"] });
    const { result } = renderHook(() => useTwoFactorAuth());
    await act(async () => {
      await result.current.fetchRecoveryCodes();
    });
    expect(result.current.recoveryCodesList).toEqual(["111-111"]);

    act(() => result.current.clearTwoFactorAuthData());

    expect(result.current.qrCodeSvg).toBeNull();
    expect(result.current.manualSetupKey).toBeNull();
    expect(result.current.recoveryCodesList).toEqual([]);
    expect(result.current.errors).toEqual([]);
  });
});
