import { router, useForm } from "@fastplace/react";
import { KeyRound } from "lucide-react";

import Heading from "@/components/heading";
import PasskeyItem from "@/components/passkey-item";
import PasskeyRegistration from "@/components/passkey-register";
import type { Passkey } from "@/types/auth";

export type Props = {
  canManagePasskeys?: boolean;
  passkeys?: Passkey[];
};

const EmptyState = () => {
  return (
    <div className="p-8 text-center">
      <div className="bg-muted mx-auto mb-4 flex h-14 w-14 items-center justify-center rounded-2xl">
        <KeyRound className="text-ink-muted h-7 w-7" />
      </div>
      <p className="font-medium">No passkeys yet</p>
      <p className="text-ink-muted mt-1 text-sm">Add a passkey to sign in without a password</p>
    </div>
  );
};

export default function ManagePasskeys(props: Props) {
  const passkeys = props.passkeys ?? [];
  const form = useForm<Record<string, unknown>>({});

  const handleDelete = (id: number, onError: () => void) => {
    void form.delete(`/user/passkeys/${String(id)}`, {
      onError: () => onError(),
    });
  };

  const handleRegisterSuccess = () => {
    // The bridge has no reload() — re-visiting the current page URL refetches
    // the passkeys list.
    void router.visit(router.page?.url ?? "/settings/security");
  };

  if (!(props.canManagePasskeys ?? false)) {
    return null;
  }

  return (
    <div className="space-y-6">
      <Heading
        variant="small"
        title="Passkeys"
        description="Manage your passkeys for passwordless sign-in"
      />

      <div className="border-line overflow-hidden rounded-lg border">
        {passkeys.length > 0 ? (
          passkeys.map((passkey) => (
            <PasskeyItem key={passkey.id} passkey={passkey} onDelete={handleDelete} />
          ))
        ) : (
          <EmptyState />
        )}
      </div>

      <PasskeyRegistration onSuccess={handleRegisterSuccess} />
    </div>
  );
}
