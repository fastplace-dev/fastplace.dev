import type { PropsWithChildren } from "react";

import { Form, Head, usePage } from "@fastplace/react";

import InputError from "@/components/input-error";
import PasskeyVerify from "@/components/passkey-verify";
import PasswordInput from "@/components/password-input";
import TextLink from "@/components/text-link";
import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Spinner } from "@/components/ui/spinner";
import AuthLayout from "@/layouts/auth-layout";
import type { SharedData } from "@/types";

export default function Login() {
  // The bridge hands page props through the page payload, not component
  // props — fall back to allowing password resets until a backend says
  // otherwise.
  const { status, canResetPassword } = usePage<
    SharedData & { status?: string; canResetPassword?: boolean }
  >().props;
  const mayReset = canResetPassword ?? true;

  return (
    <>
      <Head title="Log in" />

      <PasskeyVerify />

      <Form action="/login" resetOnSuccess={["password"]} className="flex flex-col gap-6">
        {({ processing, errors }) => (
          <>
            <div className="grid gap-6">
              <div className="grid gap-2">
                <Label htmlFor="email">Email address</Label>
                <Input
                  id="email"
                  type="email"
                  name="email"
                  required
                  autoFocus

                  autoComplete="email"
                  placeholder="email@example.com"
                  aria-describedby={errors.email ? "email-error" : undefined}
                  aria-invalid={errors.email ? true : undefined}
                />
                <InputError id="email-error" message={errors.email?.[0]} />
              </div>

              <div className="grid gap-2">
                <div className="flex items-center">
                  <Label htmlFor="password">Password</Label>
                  {mayReset && (
                    <TextLink href="/forgot-password" className="ml-auto text-sm">
                      Forgot your password?
                    </TextLink>
                  )}
                </div>
                <PasswordInput
                  id="password"
                  name="password"
                  required

                  autoComplete="current-password"
                  placeholder="Password"
                  aria-describedby={errors.password ? "password-error" : undefined}
                  aria-invalid={errors.password ? true : undefined}
                />
                <InputError id="password-error" message={errors.password?.[0]} />
              </div>

              <div className="flex items-center space-x-3">
                <Checkbox id="remember" name="remember" />
                <Label htmlFor="remember">Remember me</Label>
              </div>

              <Button
                type="submit"
                className="mt-4 w-full"

                disabled={processing}
                data-test="login-button"
              >
                {processing && <Spinner />}
                Log in
              </Button>
            </div>

            <div className="text-ink-muted text-center text-sm">
              Don't have an account? <TextLink href="/register">Sign up</TextLink>
            </div>
          </>
        )}
      </Form>

      {status && (
        <div role="status" className="text-success mb-4 text-center text-sm font-medium">
          {status}
        </div>
      )}
    </>
  );
}

const PageLayout = ({ children }: PropsWithChildren) => (
  <AuthLayout
    title="Log in to your account"
    description="Enter your email and password below to log in"
  >
    {children}
  </AuthLayout>
);

Login.layout = PageLayout;
