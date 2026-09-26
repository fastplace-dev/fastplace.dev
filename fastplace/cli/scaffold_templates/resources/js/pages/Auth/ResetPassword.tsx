import { Form, Head, usePage } from "@fastplace/react";
import type { PropsWithChildren } from "react";

import InputError from "@/components/input-error";
import PasswordInput from "@/components/password-input";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Spinner } from "@/components/ui/spinner";
import AuthLayout from "@/layouts/auth-layout";
import type { SharedData } from "@/types";

export default function ResetPassword() {
  // The bridge hands page props through the page payload, not component
  // props — token/email arrive from the reset-password email link.
  const { token, email, passwordRules } = usePage<
    SharedData & { token?: string; email?: string; passwordRules?: string }
  >().props;
  const rules = passwordRules ?? "minlength: 8;";

  return (
    <>
      <Head title="Reset password" />

      {/* The token and email ride in the body alongside the typed
          passwords, matching the server contract for the reset endpoint. */}
      <Form
        action="/reset-password"
        resetOnSuccess={["password", "password_confirmation"]}
        data-slot="form"
      >
        {({ processing, errors }) => (
          <div className="grid gap-6">
            <input type="hidden" name="token" defaultValue={token} />

            <div className="grid gap-2">
              <Label htmlFor="email">Email</Label>
              <Input
                id="email"
                type="email"
                name="email"
                autoComplete="email"
                defaultValue={email}
                className="mt-1 block w-full"
                readOnly
              />
              <InputError message={errors.email?.[0]} className="mt-2" />
            </div>

            <div className="grid gap-2">
              <Label htmlFor="password">Password</Label>
              <PasswordInput
                id="password"
                name="password"
                autoComplete="new-password"
                className="mt-1 block w-full"
                autoFocus
                placeholder="Password"
                passwordrules={rules}
              />
              <InputError message={errors.password?.[0]} />
            </div>

            <div className="grid gap-2">
              <Label htmlFor="password_confirmation">Confirm password</Label>
              <PasswordInput
                id="password_confirmation"
                name="password_confirmation"
                autoComplete="new-password"
                className="mt-1 block w-full"
                placeholder="Confirm password"
                passwordrules={rules}
              />
              <InputError message={errors.password_confirmation?.[0]} />
            </div>

            <Button
              type="submit"
              className="mt-4 w-full"
              disabled={processing}
              data-test="reset-password-button"
            >
              {processing && <Spinner />}
              Reset password
            </Button>
          </div>
        )}
      </Form>
    </>
  );
}

const PageLayout = ({ children }: PropsWithChildren) => (
  <AuthLayout title="Reset password" description="Please enter your new password below">
    {children}
  </AuthLayout>
);

ResetPassword.layout = PageLayout;
