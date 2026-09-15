import { useCallback, useState } from "react";

// Plain bridge-style endpoints backing the settings/security two-factor
// setup flow — kept as constants so pages never hardcode them inline.
const TWO_FACTOR_QR_CODE_URL = "/user/two-factor-qr-code";
const TWO_FACTOR_SECRET_KEY_URL = "/user/two-factor-secret-key";
const TWO_FACTOR_RECOVERY_CODES_URL = "/user/two-factor-recovery-codes";

// Endpoint payload shapes (local to this hook — no shared types involved).
type QrCodeResponse = { svg: string; url: string };
type SecretKeyResponse = { secretKey: string };

/** GET a plain JSON payload from a bridge endpoint, failing on !ok. */
async function getJson<T>(url: string): Promise<T> {
  const response = await fetch(url, { headers: { Accept: "application/json" } });
  if (!response.ok) {
    throw new Error(`Request to ${url} failed with ${response.status}`);
  }
  return (await response.json()) as T;
}

export type UseTwoFactorAuthReturn = {
  qrCodeSvg: string | null;
  manualSetupKey: string | null;
  recoveryCodesList: string[];
  hasSetupData: boolean;
  errors: string[];
  clearErrors: () => void;
  clearSetupData: () => void;
  clearTwoFactorAuthData: () => void;
  fetchQrCode: () => Promise<void>;
  fetchSetupKey: () => Promise<void>;
  fetchSetupData: () => Promise<void>;
  fetchRecoveryCodes: () => Promise<void>;
};

export const OTP_MAX_LENGTH = 6;

export const useTwoFactorAuth = (): UseTwoFactorAuthReturn => {
  const [qrCodeSvg, setQrCodeSvg] = useState<string | null>(null);
  const [manualSetupKey, setManualSetupKey] = useState<string | null>(null);
  const [recoveryCodesList, setRecoveryCodesList] = useState<string[]>([]);
  const [errors, setErrors] = useState<string[]>([]);

  const hasSetupData = qrCodeSvg !== null && manualSetupKey !== null;

  const clearErrors = useCallback((): void => {
    setErrors([]);
  }, []);

  const clearSetupData = useCallback((): void => {
    setManualSetupKey(null);
    setQrCodeSvg(null);
    setErrors([]);
  }, []);

  const clearTwoFactorAuthData = useCallback((): void => {
    setManualSetupKey(null);
    setQrCodeSvg(null);
    setErrors([]);
    setRecoveryCodesList([]);
  }, []);

  const fetchQrCode = useCallback(async (): Promise<void> => {
    try {
      const { svg } = await getJson<QrCodeResponse>(TWO_FACTOR_QR_CODE_URL);

      setQrCodeSvg(svg);
    } catch {
      setErrors((prev) => [...prev, "Failed to fetch QR code"]);
      setQrCodeSvg(null);
    }
  }, []);

  const fetchSetupKey = useCallback(async (): Promise<void> => {
    try {
      const { secretKey: key } = await getJson<SecretKeyResponse>(TWO_FACTOR_SECRET_KEY_URL);

      setManualSetupKey(key);
    } catch {
      setErrors((prev) => [...prev, "Failed to fetch a setup key"]);
      setManualSetupKey(null);
    }
  }, []);

  const fetchRecoveryCodes = useCallback(async (): Promise<void> => {
    try {
      setErrors([]);
      const codes = await getJson<string[]>(TWO_FACTOR_RECOVERY_CODES_URL);
      setRecoveryCodesList(codes);
    } catch {
      setErrors((prev) => [...prev, "Failed to fetch recovery codes"]);
      setRecoveryCodesList([]);
    }
  }, []);

  const fetchSetupData = useCallback(async (): Promise<void> => {
    try {
      setErrors([]);
      await Promise.all([fetchQrCode(), fetchSetupKey()]);
    } catch {
      setQrCodeSvg(null);
      setManualSetupKey(null);
    }
  }, [fetchQrCode, fetchSetupKey]);

  return {
    qrCodeSvg,
    manualSetupKey,
    recoveryCodesList,
    hasSetupData,
    errors,
    clearErrors,
    clearSetupData,
    clearTwoFactorAuthData,
    fetchQrCode,
    fetchSetupKey,
    fetchSetupData,
    fetchRecoveryCodes,
  };
};
