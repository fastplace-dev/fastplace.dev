# One-line installers

`install.sh` (macOS + Linux) and `install.ps1` (Windows) are the source of
truth for https://fastplace.dev/install.sh and
https://fastplace.dev/install.ps1.

## What the scripts do

1. Install uv with its official standalone installer (unless already on
   PATH) — everything lands under the user's home, no sudo/admin.
2. `uv tool install --upgrade --python 3.12 fastplace` — uv downloads a
   managed CPython 3.12 and installs the `fastplace` CLI into an isolated
   tool environment. The system Python is never touched.
3. Make sure uv's tool bin dir (`~/.local/bin`, `~\.local\bin`) is on the
   user's PATH — appending one marked line to the shell rc (POSIX) or the
   user PATH variable (Windows), unless told not to.
4. Verify by invoking `fastplace --version` and print next steps.

Only two remote fetches ever happen: uv's installer over HTTPS, and PyPI
via uv. A pre-existing `fastplace` from another source (pip, pipx, a
package manager) is detected and reported, never modified.

## Local development

The behavior tests run the real scripts in a sandbox home with stubbed
`uv`/`curl` — no network, nothing installed:

```bash
pytest tests/installer -v
shellcheck installer/install.sh
shfmt -d installer/install.sh
```

CI (`.github/workflows/ci.yml`) additionally smoke-tests the scripts for
real: fresh debian/ubuntu/fedora/arch containers and macOS/Windows
runners, each ending in a verified `fastplace --version`.

## Release step (required)

These copies must stay byte-identical to what fastplace.dev serves. Any
release that touches `installer/` re-copies both scripts into the
fastplace-docs app before that site is redeployed:

```
cp installer/install.sh   <fastplace-docs>/public/install.sh
cp installer/install.ps1  <fastplace-docs>/public/install.ps1
```

The docs repo's `tests/test_content_contract.py` guards the copies
against silent drift.

## Uninstall

- POSIX: `rm -f ~/.local/bin/fastplace && uv tool uninstall fastplace`,
  then remove the `# fastplace-installer` block from your shell rc.
- Windows: `uv tool uninstall fastplace`, then remove `~\.local\bin`
  from your user PATH.
