"""Extract the sample app into a self-contained sample scaffold.

The in-repo sample application (app/, routes/, config/, database/,
resources/) doubles as the framework's sample app — this script copies it
out of the monorepo so it can be moved, edited, and run anywhere:

    python scripts/extract_sample.py ~/code/my-fastplace-app

What rides along: source trees, static assets (public/ minus the build
output), .env.example, asgi.py, a standalone frontend toolchain (package.json
/ vite.config.js / index.html — the same contracts `fastplace new` writes),
and a README with the run steps. What never does: secrets (.env and every
.env.* variant), runtime output (storage/, public/build/, __pycache__),
node_modules, and the rest of the monorepo (tests, CI, the framework
itself — the scaffold installs the published fastplace as a dependency).

Stdlib only — the script has to run before any environment exists.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

#: Trees and files the scaffold needs to boot. Everything else in the
#: monorepo is framework or CI surface, not sample.
COPY_TREES = ("app", "routes", "config", "database", "resources", "public")

#: Copied as files, not trees. The repo-root asgi.py is generic (it only
#: imports fastplace.http.create_app), so it works verbatim in the scaffold.
COPY_FILES = (".env.example", "asgi.py")

#: Never extracted, at any depth: runtime output, dependencies, OS junk.
EXCLUDE_DIRS = {"storage", "build", "node_modules", "__pycache__", ".venv", ".git"}

#: Exact-name exclusions (the .env* prefix rule below handles the rest).
EXCLUDE_FILES = {".DS_Store"}

#: Published-package wiring for the scaffold's package.json — the sample
#: installs from the registry, not from a source checkout.
REACT_DEP = "^0.1.0"

# The three standalone frontend files below are the SAME contracts
# `fastplace/cli/generators.py` writes for `fastplace new` — copied verbatim
# (placeholders and all) so the sample toolchain cannot drift silently.
# tests/test_extract_sample.py pins them byte-for-byte against the generator;
# update both sides together. Stdlib-only means we cannot import the
# generator here: the script must run before any environment exists.
_INDEX_HTML_TEMPLATE = """\
<!doctype html>
<html lang="en">
  <head>
    <meta charset="utf-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1" />
    <title>{app_name}</title>
    <!-- fastplace-appearance-prepaint -->
    <script>
      (function () {{
        var mode = "system";
        try {{
          var stored = localStorage.getItem("fastplace-appearance");
          if (stored === "light" || stored === "dark") mode = stored;
        }} catch (e) {{}}
        var dark =
          mode === "dark" ||
          (mode !== "light" && window.matchMedia("(prefers-color-scheme: dark)").matches);
        var root = document.documentElement;
        if (mode === "light" || mode === "dark") root.setAttribute("data-theme", mode);
        else root.removeAttribute("data-theme");
        if (dark) root.classList.add("dark");
        root.style.colorScheme = dark ? "dark" : "light";
      }})();
    </script>
    <style>
      html {{
        background-color: oklch(0.985 0.005 250);
      }}
      html.dark {{
        background-color: oklch(0.19 0.02 262);
      }}
    </style>
    <!-- /fastplace-appearance-prepaint -->
    <!-- Dev-only source of truth; production HTML is rendered by the Python shell. -->
    <script type="module" src="/resources/js/main.jsx"></script>
  </head>
  <body>
    <noscript>
      <div style="margin:24px auto;max-width:560px;padding:20px 24px;border:1px solid #3f3f46;border-radius:12px;background:#18181b;color:#fafafa;font-family:system-ui,sans-serif;font-size:14px;line-height:1.6">
        <p style="margin:0 0 8px;font-weight:600">{app_name} — Home Index</p>
        <p style="margin:0">This page needs JavaScript for the full interface. Forms still submit without it: posting a form reloads the page with the result.</p>
      </div>
    </noscript>
    <div
      id="fastplace"
      data-page='{{"component":"Home/Index","props":{{}},"url":"/","version":"v1"}}'
    ></div>
  </body>
