"""Broadcasting demo — a live /ws/broadcast page for the sample app.

The page subscribes to a public channel plus a presence channel and offers
a publish button. The publish POST answers plain JSON (not a bridge page
swap), so the demo page never reloads — its open socket is exactly what
receives the pushed frame. That is the whole point of the demo: the
message visibly arrives over the wire the page already holds.
"""

from __future__ import annotations

from fastplace.authz import gate
from fastplace.broadcasting import broadcast
from fastplace.http import Controller, Json, Request, render

#: Fixed names keep the demo predictable; the page receives both as props.
DEMO_CHANNEL = "demo.broadcast"
DEMO_PRESENCE = "demo"


@gate.define("view-broadcast")
async def _view_broadcast(user, channel) -> bool:
    """Demo policy for private/presence channels: any authenticated user.

    Production apps decide per parsed channel here — the sample grants the
    whole authenticated surface so the demo stays about the plumbing.
    """
    return user is not None


class BroadcastDemoController(Controller):
    async def index(self, request: Request):
        return render(
            request,
            component="Broadcast/Index",
            props={"channel": DEMO_CHANNEL, "presence_name": DEMO_PRESENCE},
            title="Broadcasting",
            robots="noindex",
        )

    async def publish(self, request: Request):
        user = request.scope.get("fastplace_user")
        name = getattr(user, "name", None) or "the server"
        await broadcast(DEMO_CHANNEL, {"message": "Server push works", "user": str(name)})
        # Plain JSON — a bridge page swap would remount the page (and drop
        # its socket) right before the frame lands.
        return Json({"published": DEMO_CHANNEL})
