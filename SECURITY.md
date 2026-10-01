# Security Policy

Fastplace is in active beta development. Security review is defensive and
welcome — if you found something, report it through the channel below and
we will fix it.

## Supported versions

Fastplace tracks a single supported line during the 0.x beta:

| Version | Supported |
| ------- | --------- |
| latest 0.x minor | Yes — security fixes land in the next patch release |
| anything older | No — upgrade first, then re-report if it still reproduces |

## Reporting a vulnerability

Use GitHub's **private vulnerability reporting** — the "Report a
vulnerability" form at
<https://github.com/fastplace-dev/fastplace.dev/security>.

Please do **not** open a public issue, discussion, or pull request for
anything you suspect is security-relevant. Private reports reach the
maintainers with full advisory tooling: private forks, temporary CVE
reservation, and coordinated disclosure when the fix ships.

Include what helps us move fast:

- the affected component (HTTP bridge, ORM, queue, AI layer, CLI scaffold,
  auth, tenancy package) and the file or route if you know it
- a minimal reproduction — request sequence, payload, or config
- your assessment of impact and any preconditions (auth state, config
  flags, deployed environment)

## What happens next

- Acknowledgement within a few days of the report.
- We validate, fix on the supported line, and ship a patch release.
- Credit in the release notes if you wish; reports can stay anonymous.
- Please give us a reasonable window (we target 90 days, sooner for
  actively exploited classes) before any public disclosure.

## Scope notes

Reports about the framework's own code, the published packages, and the
scaffolded starter kit are in scope. Out of scope: deployment
misconfiguration of apps built with Fastplace, vulnerabilities in
third-party dependencies themselves (report those upstream — and we run
supply-chain audits in CI so known advisories in our resolved trees fail
the build here), and best-effort issues without a reproducible path.
