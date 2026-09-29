"""Static prerendering (SSG) — capture GET routes into static HTML.

Public surface grows across this module's subpackages:

- ``fastplace.prerender.routes`` — which routes to capture;
- ``fastplace.prerender.capture`` — fetching them through the real ASGI stack;
- ``fastplace.prerender.writer`` — writing ``index.html`` trees + manifest.
"""
