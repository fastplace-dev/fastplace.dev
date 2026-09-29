# Mail & notifications

Mail ships with three drivers (`MAIL_DRIVER`: `log`, `memory`, `smtp` —
`config/mail.py` is the canonical list) and one message type. Notifications
sit one level above: a declarative class that fans a single event out to
named channels.

## Sending mail

```python
from fastplace.mail import Mail, MailMessage

await Mail.to("user@example.com").send(
    MailMessage(subject="Welcome", text="Your account is ready.", to="user@example.com")
)
# Mail.to() overrides the message's to — the address on Mail wins.

# html + text pair, sender identity from MAIL_FROM_NAME/MAIL_FROM_ADDRESS
msg = MailMessage(
    subject="Invoice",
    text="Plain fallback.",
    html="<p>Invoice <b>#42</b></p>",
)
await Mail.deliver(msg)
```

Under an `smtp` driver with the queue installed, `Mail.send` enqueues
delivery and returns immediately (`deliver` bypasses that and sends
inline) — see [Background jobs & cache](/guides/background-and-cache).
The `log` driver writes one JSON line per message, which is what local
development (and `fastplace mail:resend`) reads.

## Rich messages

`MailMessage` carries `cc`, `bcc`, `reply_to`, and `attachments` —
chain the builders:

```python
msg = (
    MailMessage(subject="Order shipped", text="Order #42 is on its way.", to="user@example.com")
    .add_cc("sales@example.com")
    .add_bcc("audit@example.com")
    .set_reply_to("support@example.com")
    .attach(path="/tmp/label-42.pdf")  # or content=b"...", filename="label.pdf"
)
```

- `attach()` takes exactly one of `path=` / `content=`; `mime=` overrides
  the guessed content type.
- Queue payloads round-trip attachments as base64 — a queued message
  arrives byte-identical at the worker. `path=` attachments are resolved
  at send time, so the file must exist on the worker; use `content=` for
  anything not on shared storage.
- The log driver records attachment names and sizes only, never the
  bytes.

## Rendered mailables

A `Mailable` is a code-first template email that renders **into** a
`MailMessage` — the dataclass stays the queue boundary, so nothing about
queueing changes. `Mail.to(...).send(...)` accepts either:

```python
from fastplace.mail import Layout, Mail, Mailable

welcome = Mailable(
    subject="Welcome, {name}!",
    html="<h1>Hello {name}</h1><p>Your order {order} has shipped.</p>",
    placeholders={"name": user.name, "order": order.number},  # user data
).layout(Layout(html="<html><body>{body}<footer>ACME Shop</footer></body></html>"))
await Mail.to(user.email).send(welcome)
```

Rendering rules, all deliberate:

- **Values are escaped, not trusted.** Every placeholder value is
  `html.escape`d before it touches HTML — user data can never inject markup.
  Subject lines substitute raw (a subject is not HTML; entities would read
  literally). Pass text, not HTML, as placeholder values.
- **Unknown names fail loud.** A template referencing a placeholder nobody
  supplied raises `ValueError` at render time, not a silently-empty email.
- **The text body falls back.** Without explicit `text=`, a best-effort
  plain-text version is stripped from the final HTML (tags removed,
  entities decoded, block boundaries becoming newlines). Pass explicit
  `text=` when the wording matters.
- **Layouts wrap, they don't re-render.** A `Layout` needs a `{body}` slot
  (validated at construction); the rendered body is inserted already
  escaped, the layout's own slots are escaped. Layouts are where
  email-safe *inline* styles belong — email clients ignore `<style>` blocks
  and CSS custom properties, so use table-based markup with inline
  `style="..."` attributes and light-only colors (the repo's dark-mode
  tokens cannot apply inside email clients).
- **Rendering happens before the queue decision.** A queued mailable's
  payload carries final `html`/`text` only — `mail:outbox` and
  `mail:preview` show queued mailables exactly as the worker will send
  them.
- **No request context in mail.** A notification's mailable renders in
  the queue worker, where no request exists to derive a host from.
  Build absolute links from `APP_URL` (or thread the base URL through
  the mailable), never from the enqueueing request — otherwise a
  worker-sent mail carries links for the wrong host.

The envelope builders (`attach` / `add_cc` / `add_bcc` / `set_reply_to`)
work on a `Mailable` exactly as on a `MailMessage`, and a notification's
`to_mail(notifiable)` may return either one (sync or awaitable):

```python
class InvoicePaid(Notification):
    def via(self, notifiable):
        return ["mail"]

    def to_mail(self, notifiable):
        return Mailable(
            subject="Invoice {number} paid",
            html="<p>Invoice {number} was paid.</p>",
            placeholders={"number": self.invoice.number},
        )
```

## Notifications

Subclass `Notification`, declare `via()` plus one `to_<channel>` builder
per channel, and hand it to any notifiable:

```python
from fastplace.notifications import Notification, Notifiable


class OrderShipped(Notification):
    def via(self, notifiable):
        return ["mail", "database"]

    def to_mail(self, notifiable):
        from fastplace.mail import MailMessage

        return MailMessage(
            subject="Order shipped",
            text=f"Order #{notifiable.id} is on its way.",
            to=notifiable.email,
        ).attach(path=f"/tmp/label-{notifiable.id}.pdf")

    def to_database(self, notifiable):
        return {"title": "Order shipped", "order_id": str(notifiable.id)}


# anywhere:
await user.notify(OrderShipped())  # user has .email and .id (Notifiable mixin)
await send([user_a, user_b], OrderShipped())
```

Built-in channels:

- `mail` — delivered through the Mail facade; `MAIL_DRIVER` and the
  queueing rules apply unchanged.
- `database` — rows in a framework-owned `notifications` table
  (created idempotently; app migrations never reference it).
  `NotificationStore().read(...)` lists them, and
  `mark_read(id, notifiable_type=..., notifiable_id=...)` clears the
  unread badge — pass the owner, or a client-supplied id can flip
  someone else's row.

Register more channels — or test fakes — with
`register_channel("name", channel)` where channel is any object with
`async def send(notifiable, notification)`. A channel listed in `via()`
without a matching `to_<channel>` builder raises `NotificationError` at
send time — misrouting fails loudly instead of silently dropping.
