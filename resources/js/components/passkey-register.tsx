import { useCallback, useState } from "react";

import { useForm } from "@fastplace/react";
import { fromBase64Url, toBase64Url } from "@/lib/base64url";

import InputError from "@/components/input-error";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";

// Bridge endpoints backing the passkey registration ceremony.
const PASSKEY_OPTIONS_URL = "/user/passkeys/options";
const PASSKEY_STORE_URL = "/user/passkeys";

const REGISTRATION_FAILED = "Unable to register this passkey. Please try again.";

/** Creation options as the backend ships them (JSON, challenge included). */
type RegistrationOptions = Record<string, unknown>;

/** The parts of a WebAuthn credential this flow needs to serialize. */
type SerializableCredential = {
  id: string;
  rawId: ArrayBuffer;
  type: string;
  response: Record<string, unknown>;
  clientExtensionResults?: Record<string, unknown>;
  authenticatorAttachment?: string;
};

/**
 * Decode the base64url fields JSON cannot carry (challenge, user id,
 * excluded credential ids) into the buffers navigator.credentials.create
 * needs — passing the raw strings throws a WebIDL TypeError in browsers.
 */
function decodeCreationOptions(options: RegistrationOptions): PublicKeyCredentialCreationOptions {
  const decoded: Record<string, unknown> = { ...options };
  if (typeof decoded.challenge === "string") {
    decoded.challenge = fromBase64Url(decoded.challenge);
  }
  const user = decoded.user as { id?: unknown } | undefined;
  if (user && typeof user.id === "string") {
    decoded.user = { ...user, id: fromBase64Url(user.id) };
  }
  const excluded = decoded.excludeCredentials as
    ({ id?: unknown } & Record<string, unknown>)[] | undefined;
  if (Array.isArray(excluded)) {
    decoded.excludeCredentials = excluded.map((entry) =>
      typeof entry?.id === "string" ? { ...entry, id: fromBase64Url(entry.id) } : entry,
    );
  }
  return decoded as unknown as PublicKeyCredentialCreationOptions;
}

/** Map a WebAuthn credential onto a JSON-safe payload for the bridge. */
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
    ...(credential.clientExtensionResults !== undefined && {
      clientExtensionResults: credential.clientExtensionResults,
    }),
    ...(credential.authenticatorAttachment !== undefined && {
      authenticatorAttachment: credential.authenticatorAttachment,
    }),
  };
}

/** Guess a friendly default name ("Chrome on Mac") from the user agent. */
function defaultPasskeyName(): string {
  if (typeof navigator === "undefined") return "";

  const ua = navigator.userAgent;

  const browser = [
    { pattern: /Edg|Edge/, name: "Edge" },
    { pattern: /OPR|Opera|OPiOS/, name: "Opera" },
    { pattern: /Firefox|FxiOS/, name: "Firefox" },
    { pattern: /Chrome|CriOS/, name: "Chrome" },
    { pattern: /Safari/, name: "Safari" },
  ].find(({ pattern }) => pattern.test(ua))?.name;

  const os = [
    { pattern: /iPhone/, name: "iPhone" },
    { pattern: /iPad|Macintosh(?=.*Mobile)/, name: "iPad" },
    { pattern: /Android/, name: "Android" },
    { pattern: /Mac/, name: "Mac" },
    { pattern: /Windows/, name: "Windows" },
  ].find(({ pattern }) => pattern.test(ua))?.name;

  return [browser, os].filter(Boolean).join(" on ") || "";
}

type Props = {
  onSuccess: () => void;
};

export default function PasskeyRegistration({ onSuccess }: Props) {
  const [name, setName] = useState(defaultPasskeyName);
  const [showForm, setShowForm] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [isRegistering, setIsRegistering] = useState(false);
  const form = useForm<Record<string, unknown>>({});

  const isSupported = typeof window !== "undefined" && "PublicKeyCredential" in window;

  const register = useCallback(
    async (passkeyName: string) => {
      setError(null);
      setIsRegistering(true);
      try {
        // 1. Ask the backend for this user's creation options.
        const optionsResponse = await fetch(PASSKEY_OPTIONS_URL, {
          headers: { Accept: "application/json" },
        });
        if (!optionsResponse.ok) {
          throw new Error(`options request failed with ${optionsResponse.status}`);
        }
        const options = (await optionsResponse.json()) as RegistrationOptions;

        // 2. Run the WebAuthn ceremony (authenticator prompt).
        const credential = (await navigator.credentials.create({
          publicKey: decodeCreationOptions(options),
        })) as SerializableCredential | null;
        if (!credential) {
          throw new Error("credential creation was cancelled");
        }

        // 3. Store the new credential through the bridge.
        form.transform(() => ({
          name: passkeyName,
          credential: serializeCredential(credential),
        }));
        await form.post(PASSKEY_STORE_URL, {
          onSuccess: () => {
            setName("");
            setShowForm(false);
            onSuccess();
          },
          onError: (errors) => {
            const first = Object.values(errors)[0]?.[0];
            setError(first ?? REGISTRATION_FAILED);
          },
        });
      } catch {
        setError(REGISTRATION_FAILED);
      } finally {
        setIsRegistering(false);
      }
    },
    [form, onSuccess],
  );

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();

    if (!name.trim()) {
      return;
    }

    await register(name);
  };

  const handleCancel = () => {
    setShowForm(false);
    setName("");
  };

  if (!isSupported) {
    return (
      <div className="text-ink-muted text-sm">Passkeys are not supported in this browser.</div>
    );
  }

  if (!showForm) {
    return (
      <Button variant="outline" onClick={() => setShowForm(true)}>
        Add passkey
      </Button>
    );
  }

  return (
    <form
      onSubmit={handleSubmit}
      className="border-line bg-muted/50 space-y-4 rounded-lg border p-4"
    >
      <div className="grid gap-2">
        <Label htmlFor="passkey-name">Passkey name</Label>
        <Input
          id="passkey-name"
          type="text"
          value={name}
          onChange={(e: React.ChangeEvent<HTMLInputElement>) => setName(e.target.value)}
          placeholder="e.g., MacBook Pro, iPhone"
          className="border-ink/20 mt-1 block w-full"
          autoFocus
        />
        <p className="text-ink-muted text-xs">A name helps you identify this passkey later.</p>
      </div>

      {error && <InputError message={error} />}

      <div className="flex gap-2">
        <Button type="submit" disabled={isRegistering || !name.trim()}>
          {isRegistering ? "Registering..." : "Register passkey"}
        </Button>
        <Button type="button" variant="ghost" onClick={handleCancel}>
          Cancel
        </Button>
      </div>
    </form>
  );
}
