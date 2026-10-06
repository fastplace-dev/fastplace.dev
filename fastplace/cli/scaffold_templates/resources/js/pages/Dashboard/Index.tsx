import { Head } from "@fastplace/react";

import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import AppLayout from "@/layouts/app-layout";

export default function Dashboard() {
  return (
    <>
      <Head title="Dashboard" />
      <div className="px-4 py-6">
        <Card>
          <CardHeader>
            <CardTitle>Your dashboard is ready</CardTitle>
          </CardHeader>
          <CardContent className="text-muted-foreground">
            Start building — this page is yours. The first account you registered is the admin of
            this application.
          </CardContent>
        </Card>
      </div>
    </>
  );
}

// The persistent application shell (sidebar + header) wraps this page.
Dashboard.layout = AppLayout;
