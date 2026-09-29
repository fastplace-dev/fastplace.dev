import React, { useState } from "react";
import { Form, useBroadcast, usePage, usePresence } from "@fastplace/react";
import { Card, CardContent } from "../../components/ui/card";
import { Button } from "../../components/ui/button";
import AppLayout from "@/layouts/app-layout";

/**
 * Broadcasting demo — a live /ws/broadcast page. The public channel carries
 * server-pushed messages (the publish button round-trips through the
 * controller and the frame arrives on this very socket); the presence
 * channel mirrors its roster as pages join and leave.
 */
export default function BroadcastIndex() {
  const { props } = usePage();
  const channel = props.channel ?? "demo.broadcast";
  const presenceName = props.presence_name ?? "demo";

  const [lastMessage, setLastMessage] = useState(null);
  const [published, setPublished] = useState(false);
  const status = useBroadcast(channel, (message) => {
    setLastMessage(message.payload?.message ?? JSON.stringify(message.payload));
    setPublished(false);
  });
  const members = usePresence(presenceName);

  return (
    <div>
      <div className="mx-auto max-w-4xl px-6 py-10">
        <header className="mb-8 flex items-baseline justify-between">
          <h1 className="text-2xl font-semibold">Broadcasting</h1>
          <span
            data-test="ws-status"
            className={`text-sm ${status === "open" ? "text-success" : "text-muted-foreground"}`}
          >
            socket: {status}
          </span>
        </header>

        <Card className="mb-6 py-6">
          <CardContent className="px-4">
            <h2 className="mb-2 font-medium">Server push</h2>
            <p className="text-muted-foreground mb-4 text-sm">
              Publishing goes through the controller and the frame arrives on the socket this page
              already holds — no reload, no refetch.
            </p>
            <Form
              action="/broadcast/publish"
              method="post"
              onSuccess={() => setPublished(true)}
              className="flex items-center gap-3"
            >
              {({ processing }) => (
                <>
                  <Button type="submit" disabled={processing}>
                    Publish demo message
                  </Button>
                  {published && (
                    <span className="text-muted-foreground text-sm">
                      published — watch the wire
                    </span>
                  )}
                </>
              )}
            </Form>
            <p data-test="last-message" className="text mt-4 text-sm" aria-live="polite">
              {lastMessage ?? "Nothing received yet."}
            </p>
          </CardContent>
        </Card>

        <Card className="py-6">
          <CardContent className="px-4">
            <h2 className="mb-2 font-medium">
              Presence — <span data-test="member-count">{members.length}</span>{" "}
              {members.length === 1 ? "member" : "members"}
            </h2>
            <p className="text-muted-foreground mb-4 text-sm">
              Everyone on this page&rsquo;s presence channel; open it in another browser to watch
              the roster grow.
            </p>
            <ul className="text-sm">
              {members.map((member) => (
                <li key={String(member.user_id)} className="py-0.5">
                  user {String(member.user_id)}
                  {member.connections > 1 && (
                    <span className="text-muted-foreground">
                      {" "}
                      ({member.connections} connections)
                    </span>
                  )}
                </li>
              ))}
            </ul>
          </CardContent>
        </Card>
      </div>
    </div>
  );
}

// Persistent-layout opt-in — the bridge reads this static on the component.
BroadcastIndex.layout = AppLayout;
