import { Form, Head, usePage } from "@fastplace/react";
import { LoaderCircle } from "lucide-react";
import type { PropsWithChildren } from "react";

import InputError from "@/components/input-error";
import TextLink from "@/components/text-link";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import AuthLayout from "@/layouts/auth-layout";
import type { SharedData } from "@/types";

export default function ForgotPassword() {
  const { status } = usePage<SharedData & { status?: string }>().props;

  return (
    <>
      <Head title="Forgot password" />

      {status && (
        <div role="status" className="text-success mb-4 text-center text-sm font-medium">
          {status}
        </div>
      )}

      <div className="space-y-6">
        <Form action="/forgot-password" method="post">
          {({ processing, errors }) => (
            <>
              <div className="grid gap-2">
                <Label htmlFor="email">Email address</Label>
                <Input
                  id="email"
                  type="email"
                  name="email"
                  autoComplete="off"
                  autoFocus
                  placeholder="email@example.com"
                  aria-describedby={errors.email ? "email-error" : undefined}
                  aria-invalid={errors.email ? true : undefined}
                />

                <InputError id="email-error" message={errors.email?.[0]} />
              </div>

              <div className="my-6 flex items-center justify-start">
                <Button
                  className="w-full"
                  disabled={processing}
                  data-test="email-password-reset-link-button"
                >
                  {processing && <LoaderCircle className="h-4 w-4 animate-spin" />}
                  Email password reset link
                </Button>
              </div>
            </>
          )}
        </Form>

        <div className="text-ink-muted space-x-1 text-center text-sm">
          <span>Or, return to</span>
          <TextLink href="/login">log in</TextLink>
        </div>
      </div>
    </>
  );
}

const PageLayout = ({ children }: PropsWithChildren) => (
  <AuthLayout
    title="Forgot password"
    description="Enter your email to receive a password reset link"
  >
    {children}
  </AuthLayout>
);

ForgotPassword.layout = PageLayout;
