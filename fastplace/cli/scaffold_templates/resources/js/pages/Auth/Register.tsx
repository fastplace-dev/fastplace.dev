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

                  autoComplete="name"
                  name="name"
                  placeholder="Full name"
                  aria-describedby={errors.name ? "name-error" : undefined}
                  aria-invalid={errors.name ? true : undefined}
                />
                <InputError id="name-error" message={errors.name?.[0]} className="mt-2" />
              </div>

              <div className="grid gap-2">
                <Label htmlFor="email">Email address</Label>
                <Input
                  id="email"
                  type="email"
                  required

                  autoComplete="email"
                  name="email"
                  placeholder="email@example.com"
                  aria-describedby={errors.email ? "email-error" : undefined}
                  aria-invalid={errors.email ? true : undefined}
                />
                <InputError id="email-error" message={errors.email?.[0]} />
              </div>

              <div className="grid gap-2">
                <Label htmlFor="password">Password</Label>
                <PasswordInput
                  id="password"
                  required

                  autoComplete="new-password"
                  name="password"
                  placeholder="Password"
                  passwordrules={passwordRules}
                  aria-describedby={errors.password ? "password-error" : undefined}
                  aria-invalid={errors.password ? true : undefined}
                />
                <InputError id="password-error" message={errors.password?.[0]} />
              </div>

              <div className="grid gap-2">
                <Label htmlFor="password_confirmation">Confirm password</Label>
                <PasswordInput
                  id="password_confirmation"
                  required

                  autoComplete="new-password"
                  name="password_confirmation"
                  placeholder="Confirm password"
                  passwordrules={passwordRules}
                  aria-describedby={
                    errors.password_confirmation ? "password_confirmation-error" : undefined
                  }
                  aria-invalid={errors.password_confirmation ? true : undefined}
                />
                <InputError
                  id="password_confirmation-error"
                  message={errors.password_confirmation?.[0]}
                />
              </div>

              <Button
                type="submit"
                className="mt-2 w-full"

                disabled={processing}
                data-test="register-user-button"
              >
                {processing && <Spinner />}
                Create account
              </Button>
            </div>

            <div className="text-ink-muted text-center text-sm">
              Already have an account? <TextLink href="/login">Log in</TextLink>
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