</html>
"""

_PACKAGE_JSON_TEMPLATE = """\
{{
  "name": "{slug}",
  "private": true,
  "version": "0.1.0",
  "type": "module",
  "scripts": {{
    "dev": "vite",
    "build": "vite build",
    "lint": "eslint . --fix",
    "lint:check": "eslint .",
    "format": "prettier --write .",
    "format:check": "prettier --check .",
    "types": "tsc --noEmit",
    "test": "vitest run",
    "test:watch": "vitest"
  }},
  "dependencies": {{
    "@fastplace/react": "{react_dep}",
    "@radix-ui/react-avatar": "^1.2.6",
    "@radix-ui/react-checkbox": "^1.3.11",
    "@radix-ui/react-collapsible": "^1.1.20",
    "@radix-ui/react-dialog": "^1.1.23",
    "@radix-ui/react-dropdown-menu": "^2.1.24",
    "@radix-ui/react-label": "^2.1.15",
    "@radix-ui/react-navigation-menu": "^1.2.22",
    "@radix-ui/react-select": "^2.3.7",
    "@radix-ui/react-separator": "^1.1.15",
    "@radix-ui/react-slot": "^1.3.3",
    "@radix-ui/react-toggle": "^1.1.18",
    "@radix-ui/react-toggle-group": "^1.1.19",
    "@radix-ui/react-tooltip": "^1.2.16",
    "class-variance-authority": "^0.7.1",
    "clsx": "^2.1.1",
    "input-otp": "^1.5.0",
    "lucide-react": "^1.46.0",
    "react": "^19.0.0",
    "react-dom": "^19.0.0",
    "sonner": "^2.0.8",
    "tailwind-merge": "^3.7.0",
    "tw-animate-css": "^1.4.0"
  }},
  "devDependencies": {{
    "@eslint/js": "^9.14.0",
    "@tailwindcss/vite": "^4.0.0",
    "@testing-library/jest-dom": "^6.6.3",
    "@testing-library/react": "^16.1.0",
    "@testing-library/user-event": "^14.5.2",
    "@types/react": "^19.0.0",
    "@types/react-dom": "^19.0.0",
    "@vitejs/plugin-react": "^4.3.4",
    "eslint": "^9.14.0",
    "jsdom": "^25.0.1",
    "prettier": "^3.4.2",
    "tailwindcss": "^4.0.0",
    "typescript": "^5.7.2",
    "typescript-eslint": "^8.70.0",
    "vite": "^6.0.3",
    "vitest": "^2.1.8"
  }}
}}
"""

# NOTE: this template is written verbatim (no .format call) — braces stay single.
_VITE_CONFIG_TEMPLATE = """\
import path from "node:path";
import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import tailwindcss from "@tailwindcss/vite";

// Fastplace Vite contract (blueprint §7):
// - dev: HMR server — the bridge shell references these dev-server URLs
//   directly (no proxy hop; CORS-open while developing)
// - build: hashed assets + manifest.json into public/build/
export default defineConfig({
  plugins: [react(), tailwindcss()],
  // App-tree alias: dev, build, and vitest share this config.
  resolve: {
    alias: {
      "@": path.resolve(__dirname, "resources/js"),
    },
  },
  root: ".",
  publicDir: "public",
  build: {
    outDir: "public/build",
    emptyOutDir: true,
    manifest: true,
    rollupOptions: {
      input: "resources/js/main.jsx",
    },
  },
  server: {
    port: Number(process.env.VITE_PORT || 5173),
    strictPort: true,
    // The dev shell points straight at this server (see fastplace/http/assets.py);
    // no reverse proxy is needed.
    proxy: {},
  },
  test: {
    environment: "jsdom",
    include: [
      "resources/js/**/__tests__/**/*.{test,spec}.{ts,tsx,js,jsx}",
    ],
  },
});
"""

README_TEMPLATE = """# {name} — a Fastplace sample application

Extracted from the Fastplace monorepo's sample app: a complete modular
monolith (Controllers → Services → Repositories → Models) with the React
bridge, a unified `/api/v1` surface, background jobs, and an AI assistant.

## Run it

```bash
cd {name}
python -m venv .venv && source .venv/bin/activate
pip install "fastplace[queue,ai]"   # minimal install: pip install fastplace
cp .env.example .env           # zero-config SQLite to start
npm install
fastplace migrate
fastplace db:seed
fastplace run dev              # http://127.0.0.1:9000
```

