import { useCallback, useState } from "react";

import { router, useForm } from "@fastplace/react";
import { KeyRound } from "lucide-react";
import { fromBase64Url, toBase64Url } from "@/lib/base64url";

import InputError from "@/components/input-error";
import { Button } from "@/components/ui/button";
import { Separator } from "@/components/ui/separator";
import { Spinner } from "@/components/ui/spinner";
import { toUrl } from "@/lib/utils";

// Default endpoints: the passkey login flow. The confirm flow passes its
// own pair through the `routes` prop.
const DEFAULT_OPTIONS_URL = "/passkeys/login/options";
const DEFAULT_SUBMIT_URL = "/passkeys/login";

const VERIFY_FAILED = "Unable to verify this passkey. Please try again.";

/** Assertion options as the backend ships them (JSON, challenge included). */
type AssertionOptions = Record<string, unknown>;

/** The parts of a WebAuthn assertion this flow needs to serialize. */
type SerializableCredential = {
  id: string;
  rawId: ArrayBuffer;
  type: string;
  response: Record<string, unknown>;
};

/**
 * Decode the base64url fields JSON cannot carry (challenge, allowed
 * credential ids) into the buffers navigator.credentials.get needs —
 * passing the raw strings throws a WebIDL TypeError in browsers.
 */
function decodeAssertionOptions(options: AssertionOptions): PublicKeyCredentialRequestOptions {
  const decoded: Record<string, unknown> = { ...options };
  if (typeof decoded.challenge === "string") {
    decoded.challenge = fromBase64Url(decoded.challenge);
  }
  const allowed = decoded.allowCredentials as
    ({ id?: unknown } & Record<string, unknown>)[] | undefined;
  if (Array.isArray(allowed)) {
    decoded.allowCredentials = allowed.map((entry) =>
      typeof entry?.id === "string" ? { ...entry, id: fromBase64Url(entry.id) } : entry,
    );
  }
  return decoded as unknown as PublicKeyCredentialRequestOptions;
}

/** Map a WebAuthn assertion onto a JSON-safe payload for the bridge. */
function serializeCredential(credential: SerializableCredential): Record<string, unknown> {
  const response: Record<string, unknown> = {};
  for (const [key, value] of Object.entries(credential.response)) {
    response[key] =
      value instanceof ArrayBuffer || ArrayBuffer.isView(value)
        ? toBase64Url(value as ArrayBuffer | Uint8Array)
        : value;
  }
  return {
    id: credential.id,
    rawId: toBase64Url(credential.rawId),
    type: credential.type,
    response,
  };
}

type Props = {
  routes?: {
    options: string | { url: string };
    submit: string | { url: string };
  };
  label?: string;
  loadingLabel?: string;
  separator?: string;
};

export default function PasskeyVerify({ routes, label, loadingLabel, separator }: Props = {}) {
  const optionsUrl = routes ? toUrl(routes.options) : DEFAULT_OPTIONS_URL;
  const submitUrl = routes ? toUrl(routes.submit) : DEFAULT_SUBMIT_URL;

  const [error, setError] = useState<string | null>(null);
  const [isVerifying, setIsVerifying] = useState(false);
  const form = useForm<Record<string, unknown>>({});

  const isSupported = typeof window !== "undefined" && "PublicKeyCredential" in window;

  const verify = useCallback(async () => {
    if (isVerifying) return; // the disabled button guards, this catches races

    setError(null);
    setIsVerifying(true);
    try {
      // 1. Ask the backend for this flow's assertion options.
      const optionsResponse = await fetch(optionsUrl, {
        headers: { Accept: "application/json" },
      });
      if (!optionsResponse.ok) {
        throw new Error(`options request failed with ${optionsResponse.status}`);
      }
      const options = (await optionsResponse.json()) as AssertionOptions;

      // 2. Run the WebAuthn ceremony (authenticator prompt).
      const credential = (await navigator.credentials.get({
        publicKey: decodeAssertionOptions(options),
      })) as SerializableCredential | null;
      if (!credential) {
        throw new Error("assertion was cancelled");
      }

      // 3. Submit the assertion through the bridge and follow the redirect.
      form.transform(() => ({ credential: serializeCredential(credential) }));
      await form.post(submitUrl, {
        onSuccess: (payload) => {
          const redirect = (payload as { redirect?: string } | null)?.redirect;
          router.visit(redirect ?? "/dashboard");
        },
        onError: () => setError(VERIFY_FAILED),
      });
    } catch {
      setError(VERIFY_FAILED);
    } finally {
      setIsVerifying(false);
    }
  }, [form, isVerifying, optionsUrl, submitUrl]);

  if (!isSupported) {
    return null;
  }

  return (
    <>
      <div className="grid gap-2">
        <Button
          type="button"
          variant="outline"
          className="w-full"
          onClick={verify}
          disabled={isVerifying}
        >
          {isVerifying ? <Spinner /> : <KeyRound className="h-4 w-4" />}
          {isVerifying
            ? (loadingLabel ?? "Authenticating...")
            : (label ?? "Sign in with a passkey")}
        </Button>
        {error && <InputError message={error} className="text-center" />}
      </div>

      <div className="relative my-6">
        <div className="absolute inset-0 flex items-center">
          <Separator className="w-full" />
        </div>
        <div className="relative flex justify-center text-xs uppercase">
          <span className="bg-surface text-ink-muted px-2">
            {separator ?? "Or continue with email"}
          </span>
        </div>
      </div>
    </>
  );
}
