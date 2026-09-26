import type { PropsWithChildren } from "react";

import { Form, Head, usePage } from "@fastplace/react";

import InputError from "@/components/input-error";
import PasswordInput from "@/components/password-input";
import TextLink from "@/components/text-link";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Spinner } from "@/components/ui/spinner";
import AuthLayout from "@/layouts/auth-layout";
import type { SharedData } from "@/types";

export default function Register() {
  // The bridge hands page props through the page payload, not component
  // props — read the password rules from shared data with a sane default.
  const { props } = usePage<SharedData & { passwordRules?: string }>();
  const passwordRules = props.passwordRules ?? "minlength: 8;";

  return (
    <>
      <Head title="Register" />
      <Form
        action="/register"
        resetOnSuccess={["password", "password_confirmation"]}
        className="flex flex-col gap-6"
      >
        {({ processing, errors }) => (
          <>
            <div className="grid gap-6">
              <div className="grid gap-2">
                <Label htmlFor="name">Name</Label>
                <Input
                  id="name"
                  type="text"
                  required
                  autoFocus
                  tabIndex={1}
                  autoComplete="name"
                  name="name"
                  placeholder="Full name"
                />
                <InputError message={errors.name?.[0]} className="mt-2" />
              </div>

              <div className="grid gap-2">
                <Label htmlFor="email">Email address</Label>
                <Input
                  id="email"
                  type="email"
                  required
                  tabIndex={2}
                  autoComplete="email"
                  name="email"
                  placeholder="email@example.com"
                />
                <InputError message={errors.email?.[0]} />
              </div>

              <div className="grid gap-2">
                <Label htmlFor="password">Password</Label>
                <PasswordInput
                  id="password"
                  required
                  tabIndex={3}
                  autoComplete="new-password"
                  name="password"
                  placeholder="Password"
                  passwordrules={passwordRules}
                />
                <InputError message={errors.password?.[0]} />
              </div>

              <div className="grid gap-2">
                <Label htmlFor="password_confirmation">Confirm password</Label>
                <PasswordInput
                  id="password_confirmation"
                  required
                  tabIndex={4}
                  autoComplete="new-password"
                  name="password_confirmation"
                  placeholder="Confirm password"
                  passwordrules={passwordRules}
                />
                <InputError message={errors.password_confirmation?.[0]} />
              </div>

              <Button
                type="submit"
                className="mt-2 w-full"
                tabIndex={5}
                disabled={processing}
                data-test="register-user-button"
              >
                {processing && <Spinner />}
                Create account
              </Button>
            </div>

            <div className="text-ink-muted text-center text-sm">
              Already have an account?{" "}
              <TextLink href="/login" tabIndex={6}>
                Log in
              </TextLink>
            </div>
          </>
        )}
      </Form>
    </>
  );
}

const PageLayout = ({ children }: PropsWithChildren) => (
  <AuthLayout
    title="Create an account"
    description="Enter your details below to create your account"
  >
    {children}
  </AuthLayout>
);

Register.layout = PageLayout;