## Where things live

- `app/http/controllers/` — controllers (thin: validate, delegate, respond)
- `app/modules/<name>/` — bounded modules: models/, repositories/, services/
- `routes/{{web,api,ai}}.py` — route entry points
- `resources/js/pages/` — React bridge pages (`usePage().props`)
- `database/migrations/` + `database/seeders/` — schema and demo data
- `config/` — app, database, auth, ai configuration

The framework docs live at https://fastplace.dev — they cover getting
started, deployment, and everything in between.
"""


def _is_secret(name: str) -> bool:
    """.env, .env.local, .env.production … every variant except the template."""
    return name.startswith(".env") and name != ".env.example"


def _copy_tree(source: Path, target: Path) -> int:
    """Copy a top-level tree, skipping excluded dirs/files; returns file count."""
    written = 0
    for item in source.rglob("*"):
        if item.is_dir():
            continue
        relative = item.relative_to(source)
        if any(part in EXCLUDE_DIRS for part in relative.parts):
            continue
        if relative.name in EXCLUDE_FILES or _is_secret(relative.name):
            continue
        destination = target / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(item.read_bytes())
        written += 1
    return written


def extract(target: Path, *, name: str | None = None, source: Path | None = None) -> int:
    """Copy the sample scaffold into ``target``; returns the file count written.

    Refuses (exits) when ``target`` is not a clean directory: a file or
    symlink, anything non-empty, or — critically — anywhere inside the
    repository itself. An extractor must never overwrite user work or
    shadow the monorepo it extracts from.

    ``source`` overrides the repo root (tests drive the exclusion logic
    through a controlled fixture tree instead of the real checkout).
    """
    # Type check on the typed path (resolve() would follow a symlink and
    # hide it); the rest of the guards on the resolved one.
    raw = Path(target)
    if raw.is_symlink() or (raw.exists() and not raw.is_dir()):
        sys.exit(f"refusing: {raw} is not a directory")
    target = raw.resolve()
    repo_root = (
        Path(source).resolve() if source is not None else Path(__file__).resolve().parent.parent
    )
    if source is None and (target == repo_root or repo_root in target.parents):
        sys.exit(f"refusing: {target} is inside the repository")
    if target.exists() and any(target.iterdir()):
        sys.exit(f"refusing: {target} exists and is not empty")
    target.mkdir(parents=True, exist_ok=True)

    written = 0
    for tree in COPY_TREES:
        tree_source = repo_root / tree
        if tree_source.is_dir():
            written += _copy_tree(tree_source, target / tree)
    for file_name in COPY_FILES:
        file_source = repo_root / file_name
        if file_source.is_file():
            (target / file_name).write_bytes(file_source.read_bytes())
            written += 1

    # The repo's own frontend wiring is monorepo-flavored (workspaces, tests,
    # vite config for the framework packages) — the scaffold gets the same
    # standalone contracts `fastplace new` writes instead, so `npm install`
    # and `fastplace run dev` behave identically outside the monorepo.
    app_name = name or target.name
    slug = app_name.lower().replace(" ", "-")
    (target / "index.html").write_text(
        _INDEX_HTML_TEMPLATE.format(app_name=app_name), encoding="utf-8"
    )
    (target / "package.json").write_text(
        _PACKAGE_JSON_TEMPLATE.format(slug=slug, react_dep=REACT_DEP), encoding="utf-8"
    )
    (target / "vite.config.js").write_text(_VITE_CONFIG_TEMPLATE, encoding="utf-8")
    written += 3

    (target / "README.md").write_text(README_TEMPLATE.format(name=app_name), encoding="utf-8")
    return written


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("target", type=Path, help="directory to create (must be empty/absent)")
    parser.add_argument(
        "--name", help="app name for the generated README (default: target dir name)"
    )
    args = parser.parse_args(argv)
    written = extract(args.target, name=args.name)
    print(f"extracted {written} files -> {args.target}")
    print("next: cd into it and follow the README (pip install, .env, migrate, run dev)")


if __name__ == "__main__":
    main()
