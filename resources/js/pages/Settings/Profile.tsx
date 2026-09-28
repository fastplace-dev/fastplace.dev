import { Form, Head, Link, usePage, type PageLayout } from "@fastplace/react";
import type { PropsWithChildren } from "react";

import DeleteUser from "@/components/delete-user";
import Heading from "@/components/heading";
import InputError from "@/components/input-error";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import AppSidebarLayout from "@/layouts/app/app-sidebar-layout";
import SettingsLayout from "@/layouts/settings/layout";
import type { SharedData } from "@/types";

function ProfilePage({ mustVerifyEmail }: { mustVerifyEmail: boolean }) {
  // auth is absent until an auth backend exists — degrade to empty fields.
  const { auth, status } = usePage<SharedData & { status?: string }>().props;
  const user = auth?.user;

  return (
    <>
      <Head title="Profile settings" />

      <h1 className="sr-only">Profile settings</h1>

      <div className="space-y-6">
        <Heading variant="small" title="Profile" description="Update your name and email address" />

        <Form action="/settings/profile" method="patch" className="space-y-6">
          {({ processing, errors }) => (
            <>
              <div className="grid gap-2">
                <Label htmlFor="name">Name</Label>

                <Input
                  id="name"
                  className="mt-1 block w-full"
                  defaultValue={user?.name ?? ""}
                  name="name"
                  required
                  autoComplete="name"
                  placeholder="Full name"
                  aria-describedby={errors.name ? "name-error" : undefined}
                  aria-invalid={errors.name ? true : undefined}
                />

                <InputError id="name-error" className="mt-2" message={errors.name?.[0]} />
              </div>

              <div className="grid gap-2">
                <Label htmlFor="email">Email address</Label>

                <Input
                  id="email"
                  type="email"
                  className="mt-1 block w-full"
                  defaultValue={user?.email ?? ""}
                  name="email"
                  required
                  autoComplete="username"
                  placeholder="Email address"
                  aria-describedby={errors.email ? "email-error" : undefined}
                  aria-invalid={errors.email ? true : undefined}
                />

                <InputError id="email-error" className="mt-2" message={errors.email?.[0]} />
              </div>

              {mustVerifyEmail && user?.email_verified_at === null && (
                <div>
                  <p className="text-ink-muted -mt-4 text-sm">
                    Your email address is unverified.{" "}
                    <Link
                      href="/email/verification-notification"
                      method="POST"
                      className="text-ink underline decoration-line underline-offset-4 transition-colors duration-300 ease-out hover:decoration-current!"
                    >
                      Click here to re-send the verification email.
                    </Link>
                  </p>

                  {status === "verification-link-sent" && (
                    <div role="status" className="text-success mt-2 text-sm font-medium">
                      A new verification link has been sent to your email address.
                    </div>
                  )}
                </div>
              )}

              <div className="flex items-center gap-4">
                <Button disabled={processing} data-test="update-profile-button">
                  Save
                </Button>
              </div>
            </>
          )}
        </Form>
      </div>

      <DeleteUser />
    </>
  );
}

const SettingsPageLayout = ({ children }: PropsWithChildren) => (
  <AppSidebarLayout breadcrumbs={[{ title: "Profile settings", href: "/settings/profile" }]}>
    {children}
  </AppSidebarLayout>
);

// Settings chrome: the sidebar shell outside, the settings section nav inside.
export default Object.assign(ProfilePage, {
  layout: [SettingsPageLayout, SettingsLayout] satisfies PageLayout,
});
