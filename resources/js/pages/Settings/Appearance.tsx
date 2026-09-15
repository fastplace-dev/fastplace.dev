import { Head, type PageLayout } from "@fastplace/react";
import type { PropsWithChildren } from "react";

import AppearanceTabs from "@/components/appearance-tabs";
import Heading from "@/components/heading";
import AppSidebarLayout from "@/layouts/app/app-sidebar-layout";
import SettingsLayout from "@/layouts/settings/layout";

function AppearancePage() {
  return (
    <>
      <Head title="Appearance settings" />

      <h1 className="sr-only">Appearance settings</h1>

      <div className="space-y-6">
        <Heading
          variant="small"
          title="Appearance settings"
          description="Update the appearance settings for your account"
        />
        <AppearanceTabs />
      </div>
    </>
  );
}

const SettingsPageLayout = ({ children }: PropsWithChildren) => (
  <AppSidebarLayout breadcrumbs={[{ title: "Appearance settings", href: "/settings/appearance" }]}>
    {children}
  </AppSidebarLayout>
);

// Settings chrome: the sidebar shell outside, the settings section nav inside.
export default Object.assign(AppearancePage, {
  layout: [SettingsPageLayout, SettingsLayout] satisfies PageLayout,
});
