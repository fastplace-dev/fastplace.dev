import type { PropsWithChildren } from "react";

import { Form, Head, usePage } from "@fastplace/react";

import TextLink from "@/components/text-link";
import { Button } from "@/components/ui/button";
import { Spinner } from "@/components/ui/spinner";
import AuthLayout from "@/layouts/auth-layout";
import type { SharedData } from "@/types";

export default function VerifyEmail() {
  const { status } = usePage<SharedData & { status?: string }>().props;

  return (
    <>
      <Head title="Email verification" />

      {status === "verification-link-sent" && (
        <div role="status" className="text-success mb-4 text-center text-sm font-medium">
          A new verification link has been sent to the email address you provided during
          registration.
        </div>
      )}

      <Form
        action="/email/verification-notification"
        method="post"
        className="space-y-6 text-center"
      >
        {({ processing }) => (
          <>
            <Button disabled={processing} variant="secondary">
              {processing && <Spinner />}
              Resend verification email
            </Button>

            <TextLink href="/logout" method="POST" className="mx-auto block text-sm">
              Log out
            </TextLink>
          </>
        )}
      </Form>
    </>
  );
}

VerifyEmail.layout = ({ children }: PropsWithChildren) => (
  <AuthLayout
    title="Email verification"
    description="Please verify your email address by clicking on the link we just emailed to you."
  >
    {children}
  </AuthLayout>
);
