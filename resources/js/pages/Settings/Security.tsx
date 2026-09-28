import { Form, Head, usePage, type PageLayout } from "@fastplace/react";
import { useRef } from "react";
import type { PropsWithChildren } from "react";

import Heading from "@/components/heading";
import InputError from "@/components/input-error";
import ManagePasskeys, { type Props as ManagePasskeysProps } from "@/components/manage-passkeys";
import ManageTwoFactor, {
  type Props as ManageTwoFactorProps,
} from "@/components/manage-two-factor";
import PasswordInput from "@/components/password-input";
import { Button } from "@/components/ui/button";
import { Label } from "@/components/ui/label";
import AppSidebarLayout from "@/layouts/app/app-sidebar-layout";
import SettingsLayout from "@/layouts/settings/layout";

// The bridge accepts PUT directly — no form-method spoofing needed.
const UPDATE_PASSWORD_URL = "/settings/password";

type Props = {
  passwordRules: string;
} & ManagePasskeysProps &
  ManageTwoFactorProps;

function SecurityPage() {
  // Bridge props arrive via the page payload, not component arguments.
  const props = usePage<Props>().props;
  const passwordInput = useRef<HTMLInputElement>(null);
  const currentPasswordInput = useRef<HTMLInputElement>(null);

  return (
    <>
      <Head title="Security settings" />

      <h1 className="sr-only">Security settings</h1>

      <div className="space-y-6">
        <Heading
          variant="small"
          title="Update password"
          description="Ensure your account is using a long, random password to stay secure"
        />

        <Form
          action={UPDATE_PASSWORD_URL}
          method="put"
          resetOnSuccess={["current_password", "password", "password_confirmation"]}
          onError={(errors) => {
            if (errors.password) {
              passwordInput.current?.focus();
            }

            if (errors.current_password) {
              currentPasswordInput.current?.focus();
            }
          }}
          className="space-y-6"
        >
          {({ errors, processing }) => (
            <>
              <div className="grid gap-2">
                <Label htmlFor="current_password">Current password</Label>

                <PasswordInput
                  id="current_password"
                  ref={currentPasswordInput}
                  name="current_password"
                  className="mt-1 block w-full"
                  autoComplete="current-password"
                  placeholder="Current password"
                  aria-describedby={errors.current_password ? "current_password-error" : undefined}
                  aria-invalid={errors.current_password ? true : undefined}
                />

                <InputError id="current_password-error" message={errors.current_password?.[0]} />
              </div>

              <div className="grid gap-2">
                <Label htmlFor="password">New password</Label>

                <PasswordInput
                  id="password"
                  ref={passwordInput}
                  name="password"
                  className="mt-1 block w-full"
                  autoComplete="new-password"
                  placeholder="New password"
                  aria-describedby={errors.password ? "password-error" : undefined}
                  aria-invalid={errors.password ? true : undefined}
                  {...{ passwordrules: props.passwordRules }}
                />

                <InputError id="password-error" message={errors.password?.[0]} />
              </div>

              <div className="grid gap-2">
                <Label htmlFor="password_confirmation">Confirm password</Label>

                <PasswordInput
                  id="password_confirmation"
                  name="password_confirmation"
                  className="mt-1 block w-full"
                  autoComplete="new-password"
                  placeholder="Confirm password"
                  aria-describedby={
                    errors.password_confirmation ? "password_confirmation-error" : undefined
                  }
                  aria-invalid={errors.password_confirmation ? true : undefined}
                  {...{ passwordrules: props.passwordRules }}
                />

                <InputError
                  id="password_confirmation-error"
                  message={errors.password_confirmation?.[0]}
                />
              </div>

              <div className="flex items-center gap-4">
                <Button disabled={processing} data-test="update-password-button">
                  Save
                </Button>
              </div>
            </>
          )}
        </Form>
      </div>

      <ManageTwoFactor
        canManageTwoFactor={props.canManageTwoFactor}
        requiresConfirmation={props.requiresConfirmation}
        twoFactorEnabled={props.twoFactorEnabled}
      />

      <ManagePasskeys canManagePasskeys={props.canManagePasskeys} passkeys={props.passkeys} />
    </>
  );
}

const SettingsPageLayout = ({ children }: PropsWithChildren) => (
  <AppSidebarLayout breadcrumbs={[{ title: "Security settings", href: "/settings/security" }]}>
    {children}
  </AppSidebarLayout>
);

// Settings chrome: the sidebar shell outside, the settings section nav inside.
export default Object.assign(SecurityPage, {
  layout: [SettingsPageLayout, SettingsLayout] satisfies PageLayout,
});
